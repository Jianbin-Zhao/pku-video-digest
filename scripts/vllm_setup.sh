#!/usr/bin/env bash
# 在服务器上准备本地 GPU 归纳后端：独立 venv + vLLM + Qwen3-8B-AWQ。
#
# 为什么单独建环境：vLLM 会钉死一套 torch，直接装进主环境会把 funasr 依赖的
# torch 2.8.0+cu128 换掉，连带弄坏 torchaudio 与 CUDA 匹配。推理服务通过
# HTTP(127.0.0.1:8000) 与流水线解耦，各用各的环境最干净。
#
# 为什么用 venv 而不是 conda：conda 在本机镜像上 solve repodata 会卡十几分钟，
# venv 由 base python 直接建、pip 走已配好的阿里云镜像，快且省事。装在数据盘
# 上（系统盘只有 30G，vLLM+torch 约 6G 放不下）。
set -euo pipefail

VENV=/root/autodl-tmp/vllm-venv
MODEL_DIR=/root/autodl-tmp/models/Qwen3-8B-AWQ
PY=/root/miniconda3/bin/python

if [ ! -x "${VENV}/bin/python" ]; then
  echo "[vllm_setup] 建 venv ${VENV}"
  "${PY}" -m venv "${VENV}"
fi

# shellcheck disable=SC1091
source "${VENV}/bin/activate"
python -V

echo "[vllm_setup] 升级 pip"
pip install -q --upgrade pip

echo "[vllm_setup] 安装 vLLM + modelscope（阿里云镜像）"
pip install "vllm>=0.8.5" modelscope 2>&1 | tail -n 3

echo "[vllm_setup] vLLM 版本："
python -c "import vllm; print(vllm.__version__)"

echo "[vllm_setup] 下载 Qwen3-8B-AWQ 到 ${MODEL_DIR}"
if [ ! -f "${MODEL_DIR}/config.json" ]; then
  python - <<'PY'
from modelscope import snapshot_download
p = snapshot_download("Qwen/Qwen3-8B-AWQ",
                      local_dir="/root/autodl-tmp/models/Qwen3-8B-AWQ")
print("downloaded to", p)
PY
else
  echo "[vllm_setup] 模型已存在，跳过下载"
fi

echo "[vllm_setup] 完成。权重大小："
du -sh "${MODEL_DIR}" || true
