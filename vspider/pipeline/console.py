"""把流水线事件渲染成终端输出。

多条视频是并发推进的，事件会交错到达，所以每行都带上视频短标识，
否则并发场景下的日志根本读不出是哪条视频在动。
"""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from vspider.models import Sentiment
from vspider.pipeline.events import STAGE_LABELS, Event, EventKind, Stage
from vspider.pipeline.orchestrator import RunResult, VideoResult

_KIND_STYLE: dict[EventKind, str] = {
    EventKind.RUN_START: "bold cyan",
    EventKind.RUN_DONE: "bold green",
    EventKind.VIDEO_START: "bold",
    EventKind.VIDEO_DONE: "green",
    EventKind.VIDEO_FAILED: "bold red",
    EventKind.STAGE_DONE: "dim",
    EventKind.STAGE_SKIPPED: "yellow",
    EventKind.STAGE_FAILED: "red",
    EventKind.LOG: "magenta",
}

_SENTIMENT_LABELS: dict[Sentiment, str] = {
    Sentiment.POSITIVE: "正面",
    Sentiment.NEUTRAL: "中性",
    Sentiment.NEGATIVE: "负面",
    Sentiment.MIXED: "褒贬不一",
}


class ConsoleReporter:
    """事件消费者。作为 sink 传给编排器。"""

    def __init__(self, console: Console | None = None, verbose: bool = False) -> None:
        self._console = console or Console()
        self._verbose = verbose
        # 视频 uid 到序号的映射，用短序号代替冗长的 uid 做行首标识。
        self._slots: dict[str, int] = {}
        self._titles: dict[str, str] = {}

    async def __call__(self, event: Event) -> None:
        line = self._render(event)
        if line:
            self._console.print(line, highlight=False)

    def _slot(self, uid: str) -> str:
        if not uid:
            return "    "
        if uid not in self._slots:
            self._slots[uid] = len(self._slots) + 1
        return f"[{self._slots[uid]:>2}] "

    def _render(self, event: Event) -> str:
        style = _KIND_STYLE.get(event.kind, "")
        prefix = self._slot(event.video_uid)
        stage = STAGE_LABELS.get(event.stage, "") if event.stage else ""
        cost = f" {event.elapsed_sec:.2f}s" if event.elapsed_sec else ""

        if event.kind is EventKind.RUN_START:
            return f"\n[{style}]{event.message}[/{style}]"

        if event.kind is EventKind.RUN_DONE:
            return f"\n[{style}]{event.message}[/{style}]"

        if event.kind is EventKind.VIDEO_START:
            self._titles[event.video_uid] = event.message
            author = event.data.get("author_name", "")
            rank = event.data.get("rank")
            tag = f"#{rank} " if rank else ""
            return (
                f"{prefix}[{style}]{tag}{_ellipsis(event.message, 46)}[/{style}]"
                f" [dim]@{author}[/dim]"
            )

        if event.kind is EventKind.VIDEO_DONE:
            return (
                f"{prefix}[{style}]完成{cost}[/{style}]  "
                f"{_ellipsis(event.message, 60)}"
            )

        if event.kind is EventKind.VIDEO_FAILED:
            return f"{prefix}[{style}]失败[/{style}] {event.message}"

        if event.kind is EventKind.STAGE_SKIPPED:
            return f"{prefix}[{style}]{stage} 跳过[/{style}] {event.message}"

        if event.kind is EventKind.STAGE_FAILED:
            return f"{prefix}[{style}]{stage} 出错[/{style}]{cost} {event.message}"

        if event.kind is EventKind.STAGE_DONE:
            # 归纳阶段的 message 是摘要一句话，已在 VIDEO_DONE 展示过，这里不重复。
            detail = "" if event.stage is Stage.SUMMARIZE else f"  {event.message}"
            return f"{prefix}[{style}]{stage}{cost}{detail}[/{style}]"

        if event.kind is EventKind.LOG:
            return f"{prefix}[{style}]{event.message}[/{style}]"

        if event.kind is EventKind.STAGE_START and self._verbose:
            return f"{prefix}[dim]{stage} 开始…[/dim]"

        return ""


def print_report(run: RunResult, console: Console | None = None) -> None:
    """跑完之后打印结果总表和耗时分解。"""
    console = console or Console()

    for index, result in enumerate(run.results, start=1):
        _print_one(console, index, result)

    _print_timing(console, run)

    console.print(
        f"\n[bold]成功 {len(run.succeeded)}/{len(run.results)}"
        f"（{run.success_rate:.0%}），总耗时 {run.elapsed_sec:.1f}s[/bold]"
    )
    if run.failed:
        console.print("[red]失败明细：[/red]")
        for result in run.failed:
            console.print(f"  {result.item.title[:40]} — {result.error}")


def _print_one(console: Console, index: int, result: VideoResult) -> None:
    item = result.item
    rank = f"#{item.rank} " if item.rank else ""
    console.print(f"\n[bold cyan]{index}. {rank}{item.title}[/bold cyan]")
    console.print(
        f"   [dim]{item.author_name} | 时长 {item.duration_sec}s | "
        f"播放 {item.stats.play:,} | {item.url}[/dim]"
    )

    if not result.ok:
        console.print(f"   [red]处理失败：{result.error}[/red]")
        return

    summary = result.summary
    assert summary is not None
    console.print(f"   [bold]一句话：[/bold]{summary.one_liner}")
    for point in summary.key_points:
        console.print(f"     · {point}")
    meta = [
        f"话题 {'/'.join(summary.topics) or '—'}",
        f"情感 {_SENTIMENT_LABELS.get(summary.sentiment, '中性')}",
        f"推广 {'是' if summary.is_promotion else '否'}",
        f"置信度 {summary.confidence:.2f}",
    ]
    if result.escalated:
        meta.append("[magenta]已升级重做[/magenta]")
    console.print(f"   [dim]{' | '.join(meta)}[/dim]")

    signals = []
    if result.transcript and not result.transcript.is_empty:
        signals.append(f"语音 {len(result.transcript.full_text)} 字")
    else:
        signals.append("[yellow]无有效语音[/yellow]")
    if result.ocr:
        signals.append(f"画面文字 {len(result.ocr.merged_text())} 字")
    console.print(f"   [dim]依据：{' + '.join(signals)}[/dim]")


def _print_timing(console: Console, run: RunResult) -> None:
    """按阶段汇总耗时。

    注意各阶段之和会明显大于墙钟时间，因为多条视频是并发跑的；
    这张表看的是各阶段的相对开销，用于定位瓶颈。
    """
    totals: dict[str, float] = {}
    for result in run.results:
        for stage, cost in result.stage_timings.items():
            totals[stage] = totals.get(stage, 0.0) + cost
    if not totals:
        return

    grand = sum(totals.values())
    table = Table(title="\n阶段耗时（累计，跨视频并发）", title_justify="left")
    table.add_column("阶段")
    table.add_column("累计耗时", justify="right")
    table.add_column("占比", justify="right")

    for stage in Stage:
        cost = totals.get(stage.value)
        if cost is None:
            continue
        table.add_row(
            STAGE_LABELS[stage],
            f"{cost:.2f}s",
            f"{cost / grand:.1%}" if grand else "—",
        )
    table.add_row("[bold]合计[/bold]", f"[bold]{grand:.2f}s[/bold]", "")
    console.print(table)


def _ellipsis(text: str, limit: int) -> str:
    text = text.replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"
