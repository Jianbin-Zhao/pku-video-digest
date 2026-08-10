#!/usr/bin/env bash
# 初始化 GPU 服务器环境。

set -uo pipefail

WORK=/root/autodl-tmp
export MODELS=$WORK/models
LOG=$WORK/setup.log

mkdir -p "$MODELS" "$WORK/data" "$WORK/logs"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
stage() { echo "" | tee -a "$LOG"; log "===== STAGE: $* ====="; }

export HF_HOME=$WORK/.cache/huggingface
export MODELSCOPE_CACHE=$WORK/.cache/modelscope
export PIP_CACHE_DIR=$WORK/.cache/pip
export HF_ENDPOINT=https://hf-mirror.com

unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY

stage "conda base python"
for candidate in /root/miniconda3 /opt/conda /root/anaconda3; do
    if [ -f "$candidate/etc/profile.d/conda.sh" ]; then
        # shellcheck disable=SC1091
        . "$candidate/etc/profile.d/conda.sh"
        conda activate base
        break
    fi
done
PY=$(command -v python || command -v python3)
if [ -z "$PY" ]; then
    log "FATAL 找不到 python"
    exit 1
fi
log "python = $PY ($($PY --version 2>&1))"
$PY -c "import torch; print('torch', torch.__version__, 'cuda', torch.version.cuda, torch.cuda.get_device_name(0))" 2>&1 | tee -a "$LOG"

stage "ffmpeg"
if ! command -v ffmpeg >/dev/null 2>&1; then
    apt-get update -qq >>"$LOG" 2>&1
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ffmpeg >>"$LOG" 2>&1
fi
ffmpeg -version 2>&1 | head -1 | tee -a "$LOG"

stage "python deps (直连阿里云 pypi 镜像)"
$PY -m pip install -q --upgrade pip >>"$LOG" 2>&1
$PY -m pip install -q \
    funasr modelscope \
    rapidocr-onnxruntime \
    yt-dlp \
    httpx pydantic pyyaml typer rich tenacity python-dateutil \
    fastapi uvicorn sse-starlette \
    sqlalchemy aiosqlite greenlet \
    openai \
    >>"$LOG" 2>&1
log "pip exit=$?"
$PY -c "import funasr, rapidocr_onnxruntime, yt_dlp, openai; print('deps ok')" 2>&1 | tee -a "$LOG"

stage "download models (魔搭直连)"
$PY - <<'PYEOF' 2>&1 | tee -a "$LOG"
import os
import time

from modelscope import snapshot_download

root = os.environ["MODELS"]
# SenseVoiceSmall: 非自回归中文 ASR，官方 benchmark CPU 上 17.2x 实时、CER 7.81%，
#   放到 GPU 上更快。这是整条流水线里语音转文字的主力。
# fsmn-vad: 语音活动检测。长音频必须先切分，否则 SenseVoice 单次前向的
#   显存占用会随时长线性膨胀，十几分钟的视频直接 OOM。
targets = [
    ("iic/SenseVoiceSmall", "SenseVoiceSmall"),
    ("iic/speech_fsmn_vad_zh-cn-16k-common-pytorch", "fsmn-vad"),
]
for model_id, folder in targets:
    started = time.time()
    path = snapshot_download(model_id, local_dir=os.path.join(root, folder))
    print(f"OK {model_id} -> {path} ({time.time() - started:.0f}s)")
PYEOF

stage "summary"
du -sh "$MODELS"/* 2>/dev/null | tee -a "$LOG"
df -h "$WORK" | tail -1 | tee -a "$LOG"
log "SETUP_DONE"
