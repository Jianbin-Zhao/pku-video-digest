"""B 站榜单发现。

B 站是五个平台里唯一提供了公开、稳定、免登录官方榜单接口的，
因此这里走的是一级策略（官方接口），不需要任何降级。用到两个端点：

  ranking/v2  官方排行榜，按分区，三日综合评分排序
  popular     热门，时效性更强，支持翻页

"今天发布的视频"这个诉求需要 popular 翻页配合过滤才能凑够数量：
排行榜是三日综合榜，单页里当天发布的可能不足 5 条。
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import date, datetime
from typing import Any

import httpx

from vspider.discovery.base import RankingProvider, take_top
from vspider.discovery.wbi import WbiSigner, bootstrap_cookies, ensure_bili_ticket
from vspider.models import Platform, RankSource, VideoItem, VideoStats
from vspider.settings import local_datetime_fromtimestamp, local_today

API_BASE = "https://api.bilibili.com"

_CREATOR_PAGE_SIZE = 30
_MAX_CREATOR_PAGES = 5

_RISK_CODES = {-352, -509}
_RISK_RETRIES = 5
_RISK_BACKOFF_BASE = 2.0

CATEGORIES: dict[str, int] = {
    "all": 0,
    "anime": 1,
    "music": 3,
    "game": 4,
    "entertainment": 5,
    "tech": 188,
    "kichiku": 119,
    "dance": 129,
    "fashion": 155,
    "life": 160,
    "cinephile": 181,
    "documentary": 177,
    "movie": 23,
    "teleplay": 11,
    "animal": 217,
    "food": 211,
    "car": 223,
    "sports": 234,
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


class BilibiliRankingProvider(RankingProvider):
    platform = Platform.BILIBILI

    def __init__(self, cookie: str = "", timeout: float = 15.0) -> None:
        headers = dict(_HEADERS)
        cookie = cookie or os.environ.get("BILI_COOKIE", "")
        if cookie:
            headers["Cookie"] = cookie
        self._has_login = "SESSDATA" in cookie
        self._client = httpx.AsyncClient(
            base_url=API_BASE,
            headers=headers,
            timeout=timeout,
            trust_env=False,
            follow_redirects=True,
        )
        self._signer = WbiSigner(self._client)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get_json(
        self,
        path: str,
        params: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        response = await self._client.get(path, params=params, headers=headers)
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise RuntimeError(
                f"bilibili api {path} 返回错误 code={payload.get('code')} "
                f"message={payload.get('message')!r}"
            )
        return payload.get("data") or {}

    async def _get_signed_json(
        self,
        path: str,
        params: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """发送带 WBI 签名和风控重试的请求。"""
        last_error = ""
        for attempt in range(_RISK_RETRIES):
            if attempt:
                await asyncio.sleep(_RISK_BACKOFF_BASE * (2 ** (attempt - 1)))
                await self._reset_identity()

            await self._ensure_identity()
            signed = await self._signer.sign(params)
            response = await self._client.get(path, params=signed, headers=headers)

            if response.status_code == 412:
                last_error = "HTTP 412（风控拦截）"
                continue
            if response.status_code != 200:
                last_error = f"HTTP {response.status_code}"
                continue

            payload = response.json()
            code = payload.get("code")
            if code in _RISK_CODES:
                last_error = f"code={code} {payload.get('message')!r}"
                continue
            if code != 0:
                raise RuntimeError(
                    f"bilibili api {path} 返回错误 code={code} "
                    f"message={payload.get('message')!r}"
                )
            data = payload.get("data") or {}
            # v_voucher 表示 Gaia 软风控。
            if isinstance(data, dict) and "v_voucher" in data and len(data) == 1:
                last_error = f"v_voucher 软风控（{data.get('v_voucher')!r}）"
                continue
            return data

        raise RuntimeError(
            f"bilibili api {path} 连续 {_RISK_RETRIES} 次被风控拦截"
            f"（最后一次：{last_error}）。"
            f"未登录访问该接口成功率本就不稳定，"
            f"在 .env 里配置 BILI_COOKIE（浏览器登录后的 Cookie）可显著改善。"
        )

    async def _ensure_identity(self) -> None:
        await bootstrap_cookies(self._client)
        try:
            await ensure_bili_ticket(self._client)
        except (httpx.HTTPError, RuntimeError):
            pass

    async def _reset_identity(self) -> None:
        """重置匿名身份。"""
        if self._has_login:
            return
        for name in ("buvid3", "buvid4", "b_nut", "bili_ticket"):
            self._client.cookies.delete(name, domain=".bilibili.com", path="/")

    async def fetch_ranking(
        self,
        limit: int = 5,
        category: str = "all",
        today_only: bool = False,
    ) -> list[VideoItem]:
        rid = CATEGORIES.get(category)
        if rid is None:
            raise ValueError(f"未知的 B 站分区 {category!r}，可选：{', '.join(CATEGORIES)}")

        data = await self._get_json(
            "/x/web-interface/ranking/v2", {"rid": rid, "type": "all"}
        )
        items = [_parse_video(entry) for entry in data.get("list", [])]

        if not today_only:
            return take_top(items, limit, RankSource.OFFICIAL_RANKING, category)

        today = local_today()
        picked = [item for item in items if _is_today(item, today)]
        if len(picked) < limit:
            picked.extend(await self._popular_today(limit - len(picked), today, picked))
        picked.sort(key=lambda item: item.stats.play, reverse=True)
        return take_top(picked, limit, RankSource.OFFICIAL_RANKING, category)

    async def _popular_today(
        self, need: int, today: date, already: list[VideoItem]
    ) -> list[VideoItem]:
        """排行榜里当天发布的不够时，用热门接口翻页补齐。

        热门榜时效性强于三日综合排行榜，翻几页通常就能凑够当天的量。
        页数设上限避免在冷启动时段（凌晨）无限翻页。
        """
        seen = {item.video_id for item in already}
        found: list[VideoItem] = []
        max_pages = 5
        for page in range(1, max_pages + 1):
            data = await self._get_json(
                "/x/web-interface/popular", {"ps": 20, "pn": page}
            )
            entries = data.get("list", [])
            if not entries:
                break
            for entry in entries:
                item = _parse_video(entry)
                if item.video_id in seen or not _is_today(item, today):
                    continue
                seen.add(item.video_id)
                found.append(item)
            if len(found) >= need:
                break
            await asyncio.sleep(0.5)
        return found

    async def search_videos(self, keyword: str, limit: int = 5) -> list[VideoItem]:
        """按关键词搜索视频，按综合热度排序。

        搜索接口同样要 WBI 签名和匿名指纹，风控（含 v_voucher 软风控与
        412 硬拦截）统一交给 _get_signed_json 退避重试。匿名成功率不稳定，
        配置 BILI_COOKIE 后基本必成。
        """
        data = await self._get_signed_json(
            "/x/web-interface/wbi/search/type",
            {
                "search_type": "video",
                "keyword": keyword,
                "order": "totalrank",
                "page": 1,
                "page_size": max(limit * 2, 20),
            },
            headers={"Referer": "https://search.bilibili.com/"},
        )
        items = [
            _parse_search_video(entry)
            for entry in data.get("result") or []
            if entry.get("type") == "video"
        ]
        return take_top(items, limit, RankSource.KEYWORD_SEARCH, keyword)

    async def fetch_creator_videos(
        self,
        creator_id: str,
        limit: int = 20,
        since: date | None = None,
    ) -> list[VideoItem]:
        """取创作者投稿，按发布时间倒序。"""
        collected: list[VideoItem] = []
        headers = {
            "Referer": f"https://space.bilibili.com/{creator_id}/video",
            "Origin": "https://space.bilibili.com",
        }

        for page in range(1, _MAX_CREATOR_PAGES + 1):
            data = await self._get_signed_json(
                "/x/space/wbi/arc/search",
                {
                    "mid": creator_id,
                    "ps": _CREATOR_PAGE_SIZE,
                    "pn": page,
                    "order": "pubdate",
                    "platform": "web",
                    "web_location": "1550101",
                },
                headers=headers,
            )
            entries = ((data.get("list") or {}).get("vlist")) or []
            if not entries:
                break

            reached_earlier = False
            for entry in entries:
                item = _parse_creator_video(entry)
                if since is not None and item.publish_time is not None:
                    if item.publish_time.date() < since:
                        reached_earlier = True
                        break
                collected.append(item)
                if len(collected) >= limit:
                    break

            if reached_earlier or len(collected) >= limit:
                break
            if len(entries) < _CREATOR_PAGE_SIZE:
                break
            await asyncio.sleep(0.6)

        return take_top(
            collected, limit, RankSource.CREATOR_TIMELINE, category=creator_id
        )


def _is_today(item: VideoItem, today: date) -> bool:
    return item.publish_time is not None and item.publish_time.date() == today


_EM_TAG = re.compile(r"</?em[^>]*>")


def _parse_search_video(entry: dict[str, Any]) -> VideoItem:
    """解析搜索接口条目。

    字段又是一套独立命名：标题里带 <em> 高亮标签必须剥掉，
    时长是 "MM:SS" 字符串，播放数在 play 顶层。
    """
    bvid = entry.get("bvid") or ""
    pubdate = entry.get("pubdate")

    return VideoItem(
        platform=Platform.BILIBILI,
        video_id=bvid,
        url=f"https://www.bilibili.com/video/{bvid}",
        title=_EM_TAG.sub("", entry.get("title") or ""),
        desc=entry.get("description") or "",
        author_id=str(entry.get("mid") or ""),
        author_name=entry.get("author") or "",
        publish_time=local_datetime_fromtimestamp(pubdate) if pubdate else None,
        duration_sec=_parse_length(entry.get("duration")),
        cover_url=_normalize_cover(entry.get("pic") or ""),
        tags=[t for t in (entry.get("tag") or "").split(",") if t][:5],
        stats=VideoStats(
            play=_as_int(entry.get("play")),
            like=_as_int(entry.get("like")),
            comment=_as_int(entry.get("review")),
            collect=_as_int(entry.get("favorites")),
            danmaku=_as_int(entry.get("video_review")),
        ),
        raw=entry,
    )


def _parse_creator_video(entry: dict[str, Any]) -> VideoItem:
    """解析 space/arc/search 的条目。

    字段与排行榜接口完全不同：时长是 "MM:SS" 字符串而不是秒数，
    统计量平铺在顶层而不是嵌在 stat 里，且不返回点赞和收藏。
    """
    bvid = entry.get("bvid") or ""
    created = entry.get("created")

    return VideoItem(
        platform=Platform.BILIBILI,
        video_id=bvid,
        url=f"https://www.bilibili.com/video/{bvid}",
        title=entry.get("title") or "",
        desc=entry.get("description") or "",
        author_id=str(entry.get("mid") or ""),
        author_name=entry.get("author") or "",
        publish_time=local_datetime_fromtimestamp(created) if created else None,
        duration_sec=_parse_length(entry.get("length")),
        cover_url=_normalize_cover(entry.get("pic") or ""),
        stats=VideoStats(
            play=_as_int(entry.get("play")),
            comment=_as_int(entry.get("comment")),
            danmaku=_as_int(entry.get("video_review")),
        ),
        raw=entry,
    )


def _parse_length(value: Any) -> int:
    """把 "MM:SS" 或 "HH:MM:SS" 转成秒。"""
    if isinstance(value, int):
        return value
    if not isinstance(value, str) or not value.strip():
        return 0
    seconds = 0
    for part in value.strip().split(":"):
        try:
            seconds = seconds * 60 + int(part)
        except ValueError:
            return 0
    return seconds


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _normalize_cover(url: str) -> str:
    return f"https:{url}" if url.startswith("//") else url


def _parse_video(entry: dict[str, Any]) -> VideoItem:
    stat = entry.get("stat") or {}
    owner = entry.get("owner") or {}
    bvid = entry.get("bvid") or ""
    pubdate = entry.get("pubdate")

    tags: list[str] = []
    for key in ("tname", "tname_v2"):
        value = entry.get(key)
        if value and value not in tags:
            tags.append(value)

    return VideoItem(
        platform=Platform.BILIBILI,
        video_id=bvid,
        url=f"https://www.bilibili.com/video/{bvid}",
        title=entry.get("title") or "",
        desc=entry.get("desc") or "",
        author_id=str(owner.get("mid") or ""),
        author_name=owner.get("name") or "",
        publish_time=local_datetime_fromtimestamp(pubdate) if pubdate else None,
        duration_sec=int(entry.get("duration") or 0),
        cover_url=entry.get("pic") or "",
        tags=tags,
        stats=VideoStats(
            play=int(stat.get("view") or 0),
            like=int(stat.get("like") or 0),
            comment=int(stat.get("reply") or 0),
            share=int(stat.get("share") or 0),
            collect=int(stat.get("favorite") or 0),
            danmaku=int(stat.get("danmaku") or 0),
        ),
        raw=entry,
    )
