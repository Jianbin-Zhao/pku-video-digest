#!/usr/bin/env bash
# 启动 Qwen3-8B vLLM 服务。
set -euo pipefail

VENV=/root/autodl-tmp/vllm-venv
MODEL_DIR=/root/autodl-tmp/models/Qwen3-8B-AWQ
TOTAL_MIB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -n 1)
if (( TOTAL_MIB >= 20000 )); then
  DEFAULT_GPU_UTIL=0.75
else
  DEFAULT_GPU_UTIL=0.85
fi
GPU_UTIL="${VSPIDER_VLLM_GPU_MEMORY_UTILIZATION:-${DEFAULT_GPU_UTIL}}"

# shellcheck disable=SC1091
source "${VENV}/bin/activate"

unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY || true

echo "[vllm_serve] GPU ${TOTAL_MIB} MiB, vLLM memory utilization ${GPU_UTIL}"
exec python -m vllm.entrypoints.openai.api_server \
  --model "${MODEL_DIR}" \
  --served-model-name Qwen3-8B \
  --host 127.0.0.1 --port 8000 \
  --quantization awq_marlin \
  --max-model-len 16384 \
  --gpu-memory-utilization "${GPU_UTIL}" \
  --max-num-seqs 8 \
  --enable-prefix-caching \
  --no-enable-log-requests
