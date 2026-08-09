"""摸清各平台在**匿名**状态下究竟能做什么。

目的是把「必须登录」和「可以匿名」划清楚，避免两种浪费：
既不想为了能匿名的平台白白去扫码，也不想写完一整套采集逻辑
才发现接口根本不让匿名调。

抖音已经测过：热榜匿名可用，搜索返回 2483 要求登录。
这里补齐快手和微博。
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

# 微博的榜单都挂在 containerid 上，不同 id 对应不同榜。
WEIBO_CONTAINERS = [
    ("热搜榜", "106003type=25&t=3&disable_hot=1&show_type=1"),
    ("视频社区", "102803_ctg1_4188_-_ctg1_4188"),
    ("热门微博", "102803"),
]


def dump(name: str, payload: object) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"{name}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2)[:300_000], encoding="utf-8"
    )


async def probe_kuaishou(session: MediaCrawlerSession) -> None:
    print("\n===== 快手 =====")
    client = await session.client(Platform.KUAISHOU)
    print(f"  cookie {len(client.cookie_dict or {})} 项")

    print("  [1] 关键词搜索")
    try:
        result = await client.search_info_by_keyword(keyword="搞笑", pcursor="")
        data = (result or {}).get("visionSearchPhoto") or {}
        feeds = data.get("feeds") or []
        print(f"      返回 {len(feeds)} 条  result={data.get('result')}")
        if feeds:
            photo = feeds[0].get("photo") or {}
            author = feeds[0].get("author") or {}
            print(f"      首条: {(photo.get('caption') or '')[:36]}")
            print(f"      作者 {author.get('name')}  时长 {photo.get('duration')}ms")
            print(f"      直链 {'有' if photo.get('photoUrl') else '无'}")
            dump("ks_search_feed", feeds[0])
        else:
            print(f"      空结果，顶层键：{list(result or {})}")
            dump("ks_search_empty", result)
    except Exception as exc:  # noqa: BLE001
        print(f"      失败 {type(exc).__name__}: {str(exc)[:150]}")


async def probe_weibo(session: MediaCrawlerSession) -> None:
    print("\n===== 微博 =====")
    client = await session.client(Platform.WEIBO)
    print(f"  cookie {len(client.cookie_dict or {})} 项")

    for name, containerid in WEIBO_CONTAINERS:
        print(f"  [{name}] containerid={containerid[:40]}")
        try:
            payload = await client.get(
                "/api/container/getIndex", {"containerid": containerid}
            )
        except Exception as exc:  # noqa: BLE001
            print(f"      失败 {type(exc).__name__}: {str(exc)[:130]}")
            continue

        data = (payload or {}).get("data") or {}
        cards = data.get("cards") or []
        print(f"      ok={payload.get('ok')}  cards={len(cards)}")
        if not cards:
            continue

        # 微博的 card 是嵌套结构，视频可能藏在 card_group 里。
        kinds: dict[str, int] = {}
        with_video = 0
        for card in cards:
            kinds[str(card.get("card_type"))] = (
                kinds.get(str(card.get("card_type")), 0) + 1
            )
            blog = card.get("mblog") or {}
            if blog.get("page_info", {}).get("type") == "video":
                with_video += 1
        print(f"      card_type 分布 {kinds}  含视频 {with_video} 条")
        dump(f"wb_{containerid[:16].replace('=', '_').replace('&', '_')}", cards[:3])

    print("  [关键词搜索]")
    try:
        result = await client.get_note_by_keyword(keyword="搞笑")
        cards = ((result or {}).get("data") or {}).get("cards") or []
        print(f"      返回 {len(cards)} 条")
        if cards:
            dump("wb_search", cards[:3])
    except Exception as exc:  # noqa: BLE001
        print(f"      失败 {type(exc).__name__}: {str(exc)[:150]}")


async def main() -> int:
    load_env()
    async with MediaCrawlerSession(headless=True) as session:
        await probe_kuaishou(session)
        await probe_weibo(session)
    print("\n结论看上面每一项是「返回 N 条」还是「失败/空」。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
