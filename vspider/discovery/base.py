"""榜单发现层的抽象接口。

这是整个项目里 MediaCrawler 完全没有覆盖的部分：它的抓取类型只有
search / detail / creator 三种，没有任何"榜单"概念。

各平台的榜单能力差异极大，无法用一套调用方式覆盖，因此这里定义
两级策略，由各平台实现按能力自行选择：

  一级（首选）官方榜单接口 —— B 站有排行榜/热门，微博有热搜与视频榜
  二级（降级）热词重排     —— 抖音/快手只有热搜词榜，小红书什么都没有，
                              只能取热词去搜索，再按互动量重新排序近似榜单

每条产出都会带上 RankSource 标注实际走的是哪一级，
使得结果的可信度在报告和界面上都是透明的。
"""

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
        """取榜单前 limit 条。

        Args:
            limit: 返回条数。
            category: 平台自定义的分区/类目标识，"all" 表示全站。
            today_only: 只保留今天发布的视频。

                这里对应老师题面里"今天排行榜前 5 的视频"的歧义：既可以理解为
                "今天这个时刻榜单的前 5 名"（榜单本身是滚动的，视频可能是几天前发的），
                也可以理解为"今天发布的视频里最热的 5 个"。
                默认按前者（平台榜单的当前快照），置 True 时按后者过滤。

        Returns:
            已按榜位排序、且 rank 与 rank_source 均已填充的列表。
        """

    @abc.abstractmethod
    async def fetch_creator_videos(
        self,
        creator_id: str,
        limit: int = 20,
        since: date | None = None,
    ) -> list[VideoItem]:
        """取某个创作者的作品，可按发布日期下界过滤。

        对应题面第二个场景"某个用户今天发布的视频"，
        调用方传 since=today 即可。
        """

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
