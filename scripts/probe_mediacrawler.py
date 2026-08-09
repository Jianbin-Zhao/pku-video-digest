"""验证 MediaCrawler 适配层。

分两段：前半段不需要浏览器，专门验证最容易出问题的路径处理——
MediaCrawler 有多处相对路径依赖，从别的工作目录调用会直接炸。
后半段才需要 Playwright。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vspider.mediacrawler.bootstrap import ensure_importable  # noqa: E402
from vspider.models import Platform  # noqa: E402
from vspider.settings import load_env  # noqa: E402

TARGETS = [
    ("douyin", "media_platform.douyin.client", "DouYinClient"),
    ("kuaishou", "media_platform.kuaishou.client", "KuaiShouClient"),
    ("weibo", "media_platform.weibo.client", "WeiboClient"),
    ("xhs", "media_platform.xhs.client", "XiaoHongShuClient"),
]


def check_offline() -> bool:
    print(f"当前工作目录：{Path.cwd()}")
    print("（刻意不切到 MediaCrawler 目录下，以此验证路径处理是否到位）\n")

    root = ensure_importable()
    print(f"MediaCrawler 根目录：{root}")

    print("\n== 客户端导入 ==")
    ok = True
    for name, module_path, class_name in TARGETS:
        try:
            module = __import__(module_path, fromlist=[class_name])
            getattr(module, class_name)
            print(f"  {name:<10} OK")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  {name:<10} 失败 {type(exc).__name__}: {str(exc)[:110]}")

    print("\n== 抖音签名所需的 JS 运行时 ==")
    try:
        from media_platform.douyin import help as dy_help

        print(f"  douyin.js 已编译：{dy_help.douyin_sign_obj is not None}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"  失败 {type(exc).__name__}: {str(exc)[:110]}")
        print("  这一步需要本机装有 Node.js（execjs 要一个 JS 运行时）")

    print("\n== 快手 GraphQL 查询加载（原实现写死了相对路径） ==")
    try:
        from media_platform.kuaishou.graphql import KuaiShouGraphQL

        graphql = KuaiShouGraphQL()
        names = sorted(graphql.graphql_queries)
        print(f"  加载 {len(names)} 个查询：{', '.join(names)}")
        if not names:
            ok = False
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"  失败 {type(exc).__name__}: {str(exc)[:110]}")

    return ok


async def check_online(platforms: list[Platform]) -> bool:
    from vspider.mediacrawler.session import MediaCrawlerSession

    print("\n== 启动浏览器并建立各平台客户端 ==")
    try:
        async with MediaCrawlerSession(headless=True) as session:
            for platform in platforms:
                try:
                    client = await session.client(platform)
                    page = await session.page(platform)
                    cookie_count = len(client.cookie_dict or {})
                    print(
                        f"  {platform.value:<6} OK  {type(client).__name__}  "
                        f"cookie {cookie_count} 项  当前页 {page.url[:52]}"
                    )
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"  {platform.value:<6} 失败 {type(exc).__name__}: "
                        f"{str(exc)[:110]}"
                    )
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  会话启动失败 {type(exc).__name__}: {str(exc)[:160]}")
        return False


async def main() -> int:
    load_env()
    if not check_offline():
        print("\n离线检查未全部通过，先修这些再谈浏览器。")
        return 1

    if os.environ.get("VSPIDER_SKIP_BROWSER"):
        print("\n已设置 VSPIDER_SKIP_BROWSER，跳过浏览器检查。")
        return 0

    await check_online(
        [Platform.DOUYIN, Platform.KUAISHOU, Platform.WEIBO, Platform.XHS]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
