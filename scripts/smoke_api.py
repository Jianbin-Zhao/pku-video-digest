"""最小闭环验证：B 站榜单 -> 元数据融合 -> 百炼 API 归纳。

这一步刻意不含视频下载和语音识别，目的是先把两个外部依赖
（B 站接口、归纳接口）单独验证通，避免后面排查问题时不知道是哪一环挂了。
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vspider.discovery.bilibili import BilibiliRankingProvider  # noqa: E402
from vspider.fusion.context import FusionContext  # noqa: E402
from vspider.settings import load_env, require  # noqa: E402
from vspider.summarize.openai_compat import OpenAICompatSummarizer  # noqa: E402

MODEL = os.environ.get("VSPIDER_SUMMARY_MODEL", "qwen-flash")


async def main() -> int:
    load_env()
    api_key = require("DASHSCOPE_API_KEY")
    base_url = require("DASHSCOPE_BASE_URL")

    provider = BilibiliRankingProvider()
    summarizer = OpenAICompatSummarizer(
        base_url=base_url, model=MODEL, api_key=api_key
    )

    try:
        print("== 1. 拉取 B 站全站排行榜 ==")
        items = await provider.fetch_ranking(limit=5, category="all")
        for item in items:
            print(
                f"  #{item.rank} [{item.duration_sec}s] {item.title}"
                f"\n      作者={item.author_name} 播放={item.stats.play}"
                f" 点赞={item.stats.like} 发布={item.publish_time}"
                f"\n      {item.url}"
            )

        if not items:
            print("排行榜为空，B 站接口可能有变化")
            return 1

        print(f"\n== 2. 用 {MODEL} 归纳第 1 条（仅元数据，无转写）==")
        context = FusionContext(item=items[0])
        summary = await summarizer.summarize(context)
        print(f"  信号源      : {context.signal_sources}")
        print(f"  一句话      : {summary.one_liner}")
        for index, point in enumerate(summary.key_points, 1):
            print(f"  要点{index}       : {point}")
        print(f"  话题        : {summary.topics}")
        print(f"  情感/推广   : {summary.sentiment.value} / {summary.is_promotion}")
        print(f"  自评置信度  : {summary.confidence}")
        print(f"  后端/耗时   : {summary.backend} / {summary.elapsed_sec:.2f}s")
        print("\nSMOKE_OK")
        return 0
    finally:
        await provider.aclose()
        await summarizer.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
