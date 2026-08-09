"""流水线事件流。

编排器只负责发事件，不关心谁在听。CLI 把事件渲染成终端进度，
Web 后端把同一批事件通过 SSE 推给前端做实时流水线可视化。
两边共用一套事件定义，避免界面和命令行看到的进度对不上。
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Stage(str, Enum):
    DISCOVER = "discover"
    COLLECT = "collect"
    DOWNLOAD = "download"
    AUDIO = "audio"
    ASR = "asr"
    KEYFRAME = "keyframe"
    OCR = "ocr"
    SUMMARIZE = "summarize"
    DIGEST = "digest"
    STORE = "store"


STAGE_LABELS: dict[Stage, str] = {
    Stage.DISCOVER: "榜单发现",
    Stage.COLLECT: "元数据采集",
    Stage.DOWNLOAD: "视频下载",
    Stage.AUDIO: "抽取音频",
    Stage.ASR: "语音识别",
    Stage.KEYFRAME: "关键帧抽取",
    Stage.OCR: "画面文字识别",
    Stage.SUMMARIZE: "内容归纳",
    Stage.DIGEST: "生成批次总览",
    Stage.STORE: "结果入库",
}


class EventKind(str, Enum):
    RUN_START = "run_start"
    RUN_DONE = "run_done"
    VIDEO_START = "video_start"
    VIDEO_DONE = "video_done"
    VIDEO_FAILED = "video_failed"
    STAGE_START = "stage_start"
    STAGE_DONE = "stage_done"
    STAGE_SKIPPED = "stage_skipped"
    STAGE_FAILED = "stage_failed"
    LOG = "log"


@dataclass
class Event:
    kind: EventKind
    stage: Stage | None = None
    video_uid: str = ""
    message: str = ""
    elapsed_sec: float = 0.0
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "stage": self.stage.value if self.stage else None,
            "stage_label": STAGE_LABELS.get(self.stage, "") if self.stage else "",
            "video_uid": self.video_uid,
            "message": self.message,
            "elapsed_sec": round(self.elapsed_sec, 3),
            "data": self.data,
            "timestamp": self.timestamp,
        }


EventSink = Callable[[Event], Awaitable[None]]


async def noop_sink(event: Event) -> None:
    """默认事件消费者，什么都不做。"""
