"""平台发现接口。"""

from __future__ import annotations

import abc
from datetime import date, datetime

from vspider.models import Platform, RankSource, VideoItem


class RankingProvider(abc.ABC):
    """单个平台的榜单提供者。"""

    platform: Platform

    @abc.abstractmethod
    async def fetch_ranking(
        self,
        limit: int = 5,
        category: str = "all",
        today_only: bool = False,
    ) -> list[VideoItem]:
        """取当前榜单；today_only 仅保留今天发布的视频。"""

    @abc.abstractmethod
    async def fetch_creator_videos(
        self,
        creator_id: str,
        limit: int = 20,
        since: date | None = None,
    ) -> list[VideoItem]:
        """取创作者作品，可按发布日期过滤。"""

    async def search_videos(self, keyword: str, limit: int = 5) -> list[VideoItem]:
        """按关键词搜索视频。"""
        raise NotImplementedError(f"{self.platform.value} 尚未支持关键词搜索")

    async def aclose(self) -> None:
        """释放底层连接。默认无操作。"""


def is_same_day(when: datetime | None, day: date) -> bool:
    if when is None:
        return False
    return when.date() == day


def take_top(
    items: list[VideoItem],
    limit: int,
    source: RankSource,
    category: str = "",
) -> list[VideoItem]:
    """截断到 limit 条并回填榜位信息。"""
    out = items[:limit]
    for index, item in enumerate(out, start=1):
        item.rank = index
        item.rank_source = source
        item.rank_category = category
    return out
