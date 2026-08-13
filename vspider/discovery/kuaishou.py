"""快手搜索、榜单和创作者数据。"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Any

from vspider.discovery.base import RankingProvider, take_top
from vspider.discovery.keyword_rerank import KeywordRejected, collect_by_keywords
from vspider.mediacrawler.session import MediaCrawlerSession
from vspider.models import Platform, RankSource, VideoItem, VideoStats
from vspider.settings import local_datetime_fromtimestamp, local_today

_SEED_KEYWORDS = (
    "热门",
    "搞笑",
    "美食",
    "音乐",
    "生活",
    "知识",
)


class KuaishouRankingProvider(RankingProvider):
    platform = Platform.KUAISHOU

    def __init__(self, session: MediaCrawlerSession) -> None:
        self._session = session

    async def _client(self) -> Any:
        return await self._session.client(Platform.KUAISHOU)

    async def fetch_ranking(
        self,
        limit: int = 5,
        category: str = "all",
        today_only: bool = False,
    ) -> list[VideoItem]:
        client = await self._client()

        async def search(keyword: str) -> list[dict[str, Any]]:
            # pcursor 在 V2 搜索里是数字页码字符串，首页传 "1"。
            payload = await client.search_info_by_keyword_v2(
                keyword=keyword, pcursor="1"
            )
            # V2 用 result 字段表示业务状态，1 才是成功。
            # 被限流时 MediaCrawler 内部已经退避重试过 3 次，仍失败才到这里，
            # 所以要当成明确拒绝、触发熔断，绝不能再往下压。
            if (payload or {}).get("result") not in (1, None):
                raise KeywordRejected(f"result={payload.get('result')}")
            # V2 把 feeds 提到了顶层，不再包在 visionSearchPhoto 里。
            return list((payload or {}).get("feeds") or [])

        items = await collect_by_keywords(
            keywords=list(_SEED_KEYWORDS),
            search=search,
            parse=_parse_feed,
            limit=limit,
            platform_name="快手",
        )

        if today_only:
            today = local_today()
            items = [
                i
                for i in items
                if i.publish_time is not None and i.publish_time.date() == today
            ]

        return take_top(items, limit, RankSource.HOT_KEYWORD_RERANK, category)

    async def search_videos(self, keyword: str, limit: int = 5) -> list[VideoItem]:
        """按关键词搜索视频，按互动量排序（场景三）。"""
        client = await self._client()
        payload = await client.search_info_by_keyword_v2(keyword=keyword, pcursor="1")
        if (payload or {}).get("result") not in (1, None):
            raise RuntimeError(f"快手搜索被拒：result={payload.get('result')}")
        items = [
            item
            for item in (
                _parse_feed(feed) for feed in (payload or {}).get("feeds") or []
            )
            if item is not None
        ]
        items.sort(key=lambda i: i.stats.engagement(), reverse=True)
        return take_top(items, limit, RankSource.KEYWORD_SEARCH, keyword)

    async def fetch_creator_videos(
        self,
        creator_id: str,
        limit: int = 20,
        since: date | None = None,
    ) -> list[VideoItem]:
        """取创作者作品。creator_id 为快手 userId，也可直接传主页链接。"""
        client = await self._client()
        user_id = _extract_user_id(creator_id)

        collected: list[VideoItem] = []
        pcursor = ""
        for _ in range(5):
            # 作品列表同样已迁到签名版 /rest/v/profile/feed。
            payload = await client.get_video_by_creater_v2(user_id, pcursor)
            feeds = (payload or {}).get("feeds") or []
            if not feeds:
                break

            stop = False
            for feed in feeds:
                item = _parse_feed(feed)
                if item is None:
                    continue
                if since is not None and item.publish_time is not None:
                    if item.publish_time.date() < since:
                        stop = True
                        break
                collected.append(item)
                if len(collected) >= limit:
                    stop = True
                    break

            pcursor = (payload or {}).get("pcursor") or ""
            if stop or not pcursor or pcursor == "no_more":
                break
            await asyncio.sleep(0.8)

        return take_top(
            collected, limit, RankSource.CREATOR_TIMELINE, category=creator_id
        )


def _parse_feed(feed: dict[str, Any]) -> VideoItem | None:
    """把快手的 feed 结构映射成统一模型。"""
    photo = feed.get("photo") or {}
    author = feed.get("author") or {}
    video_id = str(photo.get("id") or "")
    if not video_id:
        return None

    # timestamp 是毫秒。
    timestamp = photo.get("timestamp")
    publish_time = (
        local_datetime_fromtimestamp(timestamp / 1000)
        if isinstance(timestamp, (int, float)) and timestamp > 0
        else None
    )

    caption = (photo.get("caption") or "").strip()
    return VideoItem(
        platform=Platform.KUAISHOU,
        video_id=video_id,
        url=f"https://www.kuaishou.com/short-video/{video_id}",
        # 快手同样只有一个 caption 字段，标题和正文共用。
        title=caption,
        desc=caption,
        author_id=str(author.get("id") or ""),
        author_name=author.get("name") or "",
        publish_time=publish_time,
        duration_sec=int((photo.get("duration") or 0) / 1000),
        cover_url=photo.get("coverUrl") or "",
        stats=VideoStats(
            play=_as_int(photo.get("viewCount")),
            # V2 用 likeCount，旧 GraphQL 版是 realLikeCount，两个都认。
            like=_as_int(photo.get("realLikeCount") or photo.get("likeCount")),
            # V2 的搜索结果里不带评论数和分享数，只能留 0。
            comment=_as_int(photo.get("commentCount")),
            share=_as_int(photo.get("shareCount")),
            collect=_as_int(photo.get("collectCount")),
        ),
        raw=feed,
    )


def extract_video_url(feed: dict[str, Any]) -> str:
    """取快手视频直链。

    V2 返回的是 photoUrls 列表（多个 CDN 节点），旧版是 photoUrl 单个字符串。
    列表里各项内容相同、只是节点不同，取第一个即可。
    """
    photo = feed.get("photo") or feed
    urls = photo.get("photoUrls") or []
    for entry in urls:
        if isinstance(entry, dict) and entry.get("url"):
            return str(entry["url"])
        if isinstance(entry, str) and entry:
            return entry

    single = photo.get("photoUrl")
    if single:
        return str(single)

    # 兜底：从 manifest 的自适应码流里取。
    for key in ("manifest", "manifestH265"):
        for adaptation in (photo.get(key) or {}).get("adaptationSet") or []:
            for representation in adaptation.get("representation") or []:
                if representation.get("url"):
                    return str(representation["url"])
    return ""


def _extract_user_id(creator_id: str) -> str:
    if not creator_id.startswith("http"):
        return creator_id
    # 主页链接形如 https://www.kuaishou.com/profile/3xxxxxx
    return creator_id.rstrip("/").split("/")[-1].split("?")[0]


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
