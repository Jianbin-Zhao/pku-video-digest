"""本机采集下载，导出视频与 items.json。"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vspider.download.base import DownloadMode  # noqa: E402
from vspider.mediacrawler.session import MediaCrawlerSession  # noqa: E402
from vspider.models import Platform, VideoItem  # noqa: E402
from vspider.registry import (  # noqa: E402
    BROWSER_PLATFORMS,
    build_downloader,
    build_provider,
    resolve_platform,
)
from vspider.settings import configure_stdio, load_env  # noqa: E402


async def _discover(provider: object, args: argparse.Namespace) -> list[VideoItem]:
    """按参数选择采集入口：搜索 > 创作者 > 榜单。"""
    if args.keyword:
        return await provider.search_videos(args.keyword, limit=args.limit)
    if args.creator:
        return await provider.fetch_creator_videos(
            args.creator,
            limit=args.limit,
            since=date.today() if args.today else None,
        )
    return await provider.fetch_ranking(limit=args.limit, today_only=args.today)


async def _run(
    platform: Platform, session: object | None, out_dir: Path, args: argparse.Namespace
) -> int:
    provider = build_provider(platform, session)
    items = await _discover(provider, args)
    print(f"发现 {len(items)} 条")
    if not items:
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest = out_dir / "items.json"
        manifest.write_text("[]\n", encoding="utf-8")
        print("没有可下载的条目，退出。")
        print(f"已写入空清单：{manifest}")
        # 指定创作者在今天没有投稿是合法业务结果；榜单/搜索为空仍需报失败。
        return 0 if args.creator and args.today else 1

    downloader = build_downloader(platform, session=session)
    exported: list[dict] = []
    for item in items:
        try:
            result = await downloader.download(item, out_dir, DownloadMode.VIDEO)
        except Exception as exc:  # noqa: BLE001
            print(f"  {item.video_id} 下载失败：{type(exc).__name__}: {str(exc)[:120]}")
            continue
        # 只存文件名，服务器侧按 items.json 所在目录拼绝对路径。
        record = item.model_dump(mode="json")
        record["_local_file"] = result.path.name
        exported.append(record)
        mb = result.size_bytes / 1024 / 1024
        print(f"  {item.video_id}  {mb:.1f} MB  -> {result.path.name}")

    manifest = out_dir / "items.json"
    manifest.write_text(
        json.dumps(exported, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n导出 {len(exported)} 条到 {out_dir}")
    print(f"清单：{manifest}")
    print("\n下一步：把这个目录传到服务器，再跑")
    print("  python scripts/understand.py <服务器上该目录路径> --digest")
    # 采集到但一条都没下成才算失败；部分失败仍产出可用清单。
    return 0 if exported else 1


async def main() -> int:
    parser = argparse.ArgumentParser()
    # 五个平台都支持：浏览器平台开会话，B 站直连。
    parser.add_argument("platform", help="bili / dy / ks / wb / xhs")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--creator", default="", help="创作者 ID，走场景二")
    parser.add_argument("--keyword", default="", help="搜索关键词，走场景三（plus）")
    parser.add_argument("--today", action="store_true")
    parser.add_argument("--show-browser", action="store_true")
    parser.add_argument(
        "--out-dir", default="", help="导出目录，缺省 data/handoff/<平台>。场景二/三建议单独指定，避免覆盖场景一的产物"
    )
    args = parser.parse_args()

    configure_stdio()
    load_env()
    platform = resolve_platform(args.platform)
    out_dir = (
        Path(args.out_dir)
        if args.out_dir
        else Path(__file__).resolve().parent.parent / "data" / "handoff" / platform.value
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    # 浏览器平台需要一个活的会话贯穿采集与下载；B 站不需要，直连即可。
    if platform in BROWSER_PLATFORMS:
        async with MediaCrawlerSession(headless=not args.show_browser) as session:
            return await _run(platform, session, out_dir, args)
    return await _run(platform, None, out_dir, args)


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        raise SystemExit(asyncio.run(main()))
    raise SystemExit(130)
