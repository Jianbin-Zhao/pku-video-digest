#!/usr/bin/env bash
# 启动 FastAPI Web。
set -euo pipefail

cd "$(dirname "$0")/.."
PORT="${1:-6006}"

pkill -9 -f "uvicorn.*vspider[.]web" 2>/dev/null || true
sleep 1

echo "[serve_web] http://0.0.0.0:${PORT}  (Ctrl-C 退出)"
exec /root/miniconda3/bin/python -m uvicorn vspider.web.server:app \
  --host 0.0.0.0 --port "${PORT}" --no-access-log
