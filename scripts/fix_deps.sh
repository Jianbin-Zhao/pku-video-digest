#!/usr/bin/env bash
# 补齐 funasr 运行时缺失的依赖。
#
# funasr 的 utils/load_utils.py 顶层就 import torchaudio，绕不过去。
# 装它时必须加 --no-deps：否则 pip 会按 torchaudio 的依赖声明
# 把镜像自带的 torch 2.8.0+cu128 替换成 PyPI 上的其他 CUDA 构建，
# 白下 800MB 还可能弄坏 CUDA 环境。
# torchaudio 主要通过 torch 的算子工作，同主次版本号即可正常协作。

set -uo pipefail

for candidate in /root/miniconda3 /opt/conda /root/anaconda3; do
    if [ -f "$candidate/etc/profile.d/conda.sh" ]; then
        # shellcheck disable=SC1091
        . "$candidate/etc/profile.d/conda.sh"
        conda activate base
        break
    fi
done
PY=$(command -v python)

unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY

TORCH_VERSION=$($PY -c "import torch; print(torch.__version__.split('+')[0])")
echo "installed torch = $TORCH_VERSION"

echo "--- install torchaudio==$TORCH_VERSION (no-deps) ---"
$PY -m pip install -q --no-deps "torchaudio==$TORCH_VERSION" 2>&1 | tail -3

echo "--- install soundfile / librosa deps ---"
$PY -m pip install -q soundfile librosa 2>&1 | tail -3

echo "--- verify ---"
$PY - <<'PYEOF'
import torch

print("torch      ", torch.__version__, "cuda", torch.cuda.is_available())
try:
    import torchaudio

    print("torchaudio ", torchaudio.__version__)
except Exception as exc:
    print("torchaudio  FAILED:", type(exc).__name__, exc)
try:
    import soundfile

    print("soundfile  ", soundfile.__version__)
except Exception as exc:
    print("soundfile   FAILED:", type(exc).__name__, exc)
try:
    from funasr import AutoModel

    print("funasr      import ok")
except Exception as exc:
    print("funasr      FAILED:", type(exc).__name__, exc)
try:
    from rapidocr_onnxruntime import RapidOCR

    print("rapidocr    import ok")
except Exception as exc:
    print("rapidocr    FAILED:", type(exc).__name__, exc)
PYEOF
