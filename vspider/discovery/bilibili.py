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
from datetime import date, datetime
from typing import Any

import httpx

from vspider.discovery.base import RankingProvider, take_top
from vspider.discovery.wbi import WbiSigner, bootstrap_cookies, ensure_bili_ticket
from vspider.models import Platform, RankSource, VideoItem, VideoStats

API_BASE = "https://api.bilibili.com"

_CREATOR_PAGE_SIZE = 30
# 翻页上限。配合 order=pubdate 与 since 提前终止，正常场景一两页就够，
# 这个上限只是防止参数写错时无限翻页。
_MAX_CREATOR_PAGES = 5

# 风控相关的业务错误码。-352 是风控校验失败，-509 是请求过于频繁。
_RISK_CODES = {-352, -509}
_RISK_RETRIES = 5
_RISK_BACKOFF_BASE = 2.0

# ranking/v2 接受的主分区 id。B 站 2024 年改版了分区体系，但该接口仍兼容旧 rid。
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
        # 有登录态时不去动匿名身份，SESSDATA 本身就是最稳的通行证。
        self._has_login = "SESSDATA" in cookie
        # trust_env=False 是刻意的：本机装了用于访问 GitHub/PyPI 的代理，
        # 若让 httpx 继承 HTTP(S)_PROXY，对 B 站的请求会绕道境外出口，
        # 既慢又更容易被风控。国内平台一律直连。
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
        """访问需要 WBI 签名且受风控保护的接口，失败自动退避重试。

        实测（见 docs/EXPERIMENTS.md E6）未登录访问 space 接口的成功率只有
        0%~50% 且在配置之间不可复现，主导变量是按 IP 的请求频率而非指纹参数。
        单次请求必然不可靠，因此这里必须重试。

        每次重试都重新签名（wts 会变）并换一份匿名身份，
        因为触发风控之后同一个 buvid 会被短暂拉黑，沿用它重试只是白等。
        """
        last_error = ""
        for attempt in range(_RISK_RETRIES):
            if attempt:
                # 退避从 2 秒起翻倍。实测 412 后的封禁窗口在秒级，
                # 退避到第三次时通常已经放行。
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
            return payload.get("data") or {}

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
            # 领不到票不该直接失败，没有它也有一定概率通过。
            pass

    async def _reset_identity(self) -> None:
        """丢掉当前匿名身份，下次请求会重新申领。

        已登录时不能动 cookie，否则会把登录态一起清掉，
        而登录态本身就是最稳的通行证。
        """
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

        today = date.today()
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

    async def fetch_creator_videos(
        self,
        creator_id: str,
        limit: int = 20,
        since: date | None = None,
    ) -> list[VideoItem]:
        """取创作者投稿，按发布时间倒序。

        对应题面第二个场景。接口需要 WBI 签名和匿名 buvid 指纹，
        两者都在这里就地处理。
        """
        collected: list[VideoItem] = []
        # Referer 必须指向对应的空间页，风控会核对来源。
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
                    # 按发布时间倒序，这样 since 过滤一旦遇到更早的就能立刻停，
                    # 不必把整个投稿列表翻完。
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
            # 连续翻页容易触发风控，页间留出间隔。
            await asyncio.sleep(0.6)

        return take_top(
            collected, limit, RankSource.CREATOR_TIMELINE, category=creator_id
        )


def _is_today(item: VideoItem, today: date) -> bool:
    return item.publish_time is not None and item.publish_time.date() == today


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
        publish_time=datetime.fromtimestamp(created) if created else None,
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
    # play 在稿件被设为隐藏播放量时会返回字符串 "--"。
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _normalize_cover(url: str) -> str:
    # 这个接口返回的封面是协议相对 URL（//i2.hdslb.com/...），补上协议才能直接用。
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
        publish_time=datetime.fromtimestamp(pubdate) if pubdate else None,
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
