#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 python3，请先安装 Python 3.11+"
  exit 1
fi

PY_VER="$(
python3 - <<'PY'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
PY
)"

PY_MAJOR="$(echo "$PY_VER" | cut -d. -f1)"
PY_MINOR="$(echo "$PY_VER" | cut -d. -f2)"

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
  echo "Python 版本过低：$PY_VER，需要 3.11+"
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "创建虚拟环境 .venv (Python $PY_VER)"
  python3 -m venv .venv
fi

if [ ! -f ".venv/bin/activate" ]; then
  echo "虚拟环境异常：.venv/bin/activate 不存在"
  exit 1
fi

source ".venv/bin/activate"

if [ ! -f "requirements.txt" ]; then
  echo "缺少 requirements.txt"
  exit 1
fi

python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt

if [ ! -f ".env" ] && [ -f ".env.example" ]; then
  cp .env.example .env
  echo "已生成 .env（请编辑并填入密钥与连接信息）"
fi

exec python scripts/run_redis_workers.py --feed --dry-run "$@"
