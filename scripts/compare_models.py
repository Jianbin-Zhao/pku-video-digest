"""同一条输入横向对比不同档位模型的归纳质量与耗时。

目的是用数据回答"是不是模型越大越好"，而不是靠感觉选型。
对比会跑两轮：

  轮一  只喂元数据（模拟无人声视频 / 转写失败的最坏情况）
  轮二  额外喂入模拟的转写文稿（模拟信息完整的理想情况）

两轮对比能分离出两个变量的贡献：换更强的模型 vs 补齐输入信息。
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vspider.discovery.bilibili import BilibiliRankingProvider  # noqa: E402
from vspider.fusion.context import FusionContext  # noqa: E402
from vspider.models import Transcript  # noqa: E402
from vspider.settings import load_env, require  # noqa: E402
from vspider.summarize.openai_compat import OpenAICompatSummarizer  # noqa: E402

MODELS = [
    "qwen-flash",
    "qwen-plus",
    "qwen3.7-max",
    "qwen3.8-max",
    "deepseek-v4-pro",
]


async def run_one(model: str, context: FusionContext, key: str, base: str) -> None:
    summarizer = OpenAICompatSummarizer(base_url=base, model=model, api_key=key)
    try:
        summary = await summarizer.summarize(context)
        print(f"\n  --- {model} ({summary.elapsed_sec:.2f}s, conf={summary.confidence}) ---")
        print(f"    一句话: {summary.one_liner}")
        for point in summary.key_points:
            print(f"    要点  : {point}")
        print(f"    话题  : {', '.join(summary.topics)}")
        print(f"    情感/推广: {summary.sentiment.value} / {summary.is_promotion}")
    except Exception as exc:  # noqa: BLE001
        print(f"\n  --- {model} 失败: {type(exc).__name__}: {str(exc)[:200]}")
    finally:
        await summarizer.aclose()


async def main() -> int:
    load_env()
    key = require("DASHSCOPE_API_KEY")
    base = require("DASHSCOPE_BASE_URL")

    provider = BilibiliRankingProvider()
    try:
        items = await provider.fetch_ranking(limit=3, category="tech")
    finally:
        await provider.aclose()

    if not items:
        print("科技区排行榜为空")
        return 1

    item = items[0]
    print(f"测试视频: {item.title}")
    print(f"作者: {item.author_name} | 播放: {item.stats.play} | 时长: {item.duration_sec}s")
    print(f"简介: {item.desc[:120]}")

    print("\n" + "=" * 70)
    print("轮一：只有元数据（模拟无人声视频 / ASR 失败）")
    print("=" * 70)
    bare = FusionContext(item=item)
    for model in MODELS:
        await run_one(model, bare, key, base)

    # 用视频简介充当转写文稿的替身，只为在没接 ASR 前先验证
    # "输入信息变多"对各档位模型的影响，正式流水线里这里是真实转写。
    faux_transcript = (item.desc or item.title) * 3
    print("\n" + "=" * 70)
    print("轮二：元数据 + 文稿（模拟 ASR 成功，信息完整）")
    print("=" * 70)
    rich = FusionContext(
        item=item,
        transcript=Transcript(backend="faux", full_text=faux_transcript),
    )
    for model in MODELS:
        await run_one(model, rich, key, base)

    print("\n对比要点：注意 confidence 在两轮之间的变化幅度，")
    print("以及同一轮内不同模型之间的差异幅度，哪个更大。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
