"""诊断快手签名环境:页面加载成什么样、__ks_realm 是否出现。

2026-08-09 凌晨快手所有签名接口(搜索/作品列表)突然全部超时,
下午还是好的。此脚本打开会话页面,输出 URL/标题/登录 cookie/签名环境状态,
用来区分「风控拦截页」「登录失效」「页面改版」三种可能。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vspider.mediacrawler.session import MediaCrawlerSession  # noqa: E402
from vspider.models import Platform  # noqa: E402
from vspider.settings import configure_stdio, load_env  # noqa: E402


async def main() -> int:
    configure_stdio()
    load_env()
    show = "--show-browser" in sys.argv

    async with MediaCrawlerSession(headless=not show) as session:
        client = await session.client(Platform.KUAISHOU)
        page = client.playwright_page
        print("页面 URL:", page.url)
        try:
            print("页面标题:", await page.title())
        except Exception as exc:  # noqa: BLE001
            print("取标题失败:", exc)

        cookies = await page.context.cookies("https://www.kuaishou.com")
        names = {c["name"] for c in cookies}
        print("登录 cookie:", sorted(names & {"passToken", "userId", "kuaishou.server.web_st"}))

        for i in range(6):
            has = await page.evaluate("() => !!window.__ks_realm")
            print(f"  [{i * 5}s] __ks_realm = {has}")
            if has:
                break
            await asyncio.sleep(5)

        # 看看页面上有没有验证码/风控的明显痕迹
        body_text = await page.evaluate(
            "() => (document.body && document.body.innerText || '').slice(0, 300)"
        )
        print("页面文本前 300 字：")
        print(body_text)

        # 顺带试一次刷新后再等
        if not await page.evaluate("() => !!window.__ks_realm"):
            print("刷新页面重试...")
            await page.reload(wait_until="domcontentloaded")
            await asyncio.sleep(10)
            print("刷新后 __ks_realm =", await page.evaluate("() => !!window.__ks_realm"))
            print("刷新后 URL:", page.url)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
