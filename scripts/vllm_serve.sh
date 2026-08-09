#!/usr/bin/env bash
# 启动本地 vLLM 归纳服务（OpenAI 兼容，127.0.0.1:8000）。
#
# 显存预算：3080 Ti 只有 12 GB。实测 vLLM 0.26 的固定占用约 6.1G
# (权重 5.71 + 峰值激活 0.19 + 非 torch 0.08 + CUDAGraph 0.09)。
# 试过让 LLM 与 ASR 同卡共存(util 0.70)，但 KV 只剩约 1.17G，12K 上下文放不下、
# 8K 也只是勉强够(razor-thin，运行期并发一上就可能 OOM)——对「稳定」不利。
# 故定为：LLM 独占 GPU(util 0.85≈9.9G，KV 3.92G 充裕、16K 上下文不截断)，
# ASR 让给 CPU(SenseVoice CPU 约 17× 实时，够用)。两者仍全在本地。
# 换 ≥16G 显卡时可把 ASR 拉回 GPU，两者同卡即得完整 GPU 速度。
set -euo pipefail

VENV=/root/autodl-tmp/vllm-venv
MODEL_DIR=/root/autodl-tmp/models/Qwen3-8B-AWQ

# shellcheck disable=SC1091
source "${VENV}/bin/activate"

# 业务不走代理，推理服务是本地回环，显式清掉以免继承到奇怪的出口。
unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY || true

exec python -m vllm.entrypoints.openai.api_server \
  --model "${MODEL_DIR}" \
  --served-model-name Qwen3-8B \
  --host 127.0.0.1 --port 8000 \
  --quantization awq_marlin \
  --max-model-len 16384 \
  --gpu-memory-utilization 0.85 \
  --max-num-seqs 8 \
  --enable-prefix-caching \
  --no-enable-log-requests
