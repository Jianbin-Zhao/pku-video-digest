"""流水线各阶段之间传递的数据结构。

所有平台的原始响应都会被归一化成这里的 VideoItem，
后续的下载、转写、OCR、归纳阶段都只认这一套结构，
从而让平台适配层和处理层彻底解耦。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Platform(str, Enum):
    BILIBILI = "bili"
    DOUYIN = "dy"
    KUAISHOU = "ks"
    WEIBO = "wb"
    XHS = "xhs"


class RankSource(str, Enum):
    """榜单来源，用于在报告中标注每条数据的可信度。

    平台之间的榜单能力差异极大：B 站有官方排行榜接口，
    抖音/快手只有热搜词榜，小红书连热搜词都拿不到，
    因此必须记录每一条结果究竟是怎么来的。
    """

    OFFICIAL_RANKING = "official_ranking"
    OFFICIAL_POPULAR = "official_popular"
    HOT_KEYWORD_RERANK = "hot_keyword_rerank"
    CREATOR_TIMELINE = "creator_timeline"
    KEYWORD_SEARCH = "keyword_search"


class VideoStats(BaseModel):
    play: int = 0
    like: int = 0
    comment: int = 0
    share: int = 0
    collect: int = 0
    danmaku: int = 0

    def engagement(self) -> int:
        """互动总量。用于没有官方榜单的平台做人工重排。

        权重参考各平台行为成本：收藏/分享的表达强度高于点赞。
        """
        return self.like + 2 * self.comment + 3 * self.share + 3 * self.collect


class VideoItem(BaseModel):
    platform: Platform
    video_id: str
    url: str
    title: str = ""
    desc: str = ""
    author_id: str = ""
    author_name: str = ""
    publish_time: datetime | None = None
    duration_sec: int = 0
    cover_url: str = ""
    tags: list[str] = Field(default_factory=list)
    stats: VideoStats = Field(default_factory=VideoStats)

    rank: int | None = None
    rank_source: RankSource | None = None
    rank_category: str = ""

    raw: dict[str, Any] = Field(default_factory=dict, repr=False)

    @property
    def uid(self) -> str:
        return f"{self.platform.value}:{self.video_id}"


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str


class Transcript(BaseModel):
    backend: str
    language: str = ""
    full_text: str = ""
    segments: list[TranscriptSegment] = Field(default_factory=list)
    elapsed_sec: float = 0.0

    @property
    def is_empty(self) -> bool:
        return not self.full_text.strip()


class OcrFrame(BaseModel):
    timestamp: float
    texts: list[str] = Field(default_factory=list)


class OcrResult(BaseModel):
    backend: str
    frames: list[OcrFrame] = Field(default_factory=list)
    elapsed_sec: float = 0.0

    def merged_text(self) -> str:
        """按出现顺序去重后拼接。

        短视频硬字幕在连续帧里会大量重复，直接拼接会污染 LLM 上下文。
        """
        seen: set[str] = set()
        out: list[str] = []
        for frame in self.frames:
            for text in frame.texts:
                key = text.strip()
                if key and key not in seen:
                    seen.add(key)
                    out.append(key)
        return "\n".join(out)


class Comment(BaseModel):
    text: str
    like_count: int = 0
    author_name: str = ""


class Sentiment(str, Enum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    MIXED = "mixed"


class Summary(BaseModel):
    """归纳结果。强制结构化而非自由文本，便于入库、检索和自动评测。"""

    one_liner: str
    key_points: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    sentiment: Sentiment = Sentiment.NEUTRAL
    is_promotion: bool = False
    confidence: float = 0.0

    backend: str = ""
    elapsed_sec: float = 0.0


class DigestTheme(BaseModel):
    """总览里的一个主题簇：把内容相近的视频归到一起。"""

    name: str
    description: str = ""
    video_uids: list[str] = Field(default_factory=list)


class Digest(BaseModel):
    """跨视频总览：对一整批归纳结果再做一层聚合分析。

    单条视频的 Summary 回答"这条视频讲了什么"，
    Digest 回答"这一批视频合起来说明了什么"——今天的热点主题是什么、
    哪几条同属一个话题、整体舆论倾向如何。这是单条归纳给不了的信息。
    """

    headline: str = ""
    themes: list[DigestTheme] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    top_pick_uid: str = ""
    top_pick_reason: str = ""

    backend: str = ""
    elapsed_sec: float = 0.0
