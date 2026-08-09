"""把快手和微博的完整响应打出来。

前一轮只看了解析后的字段，结果是「快手 result=50、微博 ok=None」——
这两个信息都不足以定位问题。这里直接看原始 JSON。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vspider.mediacrawler.session import MediaCrawlerSession  # noqa: E402
from vspider.models import Platform  # noqa: E402
from vspider.settings import load_env  # noqa: E402


def brief(payload: object, limit: int = 1800) -> str:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    return text if len(text) <= limit else text[:limit] + "\n  …（已截断）"


async def main() -> int:
    load_env()
    async with MediaCrawlerSession(headless=True) as session:
        print("########## 快手 ##########")
        ks = await session.client(Platform.KUAISHOU)
        jar = await session.cookies(Platform.KUAISHOU)
        print(f"cookie 键: {sorted(jar)}\n")

        print("--- 搜索原始响应 ---")
        try:
            raw = await ks.search_info_by_keyword(keyword="搞笑", pcursor="")
            print(brief(raw))
        except Exception as exc:  # noqa: BLE001
            print(f"失败 {type(exc).__name__}: {str(exc)[:300]}")

        print("\n########## 微博 ##########")
        wb = await session.client(Platform.WEIBO)
        page = await session.page(Platform.WEIBO)
        jar = await session.cookies(Platform.WEIBO)
        print(f"页面 URL: {page.url}")
        print(f"cookie 键: {sorted(jar)}\n")

        for name, params in [
            ("热门微博 102803", {"containerid": "102803"}),
            (
                "视频社区",
                {"containerid": "102803_ctg1_4188_-_ctg1_4188"},
            ),
            ("搜索 搞笑", {"containerid": "100103type=1&q=搞笑", "page_type": "searchall"}),
        ]:
            print(f"--- {name} ---")
            try:
                raw = await wb.get("/api/container/getIndex", dict(params))
                print(brief(raw, 1200))
            except Exception as exc:  # noqa: BLE001
                print(f"失败 {type(exc).__name__}: {str(exc)[:300]}")
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
