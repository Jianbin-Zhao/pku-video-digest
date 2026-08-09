#!/usr/bin/env bash
# 准备 CPU 纯本地归纳后端：llama.cpp 的 OpenAI 兼容 server + 小量化模型。
#
# 目的：正面回应「无显卡也能本地跑」。这条路全程不碰 GPU——
# ASR(SenseVoice) 走 CPU、OCR 本就在 CPU、归纳走 llama.cpp CPU 推理。
#
# 独立 venv：llama-cpp-python[server] 会拉 fastapi/uvicorn 等，
# 与主环境(funasr)隔离，避免版本互踩。推理服务通过 HTTP(127.0.0.1:8080)
# 与流水线解耦。
#
# 模型选 Qwen2.5-3B-Instruct（非思考模型，省去关思考的麻烦；3B 在 12 vCPU 上
# 出 ~1K token 摘要约几十秒，是 CPU 上可接受的档位）。GGUF Q4_K_M ~2GB。
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
