"""小红书榜单发现。

五个平台里小红书的榜单能力最弱：**没有任何公开榜单接口**，
连抖音那种热搜词榜都没有。所以只能走一条路——
用热词搜索再按互动量重排，结果一律标注 hot_keyword_rerank。

另有两个小红书特有的坑：

1. **xsec_token 必须一路带着。** 小红书的笔记链接不带这个 token 就打不开，
   详情接口也会拒绝。它只在搜索结果里出现一次，丢了就得重新搜。
2. **笔记分图文和视频两种。** 搜索结果里图文占多数，
   必须按 type == "video" 筛，否则后面下载环节会大面积失败。
"""

from __future__ import annotations

import asyncio
import re
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
from vspider.settings import local_datetime_fromtimestamp, local_today

# 小红书没有公开热搜接口，使用固定种子词。
_SEED_KEYWORDS = (
    "热门",
    "vlog",
    "美食",
    "穿搭",
    "旅行",
    "好物",
)
_COUNT_PATTERN = re.compile(r"([\d.]+)\s*([万亿]?)")


class XhsRankingProvider(RankingProvider):
    platform = Platform.XHS

    def __init__(self, session: MediaCrawlerSession) -> None:
        self._session = session

    async def _client(self) -> Any:
        return await self._session.client(Platform.XHS)

    async def fetch_ranking(
        self,
        limit: int = 5,
        category: str = "all",
        today_only: bool = False,
    ) -> list[VideoItem]:
        client = await self._client()

        async def search(keyword: str) -> list[dict[str, Any]]:
            payload = await client.get_note_by_keyword(keyword=keyword)
            # 空列表可能是静默风控。
            if payload and payload.get("success") is False:
                raise KeywordRejected(payload.get("msg", "success=false"))
            return list((payload or {}).get("items") or [])

        items = await collect_by_keywords(
            keywords=list(_SEED_KEYWORDS),
            search=search,
            parse=_parse_note,
            limit=limit,
            platform_name="小红书",
            budget=KeywordBudget(delay_sec=1.5),
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
        """按关键词搜索视频笔记，按互动量排序（场景三）。"""
        client = await self._client()
        try:
            payload = await client.get_note_by_keyword(keyword=keyword)
        except Exception as exc:  # noqa: BLE001 - 统一翻译平台侧拒绝
            raise RuntimeError(_translate_reject(exc)) from exc
        if payload and payload.get("success") is False:
            raise RuntimeError(f"小红书搜索被拒：{payload.get('msg', 'success=false')}")
        items = [
            item
            for item in (
                _parse_note(node) for node in (payload or {}).get("items") or []
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
        """取创作者的视频笔记。creator_id 为小红书 user_id，也可传主页链接。"""
        client = await self._client()
        user_id = _extract_user_id(creator_id)

        collected: list[VideoItem] = []
        cursor = ""
        for _ in range(5):
            payload = await client.get_notes_by_creator(user_id, cursor)
            notes = (payload or {}).get("notes") or []
            if not notes:
                break

            stop = False
            for note in notes:
                item = _parse_note(note)
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

            cursor = (payload or {}).get("cursor") or ""
            if stop or not cursor or not (payload or {}).get("has_more"):
                break
            await asyncio.sleep(1.2)

        return take_top(
            collected, limit, RankSource.CREATOR_TIMELINE, category=creator_id
        )


def _translate_reject(exc: Exception) -> str:
    """把 MediaCrawler 抛出的重试链翻译成能指导行动的中文。

    tenacity.RetryError 包着真正的 DataFetchError，直接往上抛用户只能看到
    "RetryError[<Future ...>]"，完全不知道发生了什么。
    """
    cause: BaseException = exc
    last_attempt = getattr(exc, "last_attempt", None)
    if last_attempt is not None and last_attempt.exception() is not None:
        cause = last_attempt.exception()
    text = str(cause)
    if "没有权限" in text:
        return (
            "小红书拒绝了本账号的数据请求（账号被临时风控，常见于当天"
            "抓取频繁或刚重新登录）。请过几小时再试，或换一个账号登录。"
        )
    return f"小红书接口出错：{text}"


def _parse_note(node: dict[str, Any]) -> VideoItem | None:
    """把小红书的搜索/列表条目映射成统一模型。

    搜索结果外面包了一层，真正的笔记在 note_card 里；
    创作者列表则是平铺的。两种都要认。
    """
    note = node.get("note_card") or node.get("note") or node
    note_id = str(node.get("id") or note.get("note_id") or "")
    if not note_id:
        return None

    # 只处理视频笔记。
    if note.get("type") != "video":
        return None

    user = note.get("user") or {}
    interact = note.get("interact_info") or {}
    xsec_token = node.get("xsec_token") or note.get("xsec_token") or ""

    published = note.get("time") or note.get("create_time")
    publish_time = None
    if isinstance(published, (int, float)) and published > 0:
        publish_time = local_datetime_fromtimestamp(published / 1000)
    elif len(note_id) >= 8:
        # 创作者列表缺 time 时从 note_id 解析 Unix 秒。
        try:
            note_timestamp = int(note_id[:8], 16)
            if 1_500_000_000 <= note_timestamp <= 4_102_444_800:
                publish_time = local_datetime_fromtimestamp(note_timestamp)
        except ValueError:
            pass

    video = note.get("video") or {}
    capa = video.get("capa") or {}

    title = (note.get("display_title") or note.get("title") or "").strip()
    desc = (note.get("desc") or "").strip()
    if not title and not desc:
        # 搜索结果可能没有标题，退回话题标签。
        topics = [
            tag.get("name", "")
            for tag in (note.get("tag_list") or [])
            if tag.get("name")
        ]
        title = " ".join(topics[:3]) or f"（无标题视频笔记 {note_id[:8]}）"

    return VideoItem(
        platform=Platform.XHS,
        video_id=note_id,
        url=(
            f"https://www.xiaohongshu.com/explore/{note_id}"
            f"?xsec_token={xsec_token}&xsec_source=pc_search"
        ),
        title=title or desc[:60],
        desc=desc,
        author_id=str(user.get("user_id") or ""),
        author_name=user.get("nickname") or user.get("nick_name") or "",
        publish_time=publish_time,
        duration_sec=int(capa.get("duration") or 0),
        cover_url=_cover_url(note),
        tags=[
            tag.get("name", "")
            for tag in (note.get("tag_list") or [])
            if tag.get("type") == "topic" and tag.get("name")
        ],
        stats=VideoStats(
            like=_parse_count(interact.get("liked_count")),
            comment=_parse_count(interact.get("comment_count")),
            share=_parse_count(interact.get("share_count")),
            collect=_parse_count(interact.get("collected_count")),
        ),
        raw={**note, "xsec_token": xsec_token},
    )


def extract_video_url(note: dict[str, Any]) -> str:
    """取小红书视频直链。

    优先用 origin_video_key 拼无水印地址（2026-08 实测详情接口已不再返回
    consumer 字段，此路留作兼容）。取不到则遍历 media.stream 下的全部流：
    旧版按编码名分键（h264 / h265），2026 改版后换成了档位名
    （EF4=X264、EF5=X265，EF6/EF7 常为空），所以不能只认 h264 一个键。
    选取顺序：H.264 优先（下载后 ffmpeg/OCR 兼容性最好）、default_stream 次之；
    master_url 缺失时退回 backup_urls 的第一个。
    """
    if note.get("type") != "video":
        return ""
    video = note.get("video") or {}

    consumer = video.get("consumer") or {}
    key = consumer.get("origin_video_key") or consumer.get("originVideoKey") or ""
    if key:
        return f"http://sns-video-bd.xhscdn.com/{key}"

    stream_map = (video.get("media") or {}).get("stream") or {}
    best: tuple[int, int, str] | None = None
    for codec_key, entries in stream_map.items():
        for entry in entries or []:
            url = entry.get("master_url") or next(
                iter(entry.get("backup_urls") or []), ""
            )
            if not url:
                continue
            desc = f"{entry.get('stream_desc') or ''}|{codec_key}".lower()
            is_h264 = "264" in desc or codec_key in ("h264", "EF4")
            rank = (
                0 if is_h264 else 1,
                0 if entry.get("default_stream") else 1,
            )
            if best is None or rank < best[:2]:
                best = (*rank, str(url))
    return best[2] if best else ""


def _cover_url(note: dict[str, Any]) -> str:
    cover = note.get("cover") or {}
    if isinstance(cover, dict):
        return cover.get("url_default") or cover.get("url") or ""
    images = note.get("image_list") or []
    if images and isinstance(images[0], dict):
        return images[0].get("url_default") or images[0].get("url") or ""
    return ""


def _parse_count(value: Any) -> int:
    """解析互动数。

    小红书这里返回的不一定是数字：常见 "1.2万"、"10万+" 这类展示用字符串，
    直接 int() 会抛异常，整条数据就废了。
    """
    if isinstance(value, (int, float)):
        return int(value)
    if not value:
        return 0

    match = _COUNT_PATTERN.search(str(value))
    if not match:
        return 0
    number, unit = match.groups()
    try:
        scale = {"万": 10_000, "亿": 100_000_000}.get(unit, 1)
        return int(float(number) * scale)
    except ValueError:
        return 0


def _extract_user_id(creator_id: str) -> str:
    if not creator_id.startswith("http"):
        return creator_id
    return creator_id.rstrip("/").split("/")[-1].split("?")[0]
