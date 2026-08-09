"""看快手 V2 签名接口的返回结构。

旧的 GraphQL 搜索已废弃，未签名请求一律返回 result:50。
MediaCrawler 提供了带 __NS_hxfalcon 签名的 V2 方法，
但返回结构和 GraphQL 版不同（feeds 提到了顶层），字段名也要重新核对。
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

OUT = Path(__file__).resolve().parent.parent / "data" / "probe"


def walk(node: object, prefix: str = "", depth: int = 0) -> None:
    if depth > 2 or not isinstance(node, dict):
        return
    for key, value in node.items():
        if isinstance(value, dict):
            print(f"  {prefix}{key}: dict[{len(value)}]")
            walk(value, prefix + "  ", depth + 1)
        elif isinstance(value, list):
            print(f"  {prefix}{key}: list[{len(value)}]")
        else:
            print(f"  {prefix}{key} = {str(value)[:52]}")


async def main() -> int:
    load_env()
    async with MediaCrawlerSession(headless=True) as session:
        client = await session.client(Platform.KUAISHOU)

        print("=== V2 签名搜索 /rest/v/search/feed ===")
        try:
            result = await client.search_info_by_keyword_v2(
                keyword="搞笑", pcursor="1"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"失败 {type(exc).__name__}: {str(exc)[:300]}")
            return 1

        print(f"result={result.get('result')}  顶层键={list(result)}")
        feeds = result.get("feeds") or []
        print(f"feeds {len(feeds)} 条\n")
        if not feeds:
            print(json.dumps(result, ensure_ascii=False, indent=2)[:1500])
            return 1

        print("首条 feed 结构：")
        walk(feeds[0])

        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "ks_v2_feed.json").write_text(
            json.dumps(feeds[0], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n完整样本已写入 {OUT / 'ks_v2_feed.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
