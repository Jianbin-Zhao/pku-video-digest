"""给抖音找一条不依赖搜索的取视频路径。

背景：抖音的热榜接口稳定可用（不要求登录、频控宽松），
但它只返回热词，不含视频。而搜索接口频控很紧——
连续测十来次就会开始返回 2483，即便账号确实是登录状态。

把榜单能力单独架在搜索上，等于让整个平台受搜索频控摆布。
所以这里逐个试那些「给定热词/话题，返回视频列表」的接口，
找出一条能替代搜索的主路径，把搜索降级成兜底。

热榜里每个热词都带 sentence_id 和 hotlist_param，正是这些接口的入参。
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


def candidates(sentence_id: str, word: str, hotlist_param: str) -> list[tuple[str, str, dict]]:
    return [
        (
            "热点榜聚合视频",
            "/aweme/v1/web/hotspot/aweme/",
            {"sentence_id": sentence_id, "count": 10, "cursor": 0},
        ),
        (
            "热搜词关联视频",
            "/aweme/v1/web/hot/search/video/list/",
            {"sentence_id": sentence_id, "count": 10, "cursor": 0},
        ),
        (
            "话题聚合页视频",
            "/aweme/v1/web/general/search/single/",
            {"keyword": word, "count": 10, "offset": 0, "search_channel": "aweme_video_web"},
        ),
        (
            "榜单详情",
            "/aweme/v1/web/hotspot/detail/",
            {"sentence_id": sentence_id},
        ),
        (
            "推荐流（按热词）",
            "/aweme/v1/web/module/feed/",
            {"module_id": 3003231, "count": 10, "hotlist_param": hotlist_param},
        ),
    ]


def count_awemes(payload: object) -> tuple[int, str]:
    """在响应里找视频列表，返回 (条数, 命中的键路径)。"""
    if not isinstance(payload, dict):
        return 0, ""
    for key in ("aweme_list", "data", "aweme_infos", "items", "video_list"):
        node = payload.get(key)
        if isinstance(node, list) and node:
            first = node[0]
            if isinstance(first, dict) and (
                first.get("aweme_id")
                or first.get("aweme_info")
                or (first.get("aweme_infos"))
            ):
                return len(node), key
    return 0, ""


async def main() -> int:
    configure_stdio()
    load_env()
    async with MediaCrawlerSession(headless=True) as session:
        client = await session.client(Platform.DOUYIN)

        hot = await client.get("/aweme/v1/web/hot/search/list/", {})
        words = ((hot or {}).get("data") or {}).get("word_list") or []
        if not words:
            print("热榜没拿到热词，无法继续。")
            return 1

        top = words[0]
        sentence_id = str(top.get("sentence_id") or "")
        word = str(top.get("word") or "")
        hotlist_param = str(top.get("hotlist_param") or "")
        print(f"用第一个热词做试探：{word}")
        print(f"  sentence_id  = {sentence_id}")
        print(f"  hotlist_param= {hotlist_param[:60]}\n")

        winners: list[str] = []
        for name, uri, params in candidates(sentence_id, word, hotlist_param):
            print(f"=== {name}  {uri} ===")
            try:
                payload = await client.get(uri, dict(params))
            except Exception as exc:  # noqa: BLE001
                print(f"  失败 {type(exc).__name__}: {str(exc)[:120]}\n")
                continue

            status = payload.get("status_code") if isinstance(payload, dict) else None
            count, key = count_awemes(payload)
            print(f"  status_code={status} 顶层键={list(payload)[:8] if isinstance(payload, dict) else '-'}")
            print(f"  视频条数={count}" + (f"（在 {key} 里）" if key else ""))
            if count:
                winners.append(f"{name} {uri}")
                OUT.mkdir(parents=True, exist_ok=True)
                (OUT / f"dy_ep_{uri.strip('/').replace('/', '_')}.json").write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2)[:200_000],
                    encoding="utf-8",
                )
            print()
            await asyncio.sleep(1.5)

        print("=== 结果 ===")
        if winners:
            print("可用的取视频路径：")
            for w in winners:
                print(f"  {w}")
        else:
            print("没有一条能拿到视频，抖音只能继续依赖搜索。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
