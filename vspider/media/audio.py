"""音视频处理：抽音频、探时长。全部通过 ffmpeg 子进程完成。"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path


class FFmpegError(RuntimeError):
    pass


def ffmpeg_path() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise FFmpegError("找不到 ffmpeg，请先安装（apt install ffmpeg）")
    return path


def ffprobe_path() -> str:
    path = shutil.which("ffprobe")
    if not path:
        raise FFmpegError("找不到 ffprobe，通常随 ffmpeg 一起安装")
    return path


async def _run(args: list[str]) -> bytes:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise FFmpegError(
            f"{Path(args[0]).name} 失败 (exit={process.returncode}): "
            f"{stderr.decode('utf-8', 'replace')[-500:]}"
        )
    return stdout


async def extract_audio(
    source: Path,
    dest: Path,
    sample_rate: int = 16000,
    channels: int = 1,
) -> Path:
    """把音轨抽成 wav。

    16k 单声道是 SenseVoice / Paraformer 这一系中文 ASR 模型的标准输入，
    在这里一次转换到位，避免模型内部再做一次重采样。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    await _run(
        [
            ffmpeg_path(),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-vn",
            "-ac",
            str(channels),
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
            str(dest),
        ]
    )
    if not dest.exists() or dest.stat().st_size == 0:
        raise FFmpegError(f"{source.name} 抽出的音频为空，可能是无音轨视频")
    return dest


async def probe_duration(source: Path) -> float:
    """返回媒体时长（秒）。"""
    stdout = await _run(
        [
            ffprobe_path(),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(source),
        ]
    )
    try:
        return float(json.loads(stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise FFmpegError(f"无法解析 {source.name} 的时长: {exc}") from exc


async def has_audio_stream(source: Path) -> bool:
    """判断是否存在音轨。

    小红书和抖音上有相当比例的纯图文视频或纯 BGM 视频，
    提前判断可以直接跳过 ASR，把这条视频交给 OCR 和视觉理解处理。
    """
    stdout = await _run(
        [
            ffprobe_path(),
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "json",
            str(source),
        ]
    )
    try:
        return bool(json.loads(stdout).get("streams"))
    except json.JSONDecodeError:
        return False
