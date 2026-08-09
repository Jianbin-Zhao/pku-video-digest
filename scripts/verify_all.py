"""一次性验证五个平台的真实可用性。

判据是「能不能取到数据」，不做任何间接推断。
接入新平台或怀疑登录态失效时跑这个，一屏看清全局。

用法：
    python scripts/verify_all.py                 # 全部平台
    python scripts/verify_all.py dy ks           # 指定平台
    python scripts/verify_all.py --download      # 顺带验证下载
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vspider.download.base import DownloadMode  # noqa: E402
from vspider.mediacrawler.session import MediaCrawlerSession  # noqa: E402
from vspider.mediacrawler.verify import (  # noqa: E402
    PlatformStatus,
    verify_data_access,
)
from vspider.models import Platform  # noqa: E402
from vspider.registry import BROWSER_PLATFORMS, build_downloader, build_provider  # noqa: E402
from vspider.settings import configure_stdio, load_env  # noqa: E402

ORDER = [
    Platform.BILIBILI,
    Platform.DOUYIN,
    Platform.KUAISHOU,
    Platform.WEIBO,
    Platform.XHS,
]


async def check_download(
    platform: Platform, session: MediaCrawlerSession | None
) -> str:
    """真的下一条最短的视频，验证直链与请求头。"""
    dest = Path(__file__).resolve().parent.parent / "data" / "verify_downloads"
    try:
        provider = build_provider(platform, session)
        items = await provider.fetch_ranking(limit=5)
        if not items:
            return "没有可下载的条目"
        # 挑最短的那条，把验证时间压到最小。时长未知的排在最后。
        target = min(items, key=lambda i: i.duration_sec or 10_000)

        downloader = build_downloader(platform, session=session)
        result = await downloader.download(target, dest, DownloadMode.VIDEO)
        mb = result.size_bytes / 1024 / 1024
        return (
            f"{downloader.name}  {mb:.1f} MB  {result.elapsed_sec:.1f}s  "
            f"{target.duration_sec}s 视频"
        )
    except Exception as exc:  # noqa: BLE001
        return f"失败 {type(exc).__name__}: {str(exc)[:80]}"


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("platforms", nargs="*", help="留空则验证全部")
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--download", action="store_true", help="同时验证下载")
    parser.add_argument("--show-browser", action="store_true")
    args = parser.parse_args()

    configure_stdio()
    logging.basicConfig(level=logging.WARNING, format="  [!] %(message)s")
    load_env()

    targets = (
        [Platform(p) for p in args.platforms] if args.platforms else list(ORDER)
    )
    needs_browser = any(p in BROWSER_PLATFORMS for p in targets)

    session: MediaCrawlerSession | None = None
    statuses: list[PlatformStatus] = []
    downloads: dict[Platform, str] = {}

    async def run() -> None:
        for platform in targets:
            scoped = session if platform in BROWSER_PLATFORMS else None
            print(f"检查 {platform.value} ……")
            status = await verify_data_access(platform, scoped, args.limit)
            statuses.append(status)
            if args.download and status.ok:
                downloads[platform] = await check_download(platform, scoped)

    if needs_browser:
        async with MediaCrawlerSession(headless=not args.show_browser) as opened:
            session = opened
            await run()
    else:
        await run()

    print("\n" + "=" * 74)
    print("平台可用性（判据：真的取到数据）")
    print("=" * 74)
    for status in statuses:
        print("  " + status.line())
        if status.login_hint:
            print(f"       登录态提示：{status.login_hint}")
        if args.download and status.platform in downloads:
            print(f"       下载：{downloads[status.platform]}")

    ok = sum(1 for s in statuses if s.ok)
    print(f"\n  合计 {ok}/{len(statuses)} 个平台可用")
    return 0 if ok == len(statuses) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
