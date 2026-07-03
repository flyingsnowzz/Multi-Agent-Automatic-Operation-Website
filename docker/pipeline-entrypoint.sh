#!/bin/sh
set -eu

wait_tcp() {
  name="$1"
  host="$2"
  port="$3"
  timeout="${4:-90}"

  python3 - "$name" "$host" "$port" "$timeout" <<'PY'
import socket
import sys
import time

name, host, port, timeout = sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4])
deadline = time.time() + timeout
last_error = None

while time.time() < deadline:
    try:
        with socket.create_connection((host, port), timeout=3):
            print(f"[entrypoint] {name} ready at {host}:{port}", flush=True)
            raise SystemExit(0)
    except OSError as exc:
        last_error = exc
        time.sleep(2)

print(f"[entrypoint] timeout waiting for {name} at {host}:{port}: {last_error}", file=sys.stderr, flush=True)
raise SystemExit(1)
PY
}

if [ "${WAIT_FOR_REDIS:-true}" = "true" ]; then
  wait_tcp redis "${REDIS_HOST:-redis}" "${REDIS_PORT:-6379}" "${WAIT_TIMEOUT_SECONDS:-90}"
fi

if [ "${WAIT_FOR_MYSQL:-true}" = "true" ]; then
  wait_tcp mysql "${MYSQL_HOST:-mysql}" "${MYSQL_PORT:-3306}" "${WAIT_TIMEOUT_SECONDS:-90}"
fi

exec "$@"
