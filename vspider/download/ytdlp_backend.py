"""yt-dlp 下载后端。"""

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

# 720p 足够用于 ASR 和 OCR。
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
