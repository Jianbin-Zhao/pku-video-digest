"""抖音榜单发现。

抖音没有「视频排行榜」这种接口，只有**热点榜**（热搜词）。
所以这里用两级策略，产出的每条都会标注实际走的是哪一级：

  一级 热点榜带样例视频
       /aweme/v1/web/hot/search/list/ 带 detail_list=1 时，
       每个热词会附带平台自己挑的代表作品。这是最接近「官方榜单」的东西。
  二级 热词搜索重排
       取热词逐个搜索，把结果按互动量重新排序。
       热榜接口失效或返回的样例视频不足时兜底。

签名交给 MediaCrawler 的客户端：它的 get() 会自动补 msToken 和 a_bogus，
所以这里可以直接调用任何抖音 web 接口，包括 MediaCrawler 自己没封装的热榜。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any

from vspider.discovery.base import RankingProvider, take_top
from vspider.discovery.keyword_rerank import (
    KeywordBudget,
    KeywordRejected,
    collect_by_keywords,
)
from vspider.mediacrawler.session import MediaCrawlerSession
from vspider.models import Platform, RankSource, VideoItem, VideoStats

_HOT_LIST_URI = "/aweme/v1/web/hot/search/list/"
# 二级策略下每个热词搜一页就够。热词本身已经是排序过的，
# 与其在单个词里翻深，不如多覆盖几个词。
_SEARCH_KEYWORDS_LIMIT = 6

_LOG = logging.getLogger(__name__)


class DouyinRankingProvider(RankingProvider):
    platform = Platform.DOUYIN

    def __init__(self, session: MediaCrawlerSession) -> None:
        self._session = session

    async def _client(self) -> Any:
        return await self._session.client(Platform.DOUYIN)

    async def fetch_ranking(
        self,
        limit: int = 5,
        category: str = "all",
        today_only: bool = False,
    ) -> list[VideoItem]:
        items, source = await self._from_hot_list(limit)
        if len(items) < limit:
            extra = await self._from_hot_keywords(limit - len(items))
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

        return take_top(items, limit, source, category)

    async def _from_hot_list(self, limit: int) -> tuple[list[VideoItem], RankSource]:
        """一级策略：热点榜自带的代表作品。"""
        client = await self._client()
        try:
            payload = await client.get(_HOT_LIST_URI, {"detail_list": 1})
        except Exception as exc:  # noqa: BLE001
            # 热榜接口是抖音改版时最容易动的一个，失败就交给二级策略，
            # 不要因为它挂掉就让整个平台不可用。
            _LOG.warning("抖音热榜接口失败，改走热词搜索：%s", str(exc)[:160])
            return [], RankSource.HOT_KEYWORD_RERANK

        words = ((payload or {}).get("data") or {}).get("word_list") or []
        items: list[VideoItem] = []
        for word in words:
            for entry in word.get("aweme_infos") or []:
                aweme = entry.get("aweme_info") or entry
                item = _parse_aweme(aweme)
                if item is not None:
                    item.tags = [w for w in [word.get("word")] if w]
                    items.append(item)
            if len(items) >= limit:
                break
        return items, RankSource.OFFICIAL_RANKING

    async def _from_hot_keywords(self, need: int) -> list[VideoItem]:
        """二级策略：取热词去搜索，再按互动量重排。

        循环骨架和熔断都交给 collect_by_keywords，这里只提供
        「怎么搜」和「怎么解析」两个回调。关键是把抖音的
        status_code=2483（要求登录/限流）翻译成 KeywordRejected，
        让连续被拒时立刻收手，而不是把六个词全打一遍加深频控。
        """
        client = await self._client()
        keywords = await self._hot_keywords()
        if not keywords:
            _LOG.warning("抖音热词一个都没拿到，无法走热词搜索")
            return []

        async def search(keyword: str) -> list[dict[str, Any]]:
            payload = await client.search_info_by_keyword(keyword=keyword, offset=0)
            # 抖音的失败会伪装成成功：HTTP 200、不抛异常，但 status_code 非 0。
            # 不显式检查就会表现成安静的「搜不到东西」。
            status = (payload or {}).get("status_code")
            if status not in (0, None):
                raise KeywordRejected(
                    f"status_code={status} {(payload or {}).get('status_msg', '')}"
                )
            return list((payload or {}).get("data") or [])

        def parse(entry: dict[str, Any]) -> VideoItem | None:
            aweme = entry.get("aweme_info")
            if not aweme and entry.get("aweme_mix_info"):
                mix = entry["aweme_mix_info"].get("mix_items") or [{}]
                aweme = mix[0]
            return _parse_aweme(aweme) if aweme else None

        ranked = await collect_by_keywords(
            keywords=keywords,
            search=search,
            parse=parse,
            limit=need,
            platform_name="抖音",
            budget=KeywordBudget(max_keywords=_SEARCH_KEYWORDS_LIMIT),
        )
        return ranked[:need]

    async def _hot_keywords(self) -> list[str]:
        client = await self._client()
        try:
            payload = await client.get(_HOT_LIST_URI, {})
        except Exception:  # noqa: BLE001
            return []
        words = ((payload or {}).get("data") or {}).get("word_list") or []
        return [w.get("word", "") for w in words if w.get("word")]

    async def search_videos(self, keyword: str, limit: int = 5) -> list[VideoItem]:
        """按关键词搜索视频，按互动量排序（场景三）。"""
        client = await self._client()
        payload = await client.search_info_by_keyword(keyword=keyword, offset=0)
        status = (payload or {}).get("status_code")
        if status not in (0, None):
            raise RuntimeError(
                f"抖音搜索被拒：status_code={status} "
                f"{(payload or {}).get('status_msg', '')}"
            )

        items: list[VideoItem] = []
        for entry in (payload or {}).get("data") or []:
            aweme = entry.get("aweme_info")
            if not aweme and entry.get("aweme_mix_info"):
                mix = entry["aweme_mix_info"].get("mix_items") or [{}]
                aweme = mix[0]
            item = _parse_aweme(aweme) if aweme else None
            if item is not None:
                items.append(item)
        items.sort(key=lambda i: i.stats.engagement(), reverse=True)
        return take_top(items, limit, RankSource.KEYWORD_SEARCH, keyword)

    async def fetch_creator_videos(
        self,
        creator_id: str,
        limit: int = 20,
        since: date | None = None,
    ) -> list[VideoItem]:
        """取创作者作品。

        抖音的 creator_id 用的是 sec_user_id（形如 MS4wLjABAAAA...），
        不是主页地址里那串数字 uid。传主页链接也行，会自动解析。
        """
        client = await self._client()
        sec_user_id = await self._resolve_sec_user_id(creator_id)

        collected: list[VideoItem] = []
        max_cursor = ""
        for _ in range(5):
            payload = await client.get_user_aweme_posts(sec_user_id, max_cursor)
            awemes = (payload or {}).get("aweme_list") or []
            if not awemes:
                break

            stop = False
            for aweme in awemes:
                item = _parse_aweme(aweme)
                if item is None:
                    continue
                if since is not None and item.publish_time is not None:
                    # 作品列表默认按发布时间倒序，遇到更早的就可以收工了。
                    if item.publish_time.date() < since:
                        stop = True
                        break
                collected.append(item)
                if len(collected) >= limit:
                    stop = True
                    break

            if stop or not payload.get("has_more"):
                break
            max_cursor = payload.get("max_cursor") or ""
            await asyncio.sleep(0.8)

        return take_top(
            collected, limit, RankSource.CREATOR_TIMELINE, category=creator_id
        )

    async def _resolve_sec_user_id(self, creator_id: str) -> str:
        if not creator_id.startswith("http"):
            return creator_id
        # 主页链接形如 https://www.douyin.com/user/MS4wLjABAAAA...
        from media_platform.douyin.help import parse_creator_info_from_url

        return parse_creator_info_from_url(creator_id).sec_user_id


def _parse_aweme(aweme: dict[str, Any]) -> VideoItem | None:
    """把抖音的 aweme 结构映射成统一模型。"""
    aweme_id = str(aweme.get("aweme_id") or "")
    if not aweme_id:
        return None

    author = aweme.get("author") or {}
    stats = aweme.get("statistics") or {}
    video = aweme.get("video") or {}
    created = aweme.get("create_time")

    tags = [
        tag.get("hashtag_name", "")
        for tag in (aweme.get("text_extra") or [])
        if tag.get("hashtag_name")
    ]

    return VideoItem(
        platform=Platform.DOUYIN,
        video_id=aweme_id,
        url=f"https://www.douyin.com/video/{aweme_id}",
        # 抖音没有独立标题字段，desc 同时承担标题和正文。
        title=(aweme.get("desc") or "").strip(),
        desc=(aweme.get("desc") or "").strip(),
        author_id=str(author.get("sec_uid") or author.get("uid") or ""),
        author_name=author.get("nickname") or "",
        publish_time=datetime.fromtimestamp(created) if created else None,
        # video.duration 是毫秒。
        duration_sec=int((video.get("duration") or 0) / 1000),
        cover_url=_first_url(video.get("cover") or video.get("origin_cover")),
        tags=tags,
        stats=VideoStats(
            play=int(stats.get("play_count") or 0),
            like=int(stats.get("digg_count") or 0),
            comment=int(stats.get("comment_count") or 0),
            share=int(stats.get("share_count") or 0),
            collect=int(stats.get("collect_count") or 0),
        ),
        raw=aweme,
    )


def extract_video_url(aweme: dict[str, Any]) -> str:
    """取抖音视频直链。

    play_addr 是无水印地址，url_list 里是多个 CDN 节点，内容相同。
    注意这些地址**必须带 Referer 才能下载**，直接访问会被拒。
    """
    video = aweme.get("video") or {}
    for key in ("play_addr", "play_addr_h264", "download_addr", "play_addr_lowbr"):
        url = _first_url(video.get(key))
        if url:
            return url
    return ""


def _first_url(node: Any) -> str:
    if isinstance(node, dict):
        urls = node.get("url_list") or []
        return urls[0] if urls else ""
    return ""
