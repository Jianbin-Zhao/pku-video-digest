"""VSpider 命令行入口。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Coroutine
from datetime import date, datetime
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from vspider.download.base import DownloadMode
from vspider.models import Platform
from vspider.pipeline.console import ConsoleReporter, print_report
from vspider.pipeline.orchestrator import (
    Orchestrator,
    PipelineConfig,
    RunResult,
    VideoResult,
)
from vspider.registry import (
    BROWSER_PLATFORMS,
    Paths,
    build_asr,
    build_downloader,
    build_ocr,
    build_provider,
    build_summarizer,
    resolve_platform,
)
from vspider.settings import configure_stdio, load_env

configure_stdio()

app = typer.Typer(
    add_completion=False,
    help="多平台短视频榜单抓取与内容归纳",
    no_args_is_help=True,
)
console = Console()


def _run_cli(
    coroutine: Coroutine[Any, Any, RunResult], *, verbose: bool
) -> RunResult:
    """执行异步命令；默认给用户简洁错误，调试模式保留完整堆栈。"""
    try:
        return asyncio.run(coroutine)
    except Exception as exc:  # noqa: BLE001
        if verbose:
            raise
        console.print(f"\n[bold red]运行失败[/bold red] {type(exc).__name__}: {exc}")
        raise typer.Exit(1) from None


def _exit_code(result: RunResult, *, allow_empty: bool = False) -> int:
    """有成功结果，或业务上允许空集合时返回成功。"""
    return 0 if result.succeeded or (allow_empty and not result.results) else 1


@contextlib.asynccontextmanager
async def _session_for(platform: Platform, show_browser: bool) -> AsyncIterator[object]:
    """按需开浏览器会话。

    只跑 B 站时完全不启动浏览器——省下好几秒启动时间和几百兆内存。
    """
    if platform not in BROWSER_PLATFORMS:
        yield None
        return

    from vspider.mediacrawler.session import MediaCrawlerSession

    async with MediaCrawlerSession(headless=not show_browser) as session:
        yield session


# fast 模式的 OCR 跳过阈值。
_FAST_OCR_THRESHOLD = 200


def _build(
    platform: Platform,
    session: object | None,
    profile: str,
    model: str,
    device: str,
    audio_only: bool,
    escalate: bool,
    concurrency: int,
    reporter: ConsoleReporter,
    skip_uids: frozenset[str] = frozenset(),
    fast: bool = False,
    digest: bool = False,
    max_duration: int = 1800,
) -> Orchestrator:
    paths = Paths.from_env()

    config = PipelineConfig(
        data_root=paths.data_root,
        download_mode=DownloadMode.AUDIO_ONLY if audio_only else DownloadMode.VIDEO,
        max_download=concurrency,
        max_audio=concurrency,
        max_summarize=max(concurrency, 2),
        max_duration_sec=max_duration,
        enable_escalation=escalate,
        skip_uids=skip_uids,
        enable_digest=digest,
        skip_ocr_if_transcript_chars=_FAST_OCR_THRESHOLD if fast else 0,
    )

    escalated = None
    if escalate:
        from vspider.registry import ESCALATION_MODEL

        escalated = build_summarizer(profile=profile, model=ESCALATION_MODEL)

    return Orchestrator(
        provider=build_provider(platform, session),
        downloader=build_downloader(platform, session=session),
        asr=build_asr(paths, device=device),
        ocr=build_ocr(),
        summarizer=build_summarizer(profile=profile, model=model),
        config=config,
        sink=reporter,
        escalated_summarizer=escalated,
    )


@app.command()
def rank(
    platform: str = typer.Option("bili", "--platform", "-p", help="平台"),
    limit: int = typer.Option(5, "--limit", "-n", help="取前几名"),
    category: str = typer.Option("all", "--category", "-c", help="分区，all 为全站"),
    today_only: bool = typer.Option(
        False, "--today-only", help="只保留今天发布的视频（默认取榜单当前快照）"
    ),
    profile: str = typer.Option("gpu", "--profile", help="归纳后端：api / gpu / cpu"),
    model: str = typer.Option("", "--model", help="覆盖默认归纳模型"),
    device: str = typer.Option("cuda:0", "--device", help="语音识别设备"),
    audio_only: bool = typer.Option(False, "--audio-only", help="只下音频，跳过画面 OCR"),
    escalate: bool = typer.Option(
        False, "--escalate", help="低置信度时自动换更强的模型重做"
    ),
    fast: bool = typer.Option(
        False, "--fast", help="快速模式：转写充分时跳过抽帧与 OCR"
    ),
    digest: bool = typer.Option(
        True, "--digest/--no-digest", help="整批完成后生成跨视频总览"
    ),
    concurrency: int = typer.Option(3, "--concurrency", "-j", help="下载与归纳并发度"),
    max_duration: int = typer.Option(
        1800, "--max-duration", help="视频最大秒数；0 表示不限时，严格保留原榜单名次"
    ),
    resume: bool = typer.Option(
        False, "--resume", help="断点续跑：跳过 SQLite 里已成功归纳过的视频"
    ),
    show_browser: bool = typer.Option(
        False, "--show-browser", help="显示浏览器窗口，用于处理验证码"
    ),
    out: Path = typer.Option(None, "--out", "-o", help="结果 JSON 落盘路径"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """今日榜单前 N 的视频，下载并归纳。"""
    _setup(verbose)
    target = resolve_platform(platform)
    reporter = ConsoleReporter(console=console, verbose=verbose)
    storage, skip_uids = _resume_state(resume)

    async def run() -> RunResult:
        async with _session_for(target, show_browser) as session:
            orchestrator = _build(
                target,
                session,
                profile,
                model,
                device,
                audio_only,
                escalate,
                concurrency,
                reporter,
                skip_uids,
                fast=fast,
                digest=digest,
                max_duration=max_duration,
            )
            try:
                return await orchestrator.run_ranking(
                    limit=limit, category=category, today_only=today_only
                )
            finally:
                await orchestrator.aclose()

    result = _run_cli(run(), verbose=verbose)
    print_report(result, console=console)
    _print_digest(result)
    _dump(result, out)
    _persist(storage, result, mode="rank", platform=platform, profile=profile)
    raise typer.Exit(_exit_code(result, allow_empty=resume))


@app.command()
def creator(
    creator_id: str = typer.Option(..., "--id", help="创作者 ID（B 站为 mid）"),
    platform: str = typer.Option("bili", "--platform", "-p", help="平台"),
    limit: int = typer.Option(20, "--limit", "-n", help="最多取几条"),
    today: bool = typer.Option(False, "--today", help="只要今天发布的"),
    since: str = typer.Option("", "--since", help="起始日期 YYYY-MM-DD，优先于 --today"),
    profile: str = typer.Option("gpu", "--profile", help="归纳后端：api / gpu / cpu"),
    model: str = typer.Option("", "--model", help="覆盖默认归纳模型"),
    device: str = typer.Option("cuda:0", "--device", help="语音识别设备"),
    audio_only: bool = typer.Option(False, "--audio-only", help="只下音频，跳过画面 OCR"),
    escalate: bool = typer.Option(
        False, "--escalate", help="低置信度时自动换更强的模型重做"
    ),
    fast: bool = typer.Option(
        False, "--fast", help="快速模式：转写充分时跳过抽帧与 OCR"
    ),
    digest: bool = typer.Option(
        True, "--digest/--no-digest", help="整批完成后生成跨视频总览"
    ),
    concurrency: int = typer.Option(3, "--concurrency", "-j", help="下载与归纳并发度"),
    max_duration: int = typer.Option(
        1800, "--max-duration", help="视频最大秒数；0 表示不限时"
    ),
    resume: bool = typer.Option(
        False, "--resume", help="断点续跑：跳过 SQLite 里已成功归纳过的视频"
    ),
    show_browser: bool = typer.Option(
        False, "--show-browser", help="显示浏览器窗口，用于处理验证码"
    ),
    out: Path = typer.Option(None, "--out", "-o", help="结果 JSON 落盘路径"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """某个创作者在指定日期之后发布的视频，下载并归纳。"""
    _setup(verbose)
    target = resolve_platform(platform)

    day: date | None = None
    if since:
        try:
            day = datetime.strptime(since, "%Y-%m-%d").date()
        except ValueError as exc:
            raise typer.BadParameter(f"--since 需要 YYYY-MM-DD 格式：{exc}") from exc
    elif today:
        day = date.today()

    reporter = ConsoleReporter(console=console, verbose=verbose)
    storage, skip_uids = _resume_state(resume)

    async def run() -> RunResult:
        async with _session_for(target, show_browser) as session:
            orchestrator = _build(
                target,
                session,
                profile,
                model,
                device,
                audio_only,
                escalate,
                concurrency,
                reporter,
                skip_uids,
                fast=fast,
                digest=digest,
                max_duration=max_duration,
            )
            try:
                return await orchestrator.run_creator(
                    creator_id=creator_id, limit=limit, since=day
                )
            finally:
                await orchestrator.aclose()

    result = _run_cli(run(), verbose=verbose)
    print_report(result, console=console)
    _print_digest(result)
    _dump(result, out)
    _persist(storage, result, mode="creator", platform=platform, profile=profile)
    # 今天无投稿属于正常结果。
    raise typer.Exit(_exit_code(result, allow_empty=True))


@app.command()
def search(
    keyword: str = typer.Option(..., "--keyword", "-k", help="搜索关键词"),
    platforms: str = typer.Option(
        "bili", "--platforms", "-p", help="逗号分隔，可跨平台，如 bili,xhs,wb"
    ),
    limit: int = typer.Option(3, "--limit", "-n", help="每个平台取几条"),
    profile: str = typer.Option("gpu", "--profile", help="归纳后端：api / gpu / cpu"),
    model: str = typer.Option("", "--model", help="覆盖默认归纳模型"),
    device: str = typer.Option("cuda:0", "--device", help="语音识别设备"),
    fast: bool = typer.Option(
        False, "--fast", help="快速模式：转写充分时跳过抽帧与 OCR"
    ),
    digest: bool = typer.Option(
        True, "--digest/--no-digest", help="跨平台汇总后生成整体总览"
    ),
    concurrency: int = typer.Option(3, "--concurrency", "-j", help="下载与归纳并发度"),
    max_duration: int = typer.Option(
        1800, "--max-duration", help="视频最大秒数；0 表示不限时"
    ),
    resume: bool = typer.Option(
        False, "--resume", help="断点续跑：跳过 SQLite 里已成功归纳过的视频"
    ),
    show_browser: bool = typer.Option(
        False, "--show-browser", help="显示浏览器窗口，用于处理验证码"
    ),
    out: Path = typer.Option(None, "--out", "-o", help="结果 JSON 落盘路径"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """场景三（plus 拓展）：按关键词跨平台搜索视频，下载归纳并生成整体总览。

    多个平台串行执行（浏览器登录态同一时刻只能被一个会话使用），
    重后端（语音识别 / OCR / 归纳）只加载一次，全程复用。
    """
    _setup(verbose)
    targets = [resolve_platform(p) for p in platforms.split(",") if p.strip()]
    if not targets:
        raise typer.BadParameter("--platforms 至少要给一个平台")

    reporter = ConsoleReporter(console=console, verbose=verbose)
    storage, skip_uids = _resume_state(resume)
    scenario = f"search:{'+'.join(t.value for t in targets)}:{keyword}"

    async def run() -> RunResult:
        paths = Paths.from_env()
        asr = build_asr(paths, device=device)
        ocr = build_ocr()
        summarizer = build_summarizer(profile=profile, model=model)
        config = PipelineConfig(
            data_root=paths.data_root,
            max_download=concurrency,
            max_audio=concurrency,
            max_summarize=max(concurrency, 2),
            max_duration_sec=max_duration,
            skip_uids=skip_uids,
            enable_digest=False,
            skip_ocr_if_transcript_chars=_FAST_OCR_THRESHOLD if fast else 0,
        )

        started = time.perf_counter()
        all_results: list[VideoResult] = []
        try:
            for target in targets:
                async with _session_for(target, show_browser) as session:
                    provider = build_provider(target, session)
                    downloader = build_downloader(target, session=session)
                    orchestrator = Orchestrator(
                        provider=provider,
                        downloader=downloader,
                        asr=asr,
                        ocr=ocr,
                        summarizer=summarizer,
                        config=config,
                        sink=reporter,
                    )
                    try:
                        partial = await orchestrator.run_search(keyword, limit=limit)
                        all_results.extend(partial.results)
                    except Exception as exc:  # noqa: BLE001
                        console.print(
                            f"[red]{target.value} 搜索失败："
                            f"{type(exc).__name__}: {str(exc)[:160]}[/red]"
                        )
                    finally:
                        for component in (provider, downloader):
                            close = getattr(component, "aclose", None)
                            if close is not None:
                                with contextlib.suppress(Exception):
                                    await close()

            cross_digest = None
            succeeded = [r for r in all_results if r.ok]
            if digest and succeeded:
                from vspider.summarize.digest import build_digest

                console.print("[dim]生成跨平台总览……[/dim]")
                try:
                    cross_digest = await build_digest(summarizer, succeeded, scenario)
                except Exception as exc:  # noqa: BLE001
                    console.print(f"[dim]总览生成失败（不影响结果）：{exc}[/dim]")

            return RunResult(
                results=all_results,
                elapsed_sec=time.perf_counter() - started,
                scenario=scenario,
                digest=cross_digest,
            )
        finally:
            for component in (asr, ocr, summarizer):
                close = getattr(component, "aclose", None)
                if close is not None:
                    with contextlib.suppress(Exception):
                        await close()

    result = _run_cli(run(), verbose=verbose)
    print_report(result, console=console)
    _print_digest(result)
    _dump(result, out)
    _persist(storage, result, mode="search", platform=platforms, profile=profile)
    raise typer.Exit(_exit_code(result, allow_empty=resume))


@app.command()
def report(
    run_id: str = typer.Argument(..., help="运行 ID，传 latest 取最近一次"),
    fmt: str = typer.Option("html", "--format", "-f", help="html / md"),
    out: Path = typer.Option(None, "--out", "-o", help="输出路径，缺省当前目录"),
) -> None:
    """把历史运行导出成可分享的报告（自包含 HTML 或 Markdown）。"""
    configure_stdio()
    load_env()
    from vspider.report import render_html, render_markdown
    from vspider.storage import Storage

    storage = Storage()
    if run_id == "latest":
        runs = storage.list_runs(limit=1)
        if not runs:
            console.print("[red]还没有任何运行记录[/red]")
            raise typer.Exit(1)
        run_id = runs[0]["run_id"]

    row = storage.get_run(run_id)
    if row is None:
        console.print(f"[red]找不到运行记录 {run_id}[/red]")
        raise typer.Exit(1)

    if fmt not in ("html", "md"):
        raise typer.BadParameter("--format 只支持 html / md")
    text = render_html(row) if fmt == "html" else render_markdown(row)
    dest = out or Path(f"report_{run_id}.{fmt}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    console.print(f"报告已导出：{dest}")


def _print_digest(run: RunResult) -> None:
    """批次总览的终端展示。"""
    d = run.digest
    if d is None:
        return
    console.print("\n[bold cyan]—— 批次总览 ——[/bold cyan]")
    if d.headline:
        console.print(f"[bold]{d.headline}[/bold]")
    for theme in d.themes:
        uids = f"（{'、'.join(theme.video_uids)}）" if theme.video_uids else ""
        console.print(f"  · {theme.name}：{theme.description}{uids}")
    for obs in d.observations:
        console.print(f"  - {obs}")
    if d.top_pick_uid:
        console.print(f"  ▶ 优先观看 {d.top_pick_uid}：{d.top_pick_reason}")


def _resume_state(resume: bool) -> tuple[object, frozenset[str]]:
    """打开 SQLite，并在续跑模式下取出已归纳过的 uid 集合。

    落库始终开启（CLI 与 Web 共用同一份 data/vspider.db，历史与续跑打通），
    仅当 --resume 时才用它去重跳过。
    """
    from vspider.storage import Storage

    storage = Storage()
    skip = frozenset(storage.processed_uids()) if resume else frozenset()
    if skip:
        console.print(f"[dim]断点续跑：已归纳 {len(skip)} 条，命中即跳过[/dim]")
    return storage, skip


def _persist(
    storage: object, result: RunResult, *, mode: str, platform: str, profile: str
) -> None:
    meta = {
        "run_id": uuid.uuid4().hex[:12],
        "mode": mode,
        "platform": platform,
        "profile": profile,
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        storage.save_run(result, meta)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        console.print(f"[dim]入库失败（不影响结果）：{exc}[/dim]")


def _setup(verbose: bool) -> None:
    configure_stdio()
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="[!] %(message)s",
    )
    load_env()


def _dump(run: RunResult, out: Path | None) -> None:
    if out is None:
        return
    payload = {
        "scenario": run.scenario,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "elapsed_sec": round(run.elapsed_sec, 2),
        "success_rate": round(run.success_rate, 3),
        "digest": run.digest.model_dump(mode="json") if run.digest else None,
        "videos": [
            {
                "item": r.item.model_dump(mode="json", exclude={"raw"}),
                "summary": r.summary.model_dump(mode="json") if r.summary else None,
                "transcript": (
                    r.transcript.model_dump(mode="json") if r.transcript else None
                ),
                "ocr_text": r.ocr.merged_text() if r.ocr else "",
                "timings": {k: round(v, 3) for k, v in r.stage_timings.items()},
                "escalated": r.escalated,
                "error": r.error,
            }
            for r in run.results
        ],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    console.print(f"\n[dim]结果已写入 {out}[/dim]")


if __name__ == "__main__":
    app()
