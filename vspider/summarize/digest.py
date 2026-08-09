"""跨视频总览（digest）。

单条归纳回答"这条视频讲了什么"，这里回答"这一批视频合起来说明了什么"：
今天的热点主题有哪几类、哪些视频同属一个话题、整体情绪如何、最值得看哪条。

输入刻意只用每条视频的归纳结果（一句话 + 要点 + 话题 + 情绪），
不塞转写全文——十条视频的完整转写轻松超过 3 万字，CPU 档的小模型
上下文装不下，而聚合分析要的本来就是"已经提炼过的信号"。
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from vspider.models import Digest, DigestTheme
from vspider.summarize.base import Summarizer

if TYPE_CHECKING:
    from vspider.pipeline.orchestrator import VideoResult

_SYSTEM = (
    "你是短视频舆情分析师。用户给你一批视频的结构化归纳结果，"
    "你输出一份整体总览。只输出 JSON 对象，不要任何解释或代码块围栏。"
)

_INSTRUCTION = """\
基于上面这批视频的归纳结果，输出 JSON：
{
  "headline": "一句话概括这批视频的整体面貌（40 字以内）",
  "themes": [
    {"name": "主题名（6 字以内）", "description": "该主题下视频的共同点（30 字以内）", "video_uids": ["属于该主题的视频 uid"]}
  ],
  "observations": ["值得注意的现象或趋势，2~4 条，每条 30 字以内"],
  "top_pick_uid": "最值得优先观看的一条视频的 uid",
  "top_pick_reason": "推荐理由（30 字以内）"
}
要求：
- themes 控制在 2~4 个，每条视频只归入一个主题；
- video_uids 与 top_pick_uid 必须原样使用输入中给出的 uid；
- 观察要基于输入事实，不要编造视频里没有的内容。"""


def _render_input(results: list["VideoResult"], scenario: str) -> str:
    lines = [f"场景：{scenario}", f"视频数：{len(results)}", ""]
    for r in results:
        item, s = r.item, r.summary
        assert s is not None
        lines.append(f"- uid: {item.uid}")
        lines.append(f"  平台: {item.platform.value}  标题: {item.title}")
        lines.append(f"  作者: {item.author_name}")
        stats = item.stats
        lines.append(
            f"  互动: 播放{stats.play} 赞{stats.like} 评{stats.comment} 转{stats.share}"
        )
        lines.append(f"  一句话: {s.one_liner}")
        if s.key_points:
            lines.append(f"  要点: {'；'.join(s.key_points[:4])}")
        if s.topics:
            lines.append(f"  话题: {'、'.join(s.topics[:5])}")
        lines.append(f"  情绪: {s.sentiment.value}  广告: {'是' if s.is_promotion else '否'}")
        lines.append("")
    return "\n".join(lines)


async def build_digest(
    summarizer: Summarizer,
    results: list["VideoResult"],
    scenario: str,
) -> Digest:
    """对一批成功归纳的结果做聚合总览。调用方保证 results 非空且都有 summary。"""
    started = time.perf_counter()
    messages = [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": _render_input(results, scenario) + "\n" + _INSTRUCTION,
        },
    ]
    raw = await summarizer.chat_json(messages)

    known_uids = {r.item.uid for r in results}
    themes = []
    for t in raw.get("themes") or []:
        if not isinstance(t, dict) or not str(t.get("name") or "").strip():
            continue
        themes.append(
            DigestTheme(
                name=str(t.get("name")).strip(),
                description=str(t.get("description") or "").strip(),
                # 小模型偶尔会把 uid 抄错一两个字符，错的直接丢掉，
                # 保证前端拿到的 uid 一定能对上视频卡片。
                video_uids=[
                    u for u in (t.get("video_uids") or []) if u in known_uids
                ],
            )
        )

    top_uid = str(raw.get("top_pick_uid") or "")
    digest = Digest(
        headline=str(raw.get("headline") or "").strip(),
        themes=themes,
        observations=[
            str(o).strip() for o in (raw.get("observations") or []) if str(o).strip()
        ],
        top_pick_uid=top_uid if top_uid in known_uids else "",
        top_pick_reason=str(raw.get("top_pick_reason") or "").strip(),
        backend=getattr(summarizer, "name", ""),
        elapsed_sec=time.perf_counter() - started,
    )
    return digest


def digest_to_json(digest: Digest) -> str:
    return json.dumps(digest.model_dump(mode="json"), ensure_ascii=False)
