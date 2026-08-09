"""看小红书搜索的原始返回和登录态。

小红书的失败模式很隐蔽：未登录时接口往往返回 success=true 但 items 为空，
不报错、不给提示，看起来就像「这个关键词没有内容」。
所以必须先用官方的 pong 明确确认登录态，再看数据。
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
        client = await session.client(Platform.XHS)
        page = await session.page(Platform.XHS)
        jar = await session.cookies(Platform.XHS)

        print(f"页面 URL: {page.url}")
        print(f"cookie 键: {sorted(jar)}")
        print(f"web_session 是否存在: {bool(jar.get('web_session'))}\n")

        print("=== 官方登录态检查 pong ===")
        try:
            print(f"  pong = {await client.pong()}")
        except Exception as exc:  # noqa: BLE001
            print(f"  失败 {type(exc).__name__}: {str(exc)[:200]}")

        print("\n=== 搜索原始返回 ===")
        try:
            raw = await client.get_note_by_keyword(keyword="美食")
        except Exception as exc:  # noqa: BLE001
            print(f"  失败 {type(exc).__name__}: {str(exc)[:300]}")
            return 1

        print(f"  顶层键: {list(raw or {})}")
        items = (raw or {}).get("items") or []
        print(f"  items {len(items)} 条")
        if not items:
            print(json.dumps(raw, ensure_ascii=False, indent=2)[:1200])
            return 1

        kinds: dict[str, int] = {}
        for entry in items:
            note = entry.get("note_card") or {}
            kinds[str(note.get("type"))] = kinds.get(str(note.get("type")), 0) + 1
        print(f"  笔记 type 分布: {kinds}")
        print(f"  首条外层键: {list(items[0])}")

        note = items[0].get("note_card") or {}
        print(f"  note_card 键: {list(note)}")
        print(f"    type        {note.get('type')}")
        print(f"    display_title {(note.get('display_title') or '')[:36]}")
        print(f"    user        {(note.get('user') or {}).get('nickname')}")
        print(f"    interact    {note.get('interact_info')}")
        print(f"    有 video 字段 {'video' in note}")
        print(f"    xsec_token  {'有' if items[0].get('xsec_token') else '无'}")

        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "xhs_search_item.json").write_text(
            json.dumps(items[0], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # 视频笔记比图文少，单独抓一条出来核对字段。
        for entry in items:
            card = entry.get("note_card") or {}
            if card.get("type") == "video":
                (OUT / "xhs_video_item.json").write_text(
                    json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print("\n  已存一条视频笔记样本 xhs_video_item.json")
                print(f"    video 键: {list(card.get('video') or {})}")
                break
        else:
            print("\n  [!] 本页没有视频笔记，全是图文")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
