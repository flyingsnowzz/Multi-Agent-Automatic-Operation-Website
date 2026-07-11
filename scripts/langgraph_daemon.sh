#!/usr/bin/env bash
# Manage the local LangGraph production runner as a background process.
#
# Usage:
#   scripts/langgraph_daemon.sh start [extra run_langgraph_batch.py args...]
#   scripts/langgraph_daemon.sh stop
#   scripts/langgraph_daemon.sh force-stop
#   scripts/langgraph_daemon.sh status
#   scripts/langgraph_daemon.sh logs

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Load local .env values so LANGGRAPH_RUN_LOG, LANGGRAPH_PID_FILE, and provider
# credentials work the same way whether the runner is started foreground or
# background.
if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
LOG_FILE="${LANGGRAPH_RUN_LOG:-$ROOT_DIR/logs/langgraph_batch.log}"
PID_FILE="${LANGGRAPH_PID_FILE:-$ROOT_DIR/output/langgraph_batch.pid}"
[[ "$LOG_FILE" = /* ]] || LOG_FILE="$ROOT_DIR/$LOG_FILE"
[[ "$PID_FILE" = /* ]] || PID_FILE="$ROOT_DIR/$PID_FILE"

# Print a short usage message for unsupported commands.
usage() {
  echo "Usage: $0 {start|stop|force-stop|restart|status|logs} [extra run args...]"
}

# Return the live runner pid. The pid file is the primary source, but a runner
# can survive if the pid file is removed by hand or by an older script version.
find_running_pid() {
  local pid
  if [[ -f "$PID_FILE" ]]; then
    pid="$(tr -d '[:space:]' <"$PID_FILE")"
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
      echo "$pid"
      return 0
    fi
  fi
  pgrep -f "$PYTHON_BIN -u scripts/run_langgraph_batch.py --production" | head -n 1 || true
}

# Return success when a live LangGraph runner exists.
is_running() {
  [[ -n "$(find_running_pid)" ]]
}

# Start LangGraph in the background and redirect all console output to a log file.
start() {
  mkdir -p "$(dirname "$LOG_FILE")" "$(dirname "$PID_FILE")"
  if is_running; then
    find_running_pid >"$PID_FILE"
    echo "LangGraph already running pid=$(cat "$PID_FILE")"
    echo "Log: $LOG_FILE"
    return 0
  fi
  if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "Python not found or not executable: $PYTHON_BIN" >&2
    echo "Run: python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt" >&2
    return 1
  fi
  cd "$ROOT_DIR"
  nohup "$PYTHON_BIN" -u scripts/run_langgraph_batch.py --production "$@" >>"$LOG_FILE" 2>&1 &
  echo "$!" >"$PID_FILE"
  echo "LangGraph started pid=$(cat "$PID_FILE")"
  echo "Log: $LOG_FILE"
}

# Stop the background runner gracefully, then remove the stale pid file.
stop() {
  pid="$(find_running_pid)"
  if [[ -z "$pid" ]]; then
    rm -f "$PID_FILE"
    echo "LangGraph is not running"
    return 0
  fi
  echo "$pid" >"$PID_FILE"
  kill "$pid"
  echo "Stopping LangGraph pid=$pid"
  for _ in $(seq 1 30); do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$PID_FILE"
      echo "Stopped"
      return 0
    fi
    sleep 1
  done
  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$PID_FILE"
    echo "Stopped"
    return 0
  fi
  echo "Still running after 30s; check current article work before forcing stop."
}

# Kill the background runner immediately and remove any stale pid file.
force_stop() {
  pid="$(find_running_pid)"
  if [[ -z "$pid" ]]; then
    rm -f "$PID_FILE"
    echo "LangGraph is not running"
    return 0
  fi
  echo "$pid" >"$PID_FILE"
  echo "Force stopping LangGraph pid=$pid"
  kill -TERM "$pid" 2>/dev/null || true
  sleep 2
  if kill -0 "$pid" 2>/dev/null; then
    kill -KILL "$pid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
  echo "Force stopped"
}

# Print whether the background runner is currently alive.
status() {
  pid="$(find_running_pid)"
  if [[ -n "$pid" ]]; then
    echo "$pid" >"$PID_FILE"
    echo "LangGraph running pid=$(cat "$PID_FILE")"
    echo "Log: $LOG_FILE"
  else
    rm -f "$PID_FILE"
    echo "LangGraph is not running"
  fi
}

# Follow the redirected runtime log.
follow_logs() {
  mkdir -p "$(dirname "$LOG_FILE")"
  touch "$LOG_FILE"
  tail -f "$LOG_FILE"
}

case "${1:-}" in
  start)
    shift
    start "$@"
    ;;
  stop)
    stop
    ;;
  force-stop)
    force_stop
    ;;
  restart)
    shift
    stop
    start "$@"
    ;;
  status)
    status
    ;;
  logs)
    follow_logs
    ;;
  *)
    usage
    exit 2
    ;;
esac
