"""看小红书详情接口(feed)的原始返回,定位 _hydrate 提不出直链的原因。

fetch_local 实测:搜索能拿到视频笔记,但 _hydrate 拉详情后 extract_video_url
仍返回空。这里把详情的原始 JSON 落盘,看 video 字段到底长什么样。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vspider.discovery.xhs import extract_video_url  # noqa: E402
from vspider.mediacrawler.session import MediaCrawlerSession  # noqa: E402
from vspider.models import Platform  # noqa: E402
from vspider.settings import configure_stdio, load_env  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "data" / "probe"


async def main() -> int:
    configure_stdio()
    load_env()
    async with MediaCrawlerSession(headless=True) as session:
        client = await session.client(Platform.XHS)

        raw = await client.get_note_by_keyword(keyword="美食")
        items = (raw or {}).get("items") or []
        video_entry = None
        for entry in items:
            if (entry.get("note_card") or {}).get("type") == "video":
                video_entry = entry
                break
        if video_entry is None:
            print("本页没有视频笔记，换个词再试")
            return 1

        note_id = str(video_entry.get("id"))
        xsec_token = video_entry.get("xsec_token") or ""
        print(f"目标笔记 {note_id}，xsec_token {'有' if xsec_token else '无'}")

        detail = await client.get_note_by_id(
            note_id=note_id, xsec_source="pc_search", xsec_token=xsec_token
        )
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "xhs_detail.json").write_text(
            json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"详情顶层键: {list(detail or {})}")
        if not detail:
            print("详情返回空 dict —— 接口被风控或 token 失效")
            return 1

        video = detail.get("video") or {}
        print(f"type = {detail.get('type')}")
        print(f"video 键: {list(video)}")
        consumer = video.get("consumer") or {}
        print(f"consumer 键: {list(consumer)}")
        media = video.get("media") or {}
        stream = (media.get("stream") or {})
        print(f"stream 键: {list(stream)}")

        url = extract_video_url(detail)
        print(f"\nextract_video_url -> {url[:120] if url else '(空)'}")
        print("原始 JSON 已存 data/probe/xhs_detail.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
