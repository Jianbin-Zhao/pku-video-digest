#!/usr/bin/env bash
# 重启 vLLM 服务。
set -uo pipefail

LOG=/root/autodl-tmp/vllm_serve.log
PIDFILE=/root/autodl-tmp/vllm_serve.pid

if [[ -s "${PIDFILE}" ]]; then
  OLD_PID=$(cat "${PIDFILE}")
  kill -TERM -- "-${OLD_PID}" 2>/dev/null || true
  sleep 2
  kill -KILL -- "-${OLD_PID}" 2>/dev/null || true
fi
# 清理旧版未记录 pid 的进程。
pkill -9 -f 'vllm[.]entrypoints[.]openai[.]api_server' 2>/dev/null || true
pkill -9 -f 'VLLM::Engine[C]ore' 2>/dev/null || true
sleep 3

cd /root/vspider || exit 1
: > "${LOG}"
setsid bash scripts/vllm_serve.sh >> "${LOG}" 2>&1 < /dev/null &
NEW_PID=$!
echo "${NEW_PID}" > "${PIDFILE}"
disown
sleep 6
echo "[vllm_restart] 已启动 pid=${NEW_PID}，日志 ${LOG}"
head -n 3 "${LOG}"
