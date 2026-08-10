"""I帧、场景变化和固定间隔抽帧。"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

from vspider.media.audio import (
    FFmpegError,
    ffmpeg_path,
    ffprobe_path,
    probe_duration,
)

_EXTRACT_CONCURRENCY = 6


@dataclass
class Keyframe:
    timestamp: float
    path: Path


async def extract_keyframes(
    video_path: Path,
    dest_dir: Path,
    scene_threshold: float = 0.30,
    max_frames: int = 24,
    min_frames: int = 6,
    width: int = 960,
    strategy: str = "iframe",
) -> list[Keyframe]:
    """抽关键帧，返回按时间排序的结果。

    Args:
        scene_threshold: 画面变化阈值，仅 scene 策略使用。0.3 是经验值，
            再低会把镜头内的运动误判成切变，再高会漏掉相似构图的换镜。
        max_frames: 上限。一条短视频抽二十几帧足够覆盖硬字幕，
            再多只是让 OCR 更慢。
        min_frames: 低于此数量则触发均匀采样兜底。
        width: 缩放宽度。OCR 对 960 宽度已经足够，
            原始分辨率只会拖慢识别且无精度收益。
        strategy: iframe / scene / interval。
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    _clear(dest_dir)

    duration = await probe_duration(video_path)

    if strategy == "iframe":
        timestamps = await _probe_iframe_times(video_path)
        # 均匀覆盖整条视频。
        frames = await _extract_at_times(
            video_path, dest_dir, _spread(timestamps, max_frames), width
        )
    elif strategy == "scene":
        frames = await _extract_by_scene(
            video_path, dest_dir, scene_threshold, max_frames, width
        )
    elif strategy == "interval":
        frames = []
    else:
        raise ValueError(f"未知抽帧策略 {strategy!r}，可选 iframe / scene / interval")

    if len(frames) < min_frames and duration > 0:
        # 帧数不足时改用均匀采样。
        _clear(dest_dir)
        target = max(min_frames, min(max_frames, int(duration // 10) + min_frames))
        frames = await _extract_at_times(
            video_path, dest_dir, _interval_times(duration, target), width
        )

    return frames


def _clear(dest_dir: Path) -> None:
    for stale in dest_dir.glob("frame_*.jpg"):
        stale.unlink()


def _spread(values: list[float], count: int) -> list[float]:
    """从有序序列里均匀取 count 个，保证首尾都在内。"""
    if len(values) <= count:
        return values
    step = (len(values) - 1) / (count - 1) if count > 1 else 0
    return [values[round(index * step)] for index in range(count)]


def _interval_times(duration: float, target: int) -> list[float]:
    # 跳过常见的片头和关注引导。
    span_start = duration * 0.02
    span_end = duration * 0.98
    step = max((span_end - span_start) / target, 0.5)
    times = [span_start + index * step for index in range(target)]
    return [t for t in times if t < duration]


async def _probe_iframe_times(video_path: Path) -> list[float]:
    """列出所有关键帧的时间戳。

    读的是**包**（packet）而不是帧：包层面只需要解复用，完全不解码，
    关键帧标记就写在包的 flags 里（含 "K"）。

    最初这里用的是 `-skip_frame nokey -show_entries frame=pts_time`，
    看起来更直观，但那条路有两个坑（见 docs/EXPERIMENTS.md E8）：
    AV1 流下 `pts_time` 返回空值，且 libdav1d 根本不理会 `-skip_frame`，
    结果是把整条视频完整解码一遍再吐出全部 18948 帧——632 秒的视频要 38 秒，
    比它想优化掉的 scene 检测还慢。包级读取同一条视频只要零点几秒。
    """
    process = await asyncio.create_subprocess_exec(
        ffprobe_path(),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "packet=pts_time,flags",
        "-of",
        "csv=p=0",
        str(video_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await process.communicate()
    if process.returncode != 0:
        return []

    times: list[float] = []
    for line in stdout.decode("utf-8", "replace").splitlines():
        value, _, flags = line.strip().partition(",")
        if "K" not in flags or not value:
            continue
        try:
            times.append(float(value))
        except ValueError:
            continue
    return sorted(times)


async def _extract_at_times(
    video_path: Path,
    dest_dir: Path,
    timestamps: list[float],
    width: int,
) -> list[Keyframe]:
    """在指定时间点各抽一帧，并发执行。"""
    if not timestamps:
        return []

    semaphore = asyncio.Semaphore(_EXTRACT_CONCURRENCY)

    async def one(index: int, timestamp: float) -> Keyframe | None:
        path = dest_dir / f"frame_{index:03d}.jpg"
        async with semaphore:
            # 输入前 seek，避免从头解码。
            try:
                await _run_ffmpeg(
                    [
                        ffmpeg_path(),
                        "-y",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-ss",
                        f"{timestamp:.3f}",
                        "-i",
                        str(video_path),
                        "-vf",
                        f"scale={width}:-2",
                        "-frames:v",
                        "1",
                        "-q:v",
                        "3",
                        str(path),
                    ]
                )
            except FFmpegError:
                # 单帧失败不影响整条视频。
                return None
        return Keyframe(timestamp=timestamp, path=path) if path.exists() else None

    results = await asyncio.gather(
        *(one(index, ts) for index, ts in enumerate(timestamps, start=1))
    )
    return [frame for frame in results if frame is not None]


async def _run_ffmpeg(args: list[str]) -> str:
    """执行 ffmpeg 并返回 stderr。"""
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    text = stderr.decode("utf-8", "replace")
    if process.returncode != 0:
        raise FFmpegError(
            f"ffmpeg 抽帧失败 (exit={process.returncode}): {text[-400:]}"
        )
    return text


async def _extract_by_scene(
    video_path: Path,
    dest_dir: Path,
    threshold: float,
    max_frames: int,
    width: int,
) -> list[Keyframe]:
    # showinfo 时间戳位于 info 级别 stderr。
    stderr = await _run_ffmpeg(
        [
            ffmpeg_path(),
            "-y",
            "-hide_banner",
            "-loglevel",
            "info",
            "-i",
            str(video_path),
            "-vf",
            f"select='gt(scene,{threshold})',scale={width}:-2,showinfo",
            "-vsync",
            "vfr",
            "-frames:v",
            str(max_frames),
            "-q:v",
            "3",
            str(dest_dir / "frame_%03d.jpg"),
        ]
    )
    return _collect(dest_dir, timestamps=_parse_showinfo_times(stderr))


_PTS_TIME = re.compile(r"pts_time:([0-9.]+)")


def _parse_showinfo_times(stderr: str) -> list[float]:
    """从 showinfo 的输出里取出每个被选中帧的时间戳。"""
    return [float(match) for match in _PTS_TIME.findall(stderr)]


def _collect(dest_dir: Path, timestamps: list[float]) -> list[Keyframe]:
    paths = sorted(dest_dir.glob("frame_*.jpg"))
    return [
        Keyframe(
            timestamp=timestamps[index] if index < len(timestamps) else 0.0,
            path=path,
        )
        for index, path in enumerate(paths)
    ]
