"""本机侧：采集并下载浏览器平台的视频，导出文件 + 元数据供服务器理解。

混合部署的前半段。浏览器平台的采集必须在本机做（干净家庭 IP + 已登录），
下载完把视频文件和一份 items.json 放到同一个目录，
整个目录传到服务器后，用 scripts/understand.py 接手做内容理解。

用法：
    python scripts/fetch_local.py ks --limit 3
    python scripts/fetch_local.py wb --creator <uid> --today
输出：
    data/handoff/<platform>/  下面是若干 mp4 和一个 items.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vspider.download.base import DownloadMode  # noqa: E402
from vspider.mediacrawler.session import MediaCrawlerSession  # noqa: E402
from vspider.models import Platform, VideoItem  # noqa: E402
from vspider.registry import BROWSER_PLATFORMS, build_downloader, build_provider  # noqa: E402
from vspider.settings import configure_stdio, load_env  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("platform", choices=[p.value for p in BROWSER_PLATFORMS])
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--creator", default="", help="创作者 ID，走场景二")
    parser.add_argument("--today", action="store_true")
    parser.add_argument("--show-browser", action="store_true")
    parser.add_argument(
        "--out-dir", default="", help="导出目录，缺省 data/handoff/<平台>。场景二建议单独指定，避免覆盖场景一的产物"
    )
    args = parser.parse_args()

    configure_stdio()
    load_env()
    platform = Platform(args.platform)
    out_dir = (
        Path(args.out_dir)
        if args.out_dir
        else Path(__file__).resolve().parent.parent / "data" / "handoff" / platform.value
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    async with MediaCrawlerSession(headless=not args.show_browser) as session:
        provider = build_provider(platform, session)
        if args.creator:
            items: list[VideoItem] = await provider.fetch_creator_videos(
                args.creator, limit=args.limit,
                since=date.today() if args.today else None,
            )
        else:
            items = await provider.fetch_ranking(
                limit=args.limit, today_only=args.today
            )
        print(f"发现 {len(items)} 条")
        if not items:
            print("没有可下载的条目，退出。")
            return 1

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
        print(f"  python scripts/understand.py <服务器上该目录路径>")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
