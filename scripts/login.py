"""登录浏览器平台并保存会话。"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vspider.mediacrawler.session import (  # noqa: E402
    DEFAULT_BROWSER_DATA,
    SPECS,
    MediaCrawlerSession,
)
from vspider.models import Platform  # noqa: E402
from vspider.settings import load_env  # noqa: E402

POLL_SECONDS = 3

# force 只清理目标平台 Cookie。
_COOKIE_DOMAINS: dict[str, str] = {
    "dy": r".*douyin\.com",
    "ks": r".*kuaishou\.com",
    "wb": r".*(weibo\.com|weibo\.cn|sina\.com\.cn)",
    "xhs": r".*xiaohongshu\.com",
}


async def show_status() -> None:
    """显示登录提示，最终状态以真实请求为准。"""
    print(f"登录态目录：{DEFAULT_BROWSER_DATA}")
    if not DEFAULT_BROWSER_DATA.exists():
        print("尚未创建，说明还没有登录过任何平台。")
        return

    async with MediaCrawlerSession(headless=True) as session:
        for platform in SPECS:
            try:
                state, reason = await session.login_hint(platform)
                jar = await session.cookies(platform)
                label = {True: "已登录", False: "未登录", None: "不确定"}[state]
                print(
                    f"  {platform.value:<4} {label:<6} cookie {len(jar):>2} 项"
                    f"   依据：{reason}"
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  {platform.value:<4} 检查失败：{str(exc)[:70]}")

    print("\n以上只是提示。要确认平台是否真的可用，跑：")
    print("  python scripts/verify_all.py")


async def login(platform: Platform, timeout: int, force: bool = False) -> bool:
    spec = SPECS[platform]
    print(f"\n=== 登录 {platform.value} ===")
    if spec.login_required_for:
        print(f"该平台以下能力需要登录：{'、'.join(spec.login_required_for)}")

    async with MediaCrawlerSession(headless=False) as session:
        page = await session.page(platform)
        # force 用于「已登录但账号被风控」或「部分接口 token 过期」的场景。
        if not force and await session.is_logged_in(platform):
            print("检测到已是登录状态，无需重复登录（要换号请加 --force）。")
            return True
        if force:
            # 必须先清掉旧 cookie：登录探测只看粗粒度信号（如 pong），
            # 半失效的旧 cookie 会让轮询立即误报「已登录」，扫码根本没等到。
            import re as _re

            pattern = _COOKIE_DOMAINS.get(platform.value)
            if pattern:
                await page.context.clear_cookies(domain=_re.compile(pattern))
                print("已清除该平台旧 cookie。")
            await page.reload(wait_until="domcontentloaded")
            print("强制重新登录：请在窗口里扫码登录。")

        print(f"浏览器已打开 {page.url}")
        print("请在窗口里完成登录（扫码或短信均可）。")
        print(f"登录成功后会自动继续，最多等待 {timeout} 秒。")

        waited = 0
        while waited < timeout:
            await asyncio.sleep(POLL_SECONDS)
            waited += POLL_SECONDS
            if await session.is_logged_in(platform):
                # 多等一会：部分平台在写入身份 cookie 之后
                # 还会补发几个风控 cookie，太早关窗会丢。
                await asyncio.sleep(5)
                jar = await session.cookies(platform)
                print(f"登录成功，已保存 {len(jar)} 项 cookie。")
                return True
            if waited % 30 == 0:
                print(f"  仍在等待……（{waited}/{timeout} 秒）")

        print("等待超时，未检测到登录态。")
        return False


async def main() -> int:
    parser = argparse.ArgumentParser()
    # 这里不能用 argparse 的 choices：配合 nargs="*" 时，
    # 它会把「一个都没传」也当成非法取值。
    parser.add_argument("platforms", nargs="*", help="要登录的平台，可写多个")
    parser.add_argument("--status", action="store_true", help="只查看登录状态")
    parser.add_argument("--timeout", type=int, default=300, help="单个平台等待秒数")
    parser.add_argument(
        "--force", action="store_true",
        help="已登录也强制重扫（部分接口 token 过期、或要换号时用）",
    )
    args = parser.parse_args()

    load_env()
    supported = {p.value for p in SPECS}
    unknown = [p for p in args.platforms if p not in supported]
    if unknown:
        parser.error(
            f"不支持的平台 {unknown}，可选：{sorted(supported)}"
            f"（B 站走自建接口，不需要浏览器登录）"
        )

    if args.status or not args.platforms:
        await show_status()
        if not args.platforms:
            print("\n要登录请指定平台，例如：python scripts/login.py dy")
        return 0

    results = {}
    for value in args.platforms:
        results[value] = await login(Platform(value), args.timeout, force=args.force)

    print("\n=== 结果 ===")
    for value, ok in results.items():
        print(f"  {value:<4} {'成功' if ok else '失败'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
