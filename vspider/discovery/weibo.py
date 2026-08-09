"""微博榜单发现。

微博和其他四个平台有个本质差异：**它不是视频平台**。
绝大多数微博是纯文字或图文，带视频的是少数。所以这里每一步都要显式筛选出
page_info.type == "video" 的条目，否则榜单里会混进一堆没法下载的纯文本微博。

两级策略：
  一级 视频社区频道
       m.weibo.cn 的 containerid 频道里有视频专区，返回的就是视频流，
       最接近「视频榜单」。
  二级 热搜词搜索后筛视频
       频道不可用时，取热搜词去搜，再把非视频条目剔掉。

MediaCrawler 的微博模块只处理图片，视频直链要自己从 page_info 里取，
所以这里额外提供 extract_video_url 供下载层复用。
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime
from typing import Any

from vspider.discovery.base import RankingProvider, take_top
from vspider.mediacrawler.session import MediaCrawlerSession
from vspider.models import Platform, RankSource, VideoItem, VideoStats

_INDEX_URI = "/api/container/getIndex"
# 视频相关频道，按优先级尝试。微博经常调整 containerid，
# 所以列多个候选，逐个试到能用为止。
_VIDEO_CONTAINERS = (
    "102803_ctg1_4188_-_ctg1_4188",
    "102803_ctg1_4288_-_ctg1_4288",
)
_HOT_SEARCH_CONTAINER = "106003type=25&t=3&disable_hot=1&show_type=1"
_HTML_TAG = re.compile(r"<[^>]+>")
_LOG = logging.getLogger(__name__)


def _cards_of(payload: Any) -> list[dict[str, Any]]:
    """从响应里取 cards。

    这里有个容易踩的坑：MediaCrawler 的 WeiboClient.request 已经把
    最外层的 data 剥掉了，所以拿到的是 {cardlistInfo, cards}，
    而不是微博原始的 {ok, data: {cards}}。两种都兼容，
    免得哪天上游改了行为又要重查一遍。
    """
    if not isinstance(payload, dict):
        return []
    cards = payload.get("cards")
    if cards is None:
        cards = (payload.get("data") or {}).get("cards")
    return cards or []


class WeiboRankingProvider(RankingProvider):
    platform = Platform.WEIBO

    def __init__(self, session: MediaCrawlerSession) -> None:
        self._session = session

    async def _client(self) -> Any:
        return await self._session.client(Platform.WEIBO)

    async def fetch_ranking(
        self,
        limit: int = 5,
        category: str = "all",
        today_only: bool = False,
    ) -> list[VideoItem]:
        items, source = await self._from_video_channel(limit)
        if len(items) < limit:
            extra = await self._from_hot_search(limit - len(items))
            if extra:
                seen = {i.video_id for i in items}
                items.extend(e for e in extra if e.video_id not in seen)
                source = RankSource.HOT_KEYWORD_RERANK

        if today_only:
            today = date.today()
            items = [
                i
                for i in items
                if i.publish_time is not None and i.publish_time.date() == today
            ]

        items.sort(key=lambda i: i.stats.engagement(), reverse=True)
        return take_top(items, limit, source, category)

    async def _from_video_channel(self, limit: int) -> tuple[list[VideoItem], RankSource]:
        client = await self._client()
        items: list[VideoItem] = []

        for containerid in _VIDEO_CONTAINERS:
            for page in range(1, 4):
                try:
                    payload = await client.get(
                        _INDEX_URI, {"containerid": containerid, "page": page}
                    )
                except Exception as exc:  # noqa: BLE001
                    _LOG.warning(
                        "微博频道 %s 第 %d 页失败：%s",
                        containerid[:24],
                        page,
                        str(exc)[:140],
                    )
                    break

                cards = _cards_of(payload)
                if not cards:
                    break
                items.extend(_parse_cards(cards))
                if len(items) >= limit * 2:
                    break
                await asyncio.sleep(0.8)
            if items:
                break

        return items, RankSource.OFFICIAL_POPULAR

    async def _from_hot_search(self, need: int) -> list[VideoItem]:
        client = await self._client()
        keywords = await self._hot_keywords()
        if not keywords:
            return []

        collected: dict[str, VideoItem] = {}
        for keyword in keywords[:6]:
            try:
                payload = await client.get_note_by_keyword(keyword=keyword)
            except Exception:  # noqa: BLE001
                continue
            for item in _parse_cards(_cards_of(payload)):
                if item.video_id not in collected:
                    item.tags = [keyword]
                    collected[item.video_id] = item
            if len(collected) >= need * 3:
                break
            await asyncio.sleep(1.0)

        ranked = sorted(
            collected.values(), key=lambda i: i.stats.engagement(), reverse=True
        )
        return ranked[:need]

    async def _hot_keywords(self) -> list[str]:
        client = await self._client()
        try:
            payload = await client.get(
                _INDEX_URI, {"containerid": _HOT_SEARCH_CONTAINER}
            )
        except Exception:  # noqa: BLE001
            return []

        words: list[str] = []
        for card in _cards_of(payload):
            for group in card.get("card_group") or []:
                text = group.get("desc") or ""
                if text:
                    words.append(text)
        return words

    async def search_videos(self, keyword: str, limit: int = 5) -> list[VideoItem]:
        """按关键词搜索视频微博，按互动量排序（场景三）。"""
        client = await self._client()
        payload = await client.get_note_by_keyword(keyword=keyword)
        items = list(_parse_cards(_cards_of(payload)))
        items.sort(key=lambda i: i.stats.engagement(), reverse=True)
        return take_top(items, limit, RankSource.KEYWORD_SEARCH, keyword)

    async def fetch_creator_videos(
        self,
        creator_id: str,
        limit: int = 20,
        since: date | None = None,
    ) -> list[VideoItem]:
        """取某个博主发的视频微博。

        微博的用户主页也是 containerid 驱动的，需要先用 uid 换取
        「微博」标签页的 containerid，不能直接用 uid 拉列表。
        """
        client = await self._client()
        uid = _extract_uid(creator_id)
        containerid = await self._creator_container(uid)

        collected: list[VideoItem] = []
        for page in range(1, 6):
            try:
                payload = await client.get(
                    _INDEX_URI,
                    {"containerid": containerid, "page": page, "type": "uid", "value": uid},
                )
            except Exception:  # noqa: BLE001
                break

            cards = _cards_of(payload)
            if not cards:
                break

            stop = False
            for item in _parse_cards(cards):
                if since is not None and item.publish_time is not None:
                    if item.publish_time.date() < since:
                        stop = True
                        break
                collected.append(item)
                if len(collected) >= limit:
                    stop = True
                    break
            if stop:
                break
            await asyncio.sleep(0.8)

        return take_top(
            collected, limit, RankSource.CREATOR_TIMELINE, category=creator_id
        )

    async def _creator_container(self, uid: str) -> str:
        client = await self._client()
        try:
            info = await client.get_creator_container_info(uid)
            if isinstance(info, dict):
                container = info.get("lfid_container") or info.get("containerid")
                if container:
                    return str(container)
        except Exception:  # noqa: BLE001
            pass
        # 107603+uid 是「该用户的微博」这个标签页的固定构造方式。
        return f"107603{uid}"


def _parse_cards(cards: list[dict[str, Any]]) -> list[VideoItem]:
    """从 card 列表里挑出带视频的微博。

    微博的 card 有两种形态：card_type=9 直接带 mblog，
    以及分组卡片把多条 mblog 塞在 card_group 里。两种都要展开。
    """
    out: list[VideoItem] = []
    for card in cards:
        blogs = []
        if card.get("mblog"):
            blogs.append(card["mblog"])
        for group in card.get("card_group") or []:
            if group.get("mblog"):
                blogs.append(group["mblog"])

        for mblog in blogs:
            item = _parse_mblog(mblog)
            if item is not None:
                out.append(item)
    return out


def _parse_mblog(mblog: dict[str, Any]) -> VideoItem | None:
    page_info = mblog.get("page_info") or {}
    # 微博绝大多数是纯文字，没有视频的直接跳过——
    # 留下来后面也下载不了，只会污染榜单。
    if page_info.get("type") != "video":
        return None

    note_id = str(mblog.get("id") or mblog.get("mid") or "")
    if not note_id:
        return None

    user = mblog.get("user") or {}
    text = _HTML_TAG.sub("", mblog.get("text") or "").strip()
    media = page_info.get("media_info") or {}

    return VideoItem(
        platform=Platform.WEIBO,
        video_id=note_id,
        url=f"https://m.weibo.cn/detail/{note_id}",
        # 优先用正文当标题。page_info.page_title 看着像标题，实测绝大多数是
        # 「某某的微博视频」这种自动生成的占位串，没有任何信息量，
        # 拿去做归纳只会浪费上下文。
        title=(text or page_info.get("page_title") or "")[:120],
        desc=text,
        author_id=str(user.get("id") or ""),
        author_name=user.get("screen_name") or "",
        publish_time=_parse_created_at(mblog.get("created_at")),
        duration_sec=int(media.get("duration") or 0),
        cover_url=(page_info.get("page_pic") or {}).get("url", "")
        if isinstance(page_info.get("page_pic"), dict)
        else str(page_info.get("page_pic") or ""),
        stats=VideoStats(
            play=int(media.get("online_users_number") or 0),
            like=int(mblog.get("attitudes_count") or 0),
            comment=int(mblog.get("comments_count") or 0),
            share=int(mblog.get("reposts_count") or 0),
        ),
        raw=mblog,
    )


def extract_video_url(mblog: dict[str, Any]) -> str:
    """从微博原始数据里取视频直链。

    微博把直链散在 media_info 和 urls 两处，字段名还随清晰度变化，
    这里按清晰度从高到低逐个试。下载层直接复用这个函数。
    """
    page_info = mblog.get("page_info") or {}
    media = page_info.get("media_info") or {}
    urls = page_info.get("urls") or {}

    candidates = [
        media.get("mp4_720p_mp4"),
        media.get("mp4_hd_url"),
        media.get("stream_url_hd"),
        media.get("mp4_sd_url"),
        media.get("stream_url"),
        urls.get("mp4_720p_mp4"),
        urls.get("mp4_hd_mp4"),
        urls.get("mp4_ld_mp4"),
    ]
    for url in candidates:
        if url:
            return str(url)
    return ""


def _parse_created_at(value: Any) -> datetime | None:
    """解析微博的时间字段。

    它同时存在两种格式：RFC2822（"Fri Aug 07 21:00:00 +0800 2026"）
    和「x分钟前」这类相对时间。MediaCrawler 的工具函数处理了前者，
    后者只能退回 None。
    """
    if not value:
        return None
    try:
        from tools import utils

        timestamp = utils.rfc2822_to_timestamp(value)
        if timestamp:
            return datetime.fromtimestamp(timestamp)
    except Exception:  # noqa: BLE001
        pass
    return None


def _extract_uid(creator_id: str) -> str:
    if not creator_id.startswith("http"):
        return creator_id
    match = re.search(r"/u/(\d+)|/(\d{6,})", creator_id)
    if match:
        return match.group(1) or match.group(2)
    return creator_id.rstrip("/").split("/")[-1].split("?")[0]
