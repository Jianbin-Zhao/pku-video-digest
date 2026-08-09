#!/usr/bin/env bash
# 干净重启本地 vLLM 服务。
#
# 为什么单独写：vLLM 的 EngineCore 是子进程，setproctitle 把它改名成
# "VLLM::EngineCore"，用 `pkill -f api_server` 杀不到它，它会继续占着显存，
# 导致下一次启动因显存不足失败。这里统一按 venv 路径匹配，父子进程一起清。
# 用括号写法 'vllm[-]venv' 避免 pkill 的 -f 把这条命令自己也匹配掉。
set -uo pipefail

LOG=/root/autodl-tmp/vllm_serve.log

pkill -9 -f 'vllm[-]venv' 2>/dev/null || true
sleep 3

cd /root/vspider || exit 1
: > "${LOG}"
setsid bash scripts/vllm_serve.sh >> "${LOG}" 2>&1 < /dev/null &
disown
sleep 6
echo "[vllm_restart] 已启动，日志 ${LOG}"
head -n 3 "${LOG}"
