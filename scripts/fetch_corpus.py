"""建一个用于跑实验的视频语料库。

只做榜单发现和下载，不跑任何推理。目的是攒一批**多样化**的样本：
不同分区的视频在镜头节奏、有无硬字幕、是否有人声上差异极大，
只在科技区取样得出的结论很可能只是过拟合。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vspider.discovery.bilibili import BilibiliRankingProvider  # noqa: E402
from vspider.download.base import DownloadMode  # noqa: E402
from vspider.download.ytdlp_backend import YtDlpDownloader  # noqa: E402

# 刻意跨越差异最大的几个分区：
# 知识区多为长镜头讲解（硬字幕多），鬼畜区镜头切换极快，
# 音乐区常无人声解说，生活区介于两者之间。
DEFAULT_CATEGORIES = ["tech", "music", "kichiku", "life", "game", "food"]


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-category", type=int, default=2)
    parser.add_argument("--categories", nargs="*", default=DEFAULT_CATEGORIES)
    parser.add_argument("--max-duration", type=int, default=900)
    parser.add_argument("--dest", default="/root/autodl-tmp/data/corpus")
    args = parser.parse_args()

    provider = BilibiliRankingProvider()
    downloader = YtDlpDownloader()
    dest = Path(args.dest)
    downloaded = 0

    try:
        for category in args.categories:
            try:
                items = await provider.fetch_ranking(limit=20, category=category)
            except Exception as exc:  # noqa: BLE001
                print(f"[{category}] 榜单拉取失败：{exc}")
                continue

            picked = [i for i in items if 30 < i.duration_sec <= args.max_duration]
            picked = picked[: args.per_category]
            print(f"\n[{category}] 命中 {len(picked)} 条")

            for item in picked:
                target = dest / f"{item.platform.value}_{item.video_id}.mp4"
                if target.exists():
                    print(f"  已存在，跳过 {item.title[:36]}")
                    downloaded += 1
                    continue
                try:
                    result = await downloader.download(
                        item, dest, mode=DownloadMode.VIDEO
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"  下载失败 {item.title[:30]}：{exc}")
                    continue
                downloaded += 1
                print(
                    f"  {result.size_bytes / 1024 / 1024:>6.1f} MB  "
                    f"{item.duration_sec:>4}s  {item.title[:36]}"
                )

        print(f"\n语料库共 {downloaded} 条，位于 {dest}")
        return 0
    finally:
        await provider.aclose()
        await downloader.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
