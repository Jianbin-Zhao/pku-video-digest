"""端到端验证：榜单 -> 下载 -> 抽音频 -> 语音识别 -> 关键帧 OCR -> 归纳。

逐阶段打印耗时，用于定位瓶颈和作为 benchmark 的基线。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vspider.asr.sensevoice import SenseVoiceAsr  # noqa: E402
from vspider.discovery.bilibili import BilibiliRankingProvider  # noqa: E402
from vspider.download.base import DownloadMode  # noqa: E402
from vspider.download.ytdlp_backend import YtDlpDownloader  # noqa: E402
from vspider.fusion.context import FusionContext  # noqa: E402
from vspider.media.audio import extract_audio, has_audio_stream, probe_duration  # noqa: E402
from vspider.media.keyframe import extract_keyframes  # noqa: E402
from vspider.ocr.rapid import RapidOcr  # noqa: E402
from vspider.settings import load_env, require  # noqa: E402
from vspider.summarize.openai_compat import OpenAICompatSummarizer  # noqa: E402

MODELS_ROOT = os.environ.get("VSPIDER_MODELS_ROOT", "/root/autodl-tmp/models")
DATA_ROOT = Path(os.environ.get("VSPIDER_DATA_ROOT", "/root/autodl-tmp/data"))


class Timer:
    def __init__(self) -> None:
        self.stages: list[tuple[str, float]] = []
        self._start = time.perf_counter()

    def mark(self, name: str) -> None:
        now = time.perf_counter()
        elapsed = now - self._start
        self.stages.append((name, elapsed))
        print(f"  [{name}] {elapsed:.2f}s")
        self._start = now

    def report(self) -> None:
        total = sum(t for _, t in self.stages)
        print("\n耗时分解：")
        for name, elapsed in self.stages:
            share = elapsed / total * 100 if total else 0
            print(f"  {name:<14} {elapsed:>7.2f}s  {share:>5.1f}%")
        print(f"  {'合计':<14} {total:>7.2f}s")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", default="tech", help="B 站分区")
    parser.add_argument("--index", type=int, default=0, help="取榜单第几名（从 0 起）")
    parser.add_argument("--model", default="qwen-flash", help="归纳模型")
    parser.add_argument("--device", default="cuda:0", help="ASR 设备")
    parser.add_argument("--max-duration", type=int, default=600,
                        help="超过该时长（秒）的视频跳过，避免第一次验证就等太久")
    args = parser.parse_args()

    load_env()
    api_key = require("DASHSCOPE_API_KEY")
    base_url = require("DASHSCOPE_BASE_URL")

    timer = Timer()
    provider = BilibiliRankingProvider()
    summarizer = OpenAICompatSummarizer(
        base_url=base_url, model=args.model, api_key=api_key
    )
    asr = SenseVoiceAsr(
        model_dir=f"{MODELS_ROOT}/SenseVoiceSmall",
        vad_dir=f"{MODELS_ROOT}/fsmn-vad",
        device=args.device,
    )
    ocr = RapidOcr()
    downloader = YtDlpDownloader()

    try:
        print("== 1. 榜单 ==")
        items = await provider.fetch_ranking(limit=10, category=args.category)
        timer.mark("ranking")
        if not items:
            print("榜单为空")
            return 1

        candidates = [i for i in items if 0 < i.duration_sec <= args.max_duration]
        if not candidates:
            print(f"榜单里没有时长 <= {args.max_duration}s 的视频，放宽 --max-duration 再试")
            return 1
        item = candidates[min(args.index, len(candidates) - 1)]
        print(f"  选中: #{item.rank} {item.title}")
        print(f"  时长 {item.duration_sec}s | 播放 {item.stats.play} | {item.url}")

        print("\n== 2. 下载 ==")
        video_dir = DATA_ROOT / "videos"
        result = await downloader.download(item, video_dir, mode=DownloadMode.VIDEO)
        timer.mark("download")
        size_mb = result.size_bytes / 1024 / 1024
        print(f"  {result.path.name}  {size_mb:.1f} MB")

        print("\n== 3. 抽音频 ==")
        duration = await probe_duration(result.path)
        if not await has_audio_stream(result.path):
            print("  该视频无音轨，跳过语音识别")
            transcript = None
        else:
            audio_path = DATA_ROOT / "audio" / f"{item.platform.value}_{item.video_id}.wav"
            await extract_audio(result.path, audio_path)
            timer.mark("extract_audio")
            print(f"  {audio_path.name}  {audio_path.stat().st_size / 1024 / 1024:.1f} MB")

            print("\n== 4. 语音识别 ==")
            transcript = await asr.transcribe(audio_path)
            timer.mark("asr")
            speed = duration / transcript.elapsed_sec if transcript.elapsed_sec else 0
            print(f"  分段 {len(transcript.segments)} 段，共 {len(transcript.full_text)} 字")
            print(f"  实时倍速 {speed:.1f}x（音频 {duration:.0f}s / 识别 {transcript.elapsed_sec:.1f}s）")
            for segment in transcript.segments[:3]:
                print(f"    [{segment.start:6.1f}-{segment.end:6.1f}] {segment.text[:60]}")

        print("\n== 5. 关键帧 + OCR ==")
        frame_dir = DATA_ROOT / "frames" / f"{item.platform.value}_{item.video_id}"
        frames = await extract_keyframes(result.path, frame_dir)
        timer.mark("keyframes")
        print(f"  抽出 {len(frames)} 帧")
        ocr_result = await ocr.recognize(frames)
        timer.mark("ocr")
        merged = ocr_result.merged_text()
        print(f"  有文字的帧 {len(ocr_result.frames)} / {len(frames)}，去重后 {len(merged)} 字")
        if merged:
            print(f"    示例: {merged[:120].replace(chr(10), ' | ')}")

        print("\n== 6. 归纳 ==")
        context = FusionContext(item=item, transcript=transcript, ocr=ocr_result)
        print(f"  参与融合的信号源: {context.signal_sources}")
        summary = await summarizer.summarize(context)
        timer.mark("summarize")
        print(f"\n  一句话   : {summary.one_liner}")
        for index, point in enumerate(summary.key_points, 1):
            print(f"  要点{index}    : {point}")
        print(f"  话题     : {', '.join(summary.topics)}")
        print(f"  情感/推广: {summary.sentiment.value} / {summary.is_promotion}")
        print(f"  置信度   : {summary.confidence}")

        timer.report()
        print("\nE2E_OK")
        return 0
    finally:
        await provider.aclose()
        await summarizer.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
