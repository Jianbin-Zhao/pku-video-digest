"""排查 B 站搜索接口：直接调 provider 并打印原始响应结构。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vspider.discovery.bilibili import BilibiliRankingProvider  # noqa: E402
from vspider.settings import configure_stdio, load_env  # noqa: E402

KEYWORD = sys.argv[1] if len(sys.argv) > 1 else "人工智能"


async def main() -> None:
    configure_stdio()
    load_env()
    provider = BilibiliRankingProvider()
    try:
        raw = await provider._get_signed_json(  # noqa: SLF001
            "/x/web-interface/wbi/search/type",
            {
                "search_type": "video",
                "keyword": KEYWORD,
                "order": "totalrank",
                "page": 1,
                "page_size": 20,
            },
            headers={"Referer": "https://search.bilibili.com/"},
        )
        print("data keys:", sorted(raw.keys()))
        print("numResults:", raw.get("numResults"), "numPages:", raw.get("numPages"))
        result = raw.get("result")
        print("result 类型:", type(result).__name__, "长度:", len(result or []))
        if result:
            first = result[0]
            print("首条 keys:", sorted(first.keys())[:20])
            print("首条 type:", first.get("type"), "title:", first.get("title", "")[:50])
            types = {}
            for e in result:
                types[e.get("type")] = types.get(e.get("type"), 0) + 1
            print("type 分布:", types)

            from vspider.discovery.bilibili import _parse_search_video

            vids = [e for e in result if e.get("type") == "video"]
            print("type==video 条数:", len(vids))
            parsed = [_parse_search_video(e) for e in vids]
            print("解析成功:", len(parsed), "首条:", parsed[0].title[:40] if parsed else "-")

        items = await provider.search_videos(KEYWORD, limit=5)
        print("search_videos 产出:", len(items))
        for it in items:
            print("  -", it.video_id, it.title[:40], it.stats.play)
    finally:
        await provider.aclose()


asyncio.run(main())
