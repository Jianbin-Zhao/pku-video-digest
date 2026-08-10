#!/usr/bin/env bash
# 安装 llama.cpp CPU 归纳后端。
set -euo pipefail

VENV=/root/autodl-tmp/llama-venv
PY=/root/miniconda3/bin/python
MODEL_DIR=/root/autodl-tmp/models/Qwen2.5-3B-Instruct-GGUF

if [ ! -x "${VENV}/bin/python" ]; then
  echo "[cpu_setup] 建 venv ${VENV}"
  "${PY}" -m venv "${VENV}"
fi

# shellcheck disable=SC1091
source "${VENV}/bin/activate"
python -V

# 预编译 wheel 挂在 abetlen.github.io 上，境内机器拉不动（实测 pip 卡死 8 分钟）。
# 服务器有 gcc 11.4 + cmake，直接走阿里云镜像的源码包现场编译，12 vCPU 几分钟搞定。
echo "[cpu_setup] 安装 llama-cpp-python[server]（源码编译，CPU 后端）"
pip install -q --upgrade pip
export CMAKE_BUILD_PARALLEL_LEVEL=12
pip install "llama-cpp-python[server]" 2>&1 | tail -n 5

python -c "import llama_cpp; print('llama_cpp', llama_cpp.__version__)"

echo "[cpu_setup] 下载 Qwen2.5-3B-Instruct GGUF (Q4_K_M) 到 ${MODEL_DIR}"
if ! ls "${MODEL_DIR}"/*q4_k_m*.gguf >/dev/null 2>&1; then
  "${PY}" - <<'PY'
from modelscope import snapshot_download
p = snapshot_download(
    "Qwen/Qwen2.5-3B-Instruct-GGUF",
    allow_patterns=["*q4_k_m*.gguf"],
    local_dir="/root/autodl-tmp/models/Qwen2.5-3B-Instruct-GGUF",
)
print("downloaded to", p)
PY
else
  echo "[cpu_setup] GGUF 已存在，跳过"
fi

echo "[cpu_setup] 完成。GGUF 文件："
ls -lh "${MODEL_DIR}"/*.gguf
