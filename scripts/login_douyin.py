"""抖音扫码登录（可靠版）。

为什么不用通用的 login.py：它靠 pong 判断登录，而抖音匿名态 pong 也返回 True
（sessionid 缺失但 pong=True，诊断见 scripts/probe_dy_auth.py），会误判成「已登录」
直接退出，根本不给扫码的机会。

这里只认真正的身份 cookie——sessionid / sessionid_ss / sid_tt，
它们只有在真正登录后才会下发。轮询到任意一个即视为成功，
再多等几秒让平台把风控 cookie 补齐，然后落盘到持久化目录 .browser。

用法：
    python scripts/login_douyin.py            # 默认等 300 秒
    python scripts/login_douyin.py --timeout 420
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vspider.mediacrawler.session import (  # noqa: E402
    DEFAULT_BROWSER_DATA,
    MediaCrawlerSession,
)
from vspider.models import Platform  # noqa: E402
from vspider.settings import configure_stdio, load_env  # noqa: E402

# 只有真正登录后才会出现的身份 cookie。匿名会话不会有。
IDENTITY_KEYS = ("sessionid", "sessionid_ss", "sid_tt")
POLL_SECONDS = 3


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=300, help="等待扫码的秒数")
    args = parser.parse_args()

    configure_stdio()
    load_env()

    print(f"登录态将保存到：{DEFAULT_BROWSER_DATA}")
    print("即将打开抖音，请在弹出的浏览器窗口里扫码登录。\n")

    # headless=False：必须有可见窗口给人扫码。
    async with MediaCrawlerSession(headless=False) as session:
        page = await session.page(Platform.DOUYIN)

        jar = await session.cookies(Platform.DOUYIN)
        if any(jar.get(k) for k in IDENTITY_KEYS):
            print("检测到已有登录态（存在身份 cookie），无需重复登录。")
            print("若要换号：先在窗口里退出登录，再重跑本脚本。")
            return 0

        print(f"浏览器已打开：{page.url}")
        print("请点击右上角登录并扫码。登录成功后会自动继续，")
        print(f"最多等待 {args.timeout} 秒。\n")

        waited = 0
        while waited < args.timeout:
            await asyncio.sleep(POLL_SECONDS)
            waited += POLL_SECONDS
            jar = await session.cookies(Platform.DOUYIN)
            hit = [k for k in IDENTITY_KEYS if jar.get(k)]
            if hit:
                # 多等几秒：抖音写入 sessionid 后还会补发一批风控 cookie，
                # 太早关窗会丢，导致下次请求仍被判未登录。
                await asyncio.sleep(6)
                jar = await session.cookies(Platform.DOUYIN)
                print(f"\n登录成功！命中身份 cookie {hit}，共保存 {len(jar)} 项。")
                print("现在可以关掉窗口了。登录态已持久化，后续无头运行会自动复用。")
                return 0
            if waited % 15 == 0:
                print(f"  仍在等待扫码……（{waited}/{args.timeout} 秒）")

        print("\n等待超时，未检测到身份 cookie。请重试，或改用贴 Cookie 的方式。")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
