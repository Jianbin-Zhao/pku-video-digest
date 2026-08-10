#!/usr/bin/env bash
# 安装 vLLM 与 Qwen3-8B-AWQ。
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
