"""关键帧抽取。

短视频的画面信息高度冗余：一个镜头里连续几十帧内容几乎相同，
逐帧 OCR 既慢又会产出大量重复文本。因此要抽帧。

三种策略，默认走 iframe（依据见 docs/EXPERIMENTS.md E8）：

  iframe    只解码 I 帧。编码器本来就会在镜头切变处插入 I 帧，
            所以这等于近乎免费地拿到了场景对齐的采样点
  scene     用 scene 滤镜按画面变化程度筛选。语义上最准，
            但要把整条视频完整解码一遍，开销随时长线性增长
  interval  按固定间隔均匀采样。兜底用，也是长镜头讲解类视频的唯一可行解

无论走哪条，帧数不足时都会退化到 interval：
一个十分钟的单镜头视频若只抽到一帧，硬字幕就全漏了。
"""

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

# 并发抽帧数。每次抽帧是一个独立的 ffmpeg 进程，且都是关键帧定位，
# 单次开销很小，主要成本在进程启动上，所以开到 6 收益就基本饱和了。
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
        # I 帧可能有几百个，均匀取 max_frames 个，覆盖整条视频而不是只取开头。
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
        # 帧数不足，改成均匀采样。目标帧数取 min_frames 与 max_frames 之间，
        # 按时长决定：越长的视频多抽几帧。
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
    # 跳过首尾各 2%：短视频开头常是黑场或平台水印，结尾常是关注引导，
    # 都不含内容信息。
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
            # -ss 放在 -i 之前是关键：这样 ffmpeg 直接 seek 到目标位置，
            # 放在后面会从头解码到该位置，长视频上慢几个数量级。
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
                # 单个时间点抽失败（多见于末尾越界）不该让整条视频失败，
                # 少一帧对 OCR 的影响可以忽略。
                return None
        return Keyframe(timestamp=timestamp, path=path) if path.exists() else None

    results = await asyncio.gather(
        *(one(index, ts) for index, ts in enumerate(timestamps, start=1))
    )
    # 就地构造而不是回头扫目录：抽帧可能有个别失败，
    # 靠文件名顺序和时间戳列表对位的话，缺一个后面就全错位了。
    return [frame for frame in results if frame is not None]


async def _run_ffmpeg(args: list[str]) -> str:
    """执行 ffmpeg 并返回 stderr 文本。

    返回 stderr 是因为 showinfo 滤镜的逐帧信息（含时间戳）写在这里。
    """
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
    # 两个容易踩的点：
    # -vsync vfr 配合 select 滤镜才能只输出被选中的帧，缺了它 ffmpeg 会按
    #   固定帧率补帧，产出大量重复图片；
    # -loglevel info 是必需的，showinfo 的逐帧时间戳写在 info 级别的 stderr 里，
    #   降到 error 就拿不到时间轴了。
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
            # 时间戳数量理论上与图片一一对应，但 ffmpeg 在末尾截断时
            # 两者可能差一个，取不到就退化为 0 而不是让整条流水线崩掉。
            timestamp=timestamps[index] if index < len(timestamps) else 0.0,
            path=path,
        )
        for index, path in enumerate(paths)
    ]
