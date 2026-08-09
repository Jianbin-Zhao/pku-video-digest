#!/usr/bin/env bash
# 启动 CPU 纯本地归纳后端：llama-cpp-python 的 OpenAI 兼容 server。
#
# 与 registry.py 的 cpu 档约定对齐：监听 127.0.0.1:8080，模型别名 "local"。
# 全程 CPU 推理，不占用任何显存——这是「无显卡也能跑」的验证路径。
#
# 参数说明：
#   --n_ctx 16384     与 gpu 档一致，长视频转写 + OCR 拼进 prompt 也够用
#   --n_threads 12    机器是 12 vCPU，吃满
#   --chat_format 留空 → 自动用 GGUF 内嵌的 Qwen2.5 chat template
set -euo pipefail

VENV=/root/autodl-tmp/llama-venv
MODEL=$(ls /root/autodl-tmp/models/Qwen2.5-3B-Instruct-GGUF/*q4_k_m*.gguf | head -n 1)
LOG=/root/autodl-tmp/llama_server.log

echo "[cpu_serve] 模型: ${MODEL}"

# 清掉旧实例（用 [.] 技巧避免 pkill 匹配到自身命令行）
pkill -9 -f "llama[-]venv" 2>/dev/null || true
sleep 1

source "${VENV}/bin/activate"
nohup python -m llama_cpp.server \
  --model "${MODEL}" \
  --model_alias local \
  --host 127.0.0.1 \
  --port 8080 \
  --n_ctx 16384 \
  --n_threads 12 \
  > "${LOG}" 2>&1 &

echo "[cpu_serve] 已后台启动，pid=$!，日志: ${LOG}"
echo "[cpu_serve] 等待就绪..."
for i in $(seq 1 60); do
  if curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/v1/models | grep -q 200; then
    echo "[cpu_serve] server 就绪"
    curl -s http://127.0.0.1:8080/v1/models
    echo
    exit 0
  fi
  sleep 2
done
echo "[cpu_serve] 60 次轮询内未就绪，日志尾部："
tail -n 30 "${LOG}"
exit 1
