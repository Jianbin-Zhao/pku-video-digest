"""本机侧：采集并下载视频，导出文件 + 元数据供服务器理解。

混合部署的前半段。两类平台都能走这条路：

  浏览器平台（抖音/快手/微博/小红书）：采集必须在本机做
      （干净家庭 IP + 已登录），这是设计使然。
  B 站：本来在服务器直连即可，但云服务器 IP 常被 B 站风控（-352 / v_voucher）。
      B 站免登录，本机家庭 IP 却很干净，所以也可以走本机采集这条兜底路。

下载完把视频文件和一份 items.json 放到同一个目录，整个目录传到服务器后，
用 scripts/understand.py 接手做内容理解（ASR / OCR / 归纳 / 总览）。

三种场景对应题面与 plus 拓展：
    --limit N              场景一：今日榜单前 N
    --creator <id> --today 场景二：某创作者今天发布的视频
    --keyword <词>         场景三（plus）：按关键词搜索

用法：
    python scripts/fetch_local.py ks --limit 3
    python scripts/fetch_local.py wb --creator <uid> --today
    python scripts/fetch_local.py bili --keyword 人工智能 --limit 5
输出：
    data/handoff/<platform>/  下面是若干 mp4 和一个 items.json
"""

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
