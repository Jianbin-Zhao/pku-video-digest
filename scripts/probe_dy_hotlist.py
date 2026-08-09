"""看抖音热榜 detail_list=1 到底还带不带代表作品。

一级策略依赖热榜每个热词自带的 aweme_infos。若这个字段消失了，
一级策略就永远产出 0 条、只能降级到会被 2483 拦的搜索。
这里把结构打出来确认。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vspider.mediacrawler.session import MediaCrawlerSession  # noqa: E402
from vspider.models import Platform  # noqa: E402
from vspider.settings import configure_stdio, load_env  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "data" / "probe"


async def main() -> int:
    configure_stdio()
    load_env()
    async with MediaCrawlerSession(headless=True) as session:
        client = await session.client(Platform.DOUYIN)

        for params in ({"detail_list": 1}, {}):
            print(f"\n=== get hot list params={params} ===")
            payload = await client.get("/aweme/v1/web/hot/search/list/", params)
            words = ((payload or {}).get("data") or {}).get("word_list") or []
            print(f"  word_list: {len(words)} 条")
            with_aweme = sum(1 for w in words if w.get("aweme_infos"))
            print(f"  带 aweme_infos 的热词: {with_aweme}")
            if words:
                w0 = words[0]
                print(f"  首词键: {list(w0)}")
                print(f"  首词 word: {w0.get('word')}")
                infos = w0.get("aweme_infos") or []
                print(f"  首词 aweme_infos: {len(infos)}")
                if infos:
                    OUT.mkdir(parents=True, exist_ok=True)
                    (OUT / "dy_hotlist_word0.json").write_text(
                        json.dumps(w0, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    print("  已存 data/probe/dy_hotlist_word0.json")

        # 另一个候选：抖音热点榜的 sub-endpoint，有的版本用 hotsearch/aweme
        print("\n=== 尝试 /aweme/v1/web/hot/search/video/list/ ===")
        try:
            alt = await client.get("/aweme/v1/web/hot/search/video/list/", {})
            print(f"  顶层键: {list(alt or {})}  status={alt.get('status_code')}")
        except Exception as exc:  # noqa: BLE001
            print(f"  失败 {type(exc).__name__}: {str(exc)[:160]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
