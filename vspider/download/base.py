"""下载层抽象。

各平台的可用后端差异很大，必须分开处理：

    B 站 / 微博 / 小红书   yt-dlp 有维护良好的 extractor
    抖音 / 快手            yt-dlp 内置 extractor 已失效，需自研分享页 SSR 解析

因此这里定义统一接口，由 registry 按平台分派到对应后端。
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from vspider.models import VideoItem


class DownloadMode(str, Enum):
    """下载什么。

    只做文字归纳时 AUDIO_ONLY 足够，体积通常只有完整视频的 2%~5%，
    在共享带宽的机器上差别非常明显。需要关键帧 OCR 时才必须取 VIDEO。
    """

    VIDEO = "video"
    AUDIO_ONLY = "audio"


@dataclass
class DownloadResult:
    item: VideoItem
    path: Path
    mode: DownloadMode
    size_bytes: int
    elapsed_sec: float
    backend: str
    has_video_track: bool


class Downloader(abc.ABC):
    name: str

    @abc.abstractmethod
    async def download(
        self,
        item: VideoItem,
        dest_dir: Path,
        mode: DownloadMode = DownloadMode.VIDEO,
    ) -> DownloadResult:
        """把视频或音频落到 dest_dir，返回实际路径。"""

    async def aclose(self) -> None:
        """释放底层连接。默认无操作。"""


class DownloadError(RuntimeError):
    pass
