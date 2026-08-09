#!/usr/bin/env bash
# 下载 ASR 所需模型权重。
#
# 为什么不直接用 modelscope 的 snapshot_download：
# 它走单连接，实测在这台机器上只有 400~750 kB/s，且在 97% 处断连后
# 不能可靠续传、直接从头重下。改用 aria2c 开 16 连接后实测 12 MiB/s，
# 快约 30 倍，且断点续传可靠。
#
# 小文件（配置、词表）仍交给 modelscope，量小且省去手工维护文件清单。

set -uo pipefail

WORK=/root/autodl-tmp
MODELS=$WORK/models
LOG=$WORK/fetch_models.log

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

for candidate in /root/miniconda3 /opt/conda /root/anaconda3; do
    if [ -f "$candidate/etc/profile.d/conda.sh" ]; then
        # shellcheck disable=SC1091
        . "$candidate/etc/profile.d/conda.sh"
        conda activate base
        break
    fi
done
PY=$(command -v python || command -v python3)

unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
export MODELSCOPE_CACHE=$WORK/.cache/modelscope

# aria2c 参数说明：-x16 单服务器最多 16 连接，-s16 文件切 16 段，
# -k1M 每段最小 1M，-c 断点续传，--retry-wait 失败后退避重试。
aria() {
    aria2c -x16 -s16 -k1M -c \
        --max-tries=8 --retry-wait=3 --timeout=30 \
        --console-log-level=warn --summary-interval=10 \
        --allow-overwrite=false --auto-file-renaming=false \
        "$@"
}

fetch_big() {
    local url=$1 dir=$2 name=$3 expect=$4
    mkdir -p "$dir"
    if [ -f "$dir/$name" ]; then
        local size
        size=$(stat -c%s "$dir/$name")
        if [ "$size" -eq "$expect" ]; then
            log "SKIP $name 已完整 ($size bytes)"
            return 0
        fi
        log "WARN $name 大小 $size != 期望 $expect，重新下载"
        rm -f "$dir/$name"
    fi
    log "GET $name"
    aria -d "$dir" -o "$name" "$url" 2>&1 | tail -4 | tee -a "$LOG"
    local size
    size=$(stat -c%s "$dir/$name" 2>/dev/null || echo 0)
    if [ "$size" -ne "$expect" ]; then
        log "FAIL $name 下载后大小 $size != 期望 $expect"
        return 1
    fi
    log "OK $name ($size bytes)"
}

log "===== SenseVoiceSmall/model.pt (16 连接) ====="
rm -f "$MODELS/SenseVoiceSmall/model.pt.incomplete" "$MODELS/SenseVoiceSmall/test.part"*
fetch_big \
    "https://modelscope.cn/models/iic/SenseVoiceSmall/resolve/master/model.pt" \
    "$MODELS/SenseVoiceSmall" "model.pt" 936291369

log "===== 补齐小文件（配置 / 词表）====="
$PY - <<'PYEOF' 2>&1 | tee -a "$LOG"
import os

from modelscope import snapshot_download

root = os.environ["HOME"] + "/autodl-tmp/models"
for model_id, folder in [
    ("iic/SenseVoiceSmall", "SenseVoiceSmall"),
    ("iic/speech_fsmn_vad_zh-cn-16k-common-pytorch", "fsmn-vad"),
]:
    path = snapshot_download(model_id, local_dir=os.path.join(root, folder))
    print(f"OK {model_id} -> {path}")
PYEOF

log "===== 结果 ====="
du -sh "$MODELS"/* 2>/dev/null | tee -a "$LOG"
ls -l "$MODELS/SenseVoiceSmall/model.pt" 2>/dev/null | tee -a "$LOG"
df -h "$WORK" | tail -1 | tee -a "$LOG"
log "FETCH_DONE"
