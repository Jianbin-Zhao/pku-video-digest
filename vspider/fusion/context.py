"""融合元数据、ASR、OCR、评论和互动数据。"""

from __future__ import annotations

from dataclasses import dataclass, field

from vspider.models import Comment, OcrResult, Transcript, VideoItem


@dataclass
class Budget:
    """各路信息的字符预算。

    默认值是按 Qwen3-8B 的 32K 上下文留足余量后定的，
    换更小的本地模型时把整体缩一半即可。
    """

    transcript: int = 6000
    ocr: int = 1500
    comments: int = 1200
    desc: int = 500
    max_comments: int = 8


@dataclass
class FusionContext:
    item: VideoItem
    transcript: Transcript | None = None
    ocr: OcrResult | None = None
    comments: list[Comment] = field(default_factory=list)
    budget: Budget = field(default_factory=Budget)

    @property
    def has_speech(self) -> bool:
        return self.transcript is not None and not self.transcript.is_empty

    @property
    def signal_sources(self) -> list[str]:
        """实际参与融合的信号源，写进报告和界面用于说明摘要依据。"""
        sources = ["metadata"]
        if self.has_speech:
            sources.append("asr")
        if self.ocr and self.ocr.merged_text().strip():
            sources.append("ocr")
        if self.comments:
            sources.append("comments")
        return sources

    def render(self) -> str:
        item = self.item
        blocks: list[str] = []

        meta = [
            f"平台: {item.platform.value}",
            f"标题: {item.title}",
            f"作者: {item.author_name}",
        ]
        if item.publish_time:
            meta.append(f"发布时间: {item.publish_time:%Y-%m-%d %H:%M}")
        if item.duration_sec:
            meta.append(f"时长: {item.duration_sec} 秒")
        if item.tags:
            meta.append(f"话题标签: {', '.join(item.tags)}")
        stats = item.stats
        meta.append(
            f"互动: 播放 {stats.play} / 点赞 {stats.like} / 评论 {stats.comment} "
            f"/ 转发 {stats.share} / 收藏 {stats.collect}"
        )
        if item.rank is not None:
            meta.append(f"榜位: 第 {item.rank} 名（来源 {item.rank_source.value if item.rank_source else '未知'}）")
        blocks.append("【元数据】\n" + "\n".join(meta))

        if item.desc.strip():
            blocks.append("【简介】\n" + _clip(item.desc, self.budget.desc))

        if self.has_speech:
            assert self.transcript is not None
            blocks.append(
                "【语音转写】\n" + _clip(self.transcript.full_text, self.budget.transcript)
            )
        else:
            blocks.append("【语音转写】\n（该视频无人声或语音识别未得到有效内容）")

        if self.ocr:
            ocr_text = self.ocr.merged_text().strip()
            if ocr_text:
                blocks.append(
                    "【画面文字 / 硬字幕（按出现顺序去重）】\n"
                    + _clip(ocr_text, self.budget.ocr)
                )

        if self.comments:
            top = sorted(self.comments, key=lambda c: c.like_count, reverse=True)
            lines = [
                f"({c.like_count} 赞) {c.text.strip()}"
                for c in top[: self.budget.max_comments]
                if c.text.strip()
            ]
            if lines:
                blocks.append(
                    "【高赞评论】\n" + _clip("\n".join(lines), self.budget.comments)
                )

        return "\n\n".join(blocks)


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    # 从中间截断而不是尾部：短视频转写的结尾常有总结句和引导关注，
    # 直接砍尾巴会丢掉信息密度最高的部分。
    head = limit * 2 // 3
    tail = limit - head
    return f"{text[:head]}\n……（中间省略 {len(text) - limit} 字）……\n{text[-tail:]}"
