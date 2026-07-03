#!/bin/bash
set -e
cd "$(dirname "$0")/.."

if command -v redis-server >/dev/null 2>&1; then
    redis-server --daemonize yes 2>/dev/null || true
fi

echo "[$(date '+%H:%M:%S')] 🚀 Auto Pipeline 启动"
exec python3 scripts/run_redis_workers.py --feed --dry-run "$@"
