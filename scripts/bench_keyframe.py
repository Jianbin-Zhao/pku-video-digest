"""对比抽帧策略：iframe / scene / interval。

**为什么每条都要跑完整 OCR**：抽帧不是目的，抽出来的图是要喂给 OCR
读画面文字的。只比速度会得出错误结论——"每隔 N 秒截一张"必然最快，
但它可能连着截到三张同一个静止画面、把有字的镜头整个跳过去。
所以每种策略都记录三个量：抽帧耗时、OCR 耗时、去重后的有效字数。

**为什么固定帧数**：三种策略统一抽 24 帧，否则"抽得多所以字多"
会污染结论，分不清是策略好还是单纯采样多。

**为什么要多样本**：第一版只测了一条视频就改了默认策略，这不成立——
视频的镜头节奏、编码参数、有无硬字幕差异极大，单条样本的结论
很可能只是过拟合。这一版默认在样本目录里随机抽多条，
输出每条的明细和跨样本的汇总。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vspider.media.audio import probe_duration  # noqa: E402
from vspider.media.keyframe import extract_keyframes  # noqa: E402
from vspider.ocr.rapid import RapidOcr  # noqa: E402

STRATEGIES = ["iframe", "scene", "interval"]


@dataclass
class Measurement:
    video: str
    duration: float
    strategy: str
    extract_sec: float
    ocr_sec: float
    frames: int
    chars: int


async def measure(
    video: Path, strategy: str, ocr: RapidOcr, dest_root: Path, max_frames: int
) -> Measurement:
    dest = dest_root / video.stem / strategy

    started = time.perf_counter()
    frames = await extract_keyframes(
        video, dest, max_frames=max_frames, strategy=strategy
    )
    extract_sec = time.perf_counter() - started

    started = time.perf_counter()
    result = await ocr.recognize(frames)
    ocr_sec = time.perf_counter() - started

    return Measurement(
        video=video.stem,
        duration=await probe_duration(video),
        strategy=strategy,
        extract_sec=extract_sec,
        ocr_sec=ocr_sec,
        frames=len(frames),
        chars=len(result.merged_text()),
    )


def summarize(rows: list[Measurement]) -> None:
    print("\n\n=== 跨样本汇总 ===")
    header = (
        f"{'策略':<10} {'抽帧中位':>10} {'抽帧均值':>10} "
        f"{'字数中位':>10} {'字数均值':>10} {'样本':>5}"
    )
    print(header)
    print("-" * len(header))

    baseline_chars: float | None = None
    for strategy in STRATEGIES:
        subset = [r for r in rows if r.strategy == strategy]
        if not subset:
            continue
        extracts = [r.extract_sec for r in subset]
        chars = [float(r.chars) for r in subset]
        print(
            f"{strategy:<10} {statistics.median(extracts):>9.2f}s "
            f"{statistics.mean(extracts):>9.2f}s "
            f"{statistics.median(chars):>10.0f} {statistics.mean(chars):>10.0f} "
            f"{len(subset):>5}"
        )
        if strategy == "interval":
            baseline_chars = statistics.mean(chars)

    # 逐样本判定谁的字数最多，比只看均值更稳：
    # 均值容易被某一条字特别多的视频带偏。
    print("\n各样本上「有效字数最多」的策略：")
    videos = sorted({r.video for r in rows})
    wins: dict[str, int] = {s: 0 for s in STRATEGIES}
    for video in videos:
        subset = [r for r in rows if r.video == video]
        best = max(subset, key=lambda r: r.chars)
        wins[best.strategy] += 1
    for strategy, count in wins.items():
        print(f"  {strategy:<10} {count}/{len(videos)}")

    if baseline_chars:
        for strategy in STRATEGIES:
            subset = [r for r in rows if r.strategy == strategy]
            if subset and strategy != "interval":
                gain = statistics.mean([float(r.chars) for r in subset]) / baseline_chars
                print(f"\n{strategy} 相对 interval 的字数倍率：{gain:.2f}×")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "source", help="视频文件，或包含多个视频的目录（目录时随机抽样）"
    )
    parser.add_argument("--samples", type=int, default=5, help="目录模式下抽几条")
    parser.add_argument("--seed", type=int, default=42, help="随机种子，便于复现")
    parser.add_argument("--max-frames", type=int, default=24)
    parser.add_argument("--out", default="/root/autodl-tmp/data/bench_frames")
    args = parser.parse_args()

    source = Path(args.source)
    if source.is_dir():
        candidates = sorted(
            p for p in source.iterdir() if p.suffix in {".mp4", ".mkv", ".flv"}
        )
        if not candidates:
            print(f"{source} 下没有视频文件")
            return 1
        random.seed(args.seed)
        videos = random.sample(candidates, min(args.samples, len(candidates)))
    elif source.exists():
        videos = [source]
    else:
        print(f"找不到 {source}")
        return 1

    print(f"样本数 {len(videos)}，随机种子 {args.seed}，每条固定抽 {args.max_frames} 帧")

    ocr = RapidOcr()
    rows: list[Measurement] = []
    dest_root = Path(args.out)

    try:
        for video in videos:
            duration = await probe_duration(video)
            print(f"\n--- {video.name}  时长 {duration:.0f}s ---")
            header = (
                f"{'策略':<10} {'抽帧':>8} {'OCR':>8} {'合计':>8} "
                f"{'帧数':>5} {'有效字数':>8}"
            )
            print(header)
            for strategy in STRATEGIES:
                row = await measure(video, strategy, ocr, dest_root, args.max_frames)
                rows.append(row)
                print(
                    f"{row.strategy:<10} {row.extract_sec:>7.2f}s {row.ocr_sec:>7.2f}s "
                    f"{row.extract_sec + row.ocr_sec:>7.2f}s {row.frames:>5} "
                    f"{row.chars:>8}"
                )
        summarize(rows)
    finally:
        await ocr.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
