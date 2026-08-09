"""对单个平台做真实网络调用，检查榜单发现、字段映射与下载。

用法：
    python scripts/probe_platform.py dy
    python scripts/probe_platform.py dy --creator MS4wLjABAAAA...
    python scripts/probe_platform.py dy --download 2     # 顺带下载前 2 条
    python scripts/probe_platform.py dy --show-browser   # 需要扫码时用

默认只验证「发现」这一环，跑得快，适合在接入新平台时反复调。
加 --download 才会真的落盘，用来确认直链和请求头都对。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vspider.mediacrawler.session import MediaCrawlerSession  # noqa: E402
from vspider.models import Platform, VideoItem  # noqa: E402
from vspider.settings import configure_stdio, load_env  # noqa: E402


def build_provider(platform: Platform, session: MediaCrawlerSession):
    if platform is Platform.DOUYIN:
        from vspider.discovery.douyin import DouyinRankingProvider

        return DouyinRankingProvider(session)
    if platform is Platform.KUAISHOU:
        from vspider.discovery.kuaishou import KuaishouRankingProvider

        return KuaishouRankingProvider(session)
    if platform is Platform.WEIBO:
        from vspider.discovery.weibo import WeiboRankingProvider

        return WeiboRankingProvider(session)
    if platform is Platform.XHS:
        from vspider.discovery.xhs import XhsRankingProvider

        return XhsRankingProvider(session)
    raise SystemExit(f"{platform.value} 不走 MediaCrawler 适配层")


def clean(text: str) -> str:
    """压掉换行和各类不可见空白，避免标题把表格撑乱。"""
    return " ".join((text or "").split())


def show(items: list[VideoItem]) -> None:
    if not items:
        print("  没有结果")
        return
    for item in items:
        source = item.rank_source.value if item.rank_source else "-"
        published = (
            item.publish_time.strftime("%m-%d %H:%M") if item.publish_time else "未知"
        )
        title = clean(item.title or item.desc)[:38]
        print(
            f"  {item.rank or 0:>2}. [{source}] {title}\n"
            f"      作者 {clean(item.author_name)[:14]:<14} 发布 {published}  "
            f"时长 {item.duration_sec}s  互动 {item.stats.engagement()}"
        )
        print(f"      {item.url}")
    # 字段映射最容易在这里翻车：拿到了条目但关键字段全是空的。
    missing = [
        name
        for name, count in {
            "标题": sum(1 for i in items if not (i.title or i.desc)),
            "作者": sum(1 for i in items if not i.author_name),
            "发布时间": sum(1 for i in items if i.publish_time is None),
            "时长": sum(1 for i in items if i.duration_sec <= 0),
        }.items()
        if count == len(items)
    ]
    if missing:
        print(f"\n  [!] 这些字段全部为空，映射可能有误：{', '.join(missing)}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("platform", choices=[p.value for p in Platform])
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--creator", default="", help="创作者 ID 或主页链接")
    parser.add_argument("--today", action="store_true", help="只保留今天发布的")
    parser.add_argument(
        "--show-browser", action="store_true", help="显示浏览器窗口（扫码登录时用）"
    )
    parser.add_argument(
        "--user-data-dir", default="", help="持久化浏览器目录，可保留登录态"
    )
    parser.add_argument("--wait", type=int, default=0, help="启动后暂停若干秒供扫码")
    parser.add_argument(
        "--download", type=int, default=0, help="下载前 N 条，验证直链是否可用"
    )
    args = parser.parse_args()

    configure_stdio()
    # 采集层用 warning 报告「某个词失败了」这类可恢复问题。不打开的话，
    # 被限流会表现成安静的「没有结果」，根本没法排查。
    logging.basicConfig(level=logging.WARNING, format="  [!] %(message)s")
    load_env()
    platform = Platform(args.platform)

    async with MediaCrawlerSession(
        headless=not args.show_browser,
        user_data_dir=args.user_data_dir,
    ) as session:
        await session.page(platform)
        if args.wait:
            print(f"浏览器已打开，{args.wait} 秒内完成登录……")
            await asyncio.sleep(args.wait)
            await session.refresh_cookies(platform)

        provider = build_provider(platform, session)

        if args.creator:
            print(f"\n=== {platform.value} 创作者作品：{args.creator} ===")
            items = await provider.fetch_creator_videos(
                args.creator,
                limit=args.limit,
                since=date.today() if args.today else None,
            )
        else:
            print(f"\n=== {platform.value} 榜单前 {args.limit} ===")
            items = await provider.fetch_ranking(
                limit=args.limit, today_only=args.today
            )
        show(items)

        if args.download and items:
            await try_download(platform, session, items[: args.download])
    return 0


async def try_download(
    platform: Platform, session: MediaCrawlerSession, items: list[VideoItem]
) -> None:
    from vspider.download.base import DownloadMode
    from vspider.registry import build_downloader

    dest = Path(__file__).resolve().parent.parent / "data" / "probe_downloads"
    downloader = build_downloader(platform, session=session)
    print(f"\n=== 下载验证（后端 {downloader.name}，落到 {dest.name}/）===")

    for item in items:
        try:
            result = await downloader.download(item, dest, DownloadMode.VIDEO)
        except Exception as exc:  # noqa: BLE001
            print(f"  {item.video_id} 失败 {type(exc).__name__}: {str(exc)[:150]}")
            continue
        mb = result.size_bytes / 1024 / 1024
        speed = mb / result.elapsed_sec if result.elapsed_sec > 0 else 0
        print(
            f"  {item.video_id}  {mb:.1f} MB  {result.elapsed_sec:.1f}s  "
            f"{speed:.1f} MB/s  -> {result.path.name}"
        )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
