#!/usr/bin/env bash
# 启动 Web 界面（FastAPI + SSE 实时流水线）。
#
# 跑在 conda base 环境：它已装齐 funasr / rapidocr / torch（流水线要用）
# 以及 fastapi / uvicorn。绑定 0.0.0.0:6006，对齐 AutoDL 的「自定义服务」端口，
# 也可在本机用 SSH 隧道访问：
#   ssh -p <port> -L 6006:127.0.0.1:6006 root@<host>
# 然后浏览器打开 http://127.0.0.1:6006
#
# 归纳后端按需自选：api 需 .env 里的 DASHSCOPE_API_KEY；
# gpu 需先 bash scripts/vllm_restart.sh；cpu 需先 bash scripts/cpu_serve.sh。
set -euo pipefail

cd "$(dirname "$0")/.."
PORT="${1:-6006}"

# 杀掉同端口旧实例（[.] 技巧避免 pkill 匹配到自身命令行）
pkill -9 -f "uvicorn.*vspider[.]web" 2>/dev/null || true
sleep 1

echo "[serve_web] http://0.0.0.0:${PORT}  (Ctrl-C 退出)"
exec /root/miniconda3/bin/python -m uvicorn vspider.web.server:app \
  --host 0.0.0.0 --port "${PORT}" --no-access-log
