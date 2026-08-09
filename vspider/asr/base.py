"""语音识别后端抽象。"""

from __future__ import annotations

import abc
from pathlib import Path

from vspider.models import Transcript


class AsrBackend(abc.ABC):
    name: str

    @abc.abstractmethod
    async def transcribe(self, audio_path: Path) -> Transcript:
        """把 16k 单声道 wav 转成带时间戳分段的文稿。"""

    async def aclose(self) -> None:
        """释放模型占用的资源。默认无操作。"""
