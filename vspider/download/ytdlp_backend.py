"""基于 yt-dlp 的下载后端，用于 B 站 / 微博 / 小红书。

以子进程方式调用 yt-dlp 而非 import 其 Python API，原因有两点：
yt-dlp 的内部接口在版本间并不稳定，而 CLI 参数向来向后兼容；
且子进程天然隔离，单条视频解析失败不会污染主进程状态。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path

from vspider.download.base import (
    DownloadError,
    Downloader,
    DownloadMode,
    DownloadResult,
)
from vspider.models import Platform, VideoItem

# 720p 是刻意设的上限：更高清晰度对语音识别毫无帮助，对关键帧 OCR 的
# 识别率提升也很有限，但体积和下载时间会成倍增长。
#
# 不限定视频编码，让 yt-dlp 挑体积最优的（B 站上通常是 AV1）。
#
# 曾经改成优先 H.264，理由是 AV1 软解慢得多（同一条 632 秒视频整片解码
# 要 22.97 秒，H.264 只要 5.95 秒）。单条 A/B 测下载耗时几乎不变，看着是白拿。
#
# 但批量实测推翻了这个结论：H.264 体积大 61%，五条视频三路并发下载时
# 累计耗时从 49.19s 涨到 80.07s，而抽帧只省下约 4 秒。单条测不出来是因为
# 那时没有并发争抢带宽。
#
# 根本原因是 iframe 抽帧策略只解复用、不解码，主路径本来就不吃编码格式，
# 所以「解码快」这个好处在这条流水线上兑现不了。详见 docs/EXPERIMENTS.md E8。
_VIDEO_FORMAT = "bv*[height<=720]+ba/b[height<=720]/bv*+ba/b"
_AUDIO_FORMAT = "ba/bestaudio/b"

_SUPPORTED = {Platform.BILIBILI, Platform.WEIBO, Platform.XHS}


class YtDlpDownloader(Downloader):
    name = "yt-dlp"

    def __init__(
        self,
        binary: str = "yt-dlp",
        cookies_from_browser: str = "",
        cookie_file: str = "",
        concurrent_fragments: int = 8,
        socket_timeout: int = 20,
        retries: int = 3,
    ) -> None:
        # 优先找 PATH 上的可执行文件；找不到就退回 `python -m yt_dlp`。
        # 用 uv/pip 装进 venv 时，模块一定在，但同名脚本不一定挂到了 PATH 上，
        # 只认 exe 会误报「没装」。
        self._argv = self._resolve_binary(binary)
        self._cookies_from_browser = cookies_from_browser
        self._cookie_file = cookie_file
        self._concurrent_fragments = concurrent_fragments
        self._socket_timeout = socket_timeout
        self._retries = retries

    @staticmethod
    def _resolve_binary(binary: str) -> list[str]:
        resolved = shutil.which(binary)
        if resolved:
            return [resolved]

        import importlib.util
        import sys

        if importlib.util.find_spec("yt_dlp") is not None:
            # 用当前解释器跑模块，保证和采集层是同一个环境。
            return [sys.executable, "-m", "yt_dlp"]

        raise DownloadError(
            f"找不到 {binary}，也没有 yt_dlp 模块。请执行 uv pip install yt-dlp"
        )

    def supports(self, platform: Platform) -> bool:
        return platform in _SUPPORTED

    async def download(
        self,
        item: VideoItem,
        dest_dir: Path,
        mode: DownloadMode = DownloadMode.VIDEO,
    ) -> DownloadResult:
        dest_dir.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()

        # 输出名固定用 uid，避免标题里的表情和特殊字符在不同文件系统上出问题。
        stem = f"{item.platform.value}_{item.video_id}"
        args = [
            *self._argv,
            item.url,
            "-o",
            str(dest_dir / f"{stem}.%(ext)s"),
            "-f",
            _AUDIO_FORMAT if mode is DownloadMode.AUDIO_ONLY else _VIDEO_FORMAT,
            "--no-playlist",
            "--no-warnings",
            "--no-progress",
            "--concurrent-fragments",
            str(self._concurrent_fragments),
            "--socket-timeout",
            str(self._socket_timeout),
            "--retries",
            str(self._retries),
            "--print-json",
        ]
        if mode is DownloadMode.VIDEO:
            args += ["--merge-output-format", "mp4"]
        else:
            # 直接让 yt-dlp 转成 16k 单声道 wav，正好是 SenseVoice 期望的输入，
            # 省掉后面再单独抽一次音频。
            args += [
                "--extract-audio",
                "--audio-format",
                "wav",
                "--postprocessor-args",
                "ExtractAudio:-ar 16000 -ac 1",
            ]
        if self._cookie_file:
            args += ["--cookies", self._cookie_file]
        elif self._cookies_from_browser:
            args += ["--cookies-from-browser", self._cookies_from_browser]
        if item.platform is Platform.BILIBILI:
            args += ["--referer", "https://www.bilibili.com/"]

        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            raise DownloadError(
                f"yt-dlp 下载 {item.uid} 失败 (exit={process.returncode}): "
                f"{stderr.decode('utf-8', 'replace')[-600:]}"
            )

        path = _resolve_output(dest_dir, stem, stdout, mode)
        if path is None:
            raise DownloadError(f"yt-dlp 声称成功但找不到 {stem} 的输出文件")

        return DownloadResult(
            item=item,
            path=path,
            mode=mode,
            size_bytes=path.stat().st_size,
            elapsed_sec=time.perf_counter() - started,
            backend=self.name,
            has_video_track=mode is DownloadMode.VIDEO,
        )


def _resolve_output(
    dest_dir: Path, stem: str, stdout: bytes, mode: DownloadMode
) -> Path | None:
    """定位实际产出的文件。

    --print-json 里的 _filename 是后处理之前的名字，音频转码后扩展名会变，
    因此优先按 stem 在目录里找，json 只作为兜底。
    """
    preferred = [".wav"] if mode is DownloadMode.AUDIO_ONLY else [".mp4", ".mkv", ".flv"]
    for suffix in preferred:
        candidate = dest_dir / f"{stem}{suffix}"
        if candidate.exists():
            return candidate

    matches = sorted(
        (p for p in dest_dir.glob(f"{stem}.*") if p.is_file() and p.suffix != ".part"),
        key=lambda p: p.stat().st_size,
        reverse=True,
    )
    if matches:
        return matches[0]

    for line in stdout.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            info = json.loads(line)
        except json.JSONDecodeError:
            continue
        filename = info.get("_filename") or info.get("filename")
        if filename and Path(filename).exists():
            return Path(filename)
    return None
