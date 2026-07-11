#!/usr/bin/env bash
# Manage the local LangGraph production runner as a background process.
#
# Usage:
#   scripts/langgraph_daemon.sh start [extra run_langgraph_batch.py args...]
#   scripts/langgraph_daemon.sh stop
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
  echo "Usage: $0 {start|stop|restart|status|logs} [extra run args...]"
}

# Return success when the pid file points to a live process.
is_running() {
  [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

# Start LangGraph in the background and redirect all console output to a log file.
start() {
  mkdir -p "$(dirname "$LOG_FILE")" "$(dirname "$PID_FILE")"
  if is_running; then
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
  if ! is_running; then
    rm -f "$PID_FILE"
    echo "LangGraph is not running"
    return 0
  fi
  pid="$(cat "$PID_FILE")"
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
  echo "Still running after 30s; check current article work before forcing stop."
}

# Print whether the background runner is currently alive.
status() {
  if is_running; then
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
