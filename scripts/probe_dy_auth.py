"""诊断抖音登录态。

抖音的搜索接口在未登录时返回 status_code=2483，但热榜依然可用，
所以「部分能用」是正常现象，不能据此判断整体状态。
这里把身份相关的证据全列出来，定位到底缺什么。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vspider.mediacrawler.session import SPECS, MediaCrawlerSession  # noqa: E402
from vspider.models import Platform  # noqa: E402
from vspider.settings import configure_stdio, load_env  # noqa: E402

# 抖音的登录态主要看这几个。sessionid 是核心会话票据。
IDENTITY_KEYS = ("sessionid", "sessionid_ss", "sid_tt", "sid_guard", "uid_tt", "odin_tt")


async def main() -> int:
    configure_stdio()
    load_env()
    async with MediaCrawlerSession(headless=True) as session:
        client = await session.client(Platform.DOUYIN)
        page = await session.page(Platform.DOUYIN)
        jar = await session.cookies(Platform.DOUYIN)

        print(f"页面 URL: {page.url}")
        print(f"cookie 总数: {len(jar)}\n")

        print("=== 身份 cookie ===")
        for key in IDENTITY_KEYS:
            value = jar.get(key, "")
            print(f"  {key:<14} {'有' if value else '缺失'}")

        storage = await session.evaluate(page, "() => window.localStorage")
        print("\n=== localStorage 里的登录标记 ===")
        print(f"  HasUserLogin = {(storage or {}).get('HasUserLogin', '未设置')}")
        print(f"  LOGIN_STATUS(cookie) = {jar.get('LOGIN_STATUS', '未设置')}")

        print("\n=== 官方 pong ===")
        try:
            print(f"  pong = {await client.pong(browser_context=session._context)}")
        except Exception as exc:  # noqa: BLE001
            print(f"  失败 {type(exc).__name__}: {str(exc)[:120]}")

        print("\n=== 实际能力 ===")
        hot = await client.get("/aweme/v1/web/hot/search/list/", {})
        words = ((hot or {}).get("data") or {}).get("word_list") or []
        print(f"  热榜（不需登录）: {len(words)} 条热词")

        keyword = words[0].get("word") if words else "热点"
        search = await client.search_info_by_keyword(keyword=keyword, offset=0)
        code = search.get("status_code")
        print(f"  搜索（需要登录）: status_code={code} {search.get('status_msg', '')}")
        print(f"                    返回 {len(search.get('data') or [])} 条")

        if code == 2483:
            spec = SPECS[Platform.DOUYIN]
            print("\n判断：搜索要求登录，当前会话不被认可。")
            print("  重新登录：python scripts/login.py dy --timeout 420")
            print(f"  或在 .env 里配 {spec.cookie_env}（从浏览器复制完整 Cookie）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
