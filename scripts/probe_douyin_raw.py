"""直接打抖音的原始接口，看返回长什么样。

榜单发现拿不到数据时，先用这个把「请求失败」和「解析失败」区分开——
前者是签名/风控问题，后者是字段映射问题，排查方向完全不同。

已确认的结论（2026-08-07）：
  - 热榜 /hot/search/list/ 匿名可用，签名正常。
  - 但它**不再返回样例视频**，加 detail_list=1 也没有 aweme_infos，
    只有热词本身。所以拿视频必须再走一步搜索。
  - /hotspot/rank/ 返回 Unsupported path，接口已下线。
  - /tab/feed/ 推荐流需要登录，匿名直接 account blocked。
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

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "probe"


def dump(name: str, payload: object) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{name}.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2)[:400_000], encoding="utf-8"
    )
    print(f"    已存 {path.relative_to(OUT_DIR.parent.parent)}")


async def main() -> int:
    load_env()
    async with MediaCrawlerSession(headless=True) as session:
        client = await session.client(Platform.DOUYIN)

        print("=== 1. 热榜结构 ===")
        hot = await client.get("/aweme/v1/web/hot/search/list/", {"detail_list": 1})
        data = hot.get("data") or {}
        words = data.get("word_list") or []
        trending = data.get("trending_list") or []
        print(f"  热词 {len(words)} 条，trending_list {len(trending)} 条")
        if words:
            print(f"  热词样例: {[w.get('word') for w in words[:5]]}")
        if trending:
            print(f"  trending_list[0] 字段: {list(trending[0])}")
            dump("dy_trending_list", trending[:3])

        print("\n=== 2. 搜索能否匿名使用 ===")
        keyword = words[0].get("word") if words else "热点"
        print(f"  用热词搜索：{keyword}")
        try:
            result = await client.search_info_by_keyword(keyword=keyword, offset=0)
        except Exception as exc:  # noqa: BLE001
            print(f"  搜索失败 {type(exc).__name__}: {str(exc)[:200]}")
            print("  → 搜索需要登录，二级策略也走不通，只能配 Cookie")
            return 1

        entries = result.get("data") or []
        print(f"  status_code={result.get('status_code')} 返回 {len(entries)} 条")
        if not entries:
            print(f"  顶层键: {list(result)}")
            dump("dy_search_empty", result)
            return 1

        kinds: dict[str, int] = {}
        for entry in entries:
            kinds[str(entry.get("type"))] = kinds.get(str(entry.get("type")), 0) + 1
        print(f"  条目 type 分布: {kinds}")
        print(f"  首条字段: {list(entries[0])}")

        aweme = entries[0].get("aweme_info")
        if aweme:
            stats = aweme.get("statistics") or {}
            video = aweme.get("video") or {}
            print("\n  首条视频字段核对：")
            print(f"    aweme_id     {aweme.get('aweme_id')}")
            print(f"    desc         {(aweme.get('desc') or '')[:40]}")
            print(f"    create_time  {aweme.get('create_time')}")
            print(f"    author       {(aweme.get('author') or {}).get('nickname')}")
            print(f"    duration(ms) {video.get('duration')}")
            print(f"    统计         {dict(list(stats.items())[:6])}")
            has_play = bool((video.get("play_addr") or {}).get("url_list"))
            print(f"    play_addr    {'有' if has_play else '无'}")
            dump("dy_search_aweme", aweme)
        else:
            print(f"  首条没有 aweme_info，实际结构: {list(entries[0])}")
            dump("dy_search_entry", entries[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
