"""命令行入口。

两条命令直接对应题面的两个验收场景：

    vspider rank    --platform bili --limit 5           今日榜单前 5，下载并归纳
    vspider creator --platform bili --id <mid> --today  某用户今天发布的视频

五个平台共用这两条命令，差别被封在 registry 里：B 站直连官方接口，
抖音/快手/微博/小红书要一个活的浏览器会话来算签名。
浏览器会话必须贯穿整个运行——签名依赖页面里的 JS 环境，
页面一关，后续所有请求都会失败。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import date, datetime
from pathlib import Path

import typer
from rich.console import Console

from vspider.download.base import DownloadMode
from vspider.models import Platform
from vspider.pipeline.console import ConsoleReporter, print_report
from vspider.pipeline.orchestrator import Orchestrator, PipelineConfig, RunResult
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

app = typer.Typer(
    add_completion=False,
    help="多平台短视频榜单抓取与内容归纳",
    no_args_is_help=True,
)
console = Console()


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
) -> Orchestrator:
    paths = Paths.from_env()

    config = PipelineConfig(
        data_root=paths.data_root,
        # 只要文字归纳时可以只下音频（体积通常只有完整视频的 2%~5%），
        # 但那样就没有画面可供 OCR，无人声视频会彻底失去信息来源，
        # 所以默认取完整视频。
        download_mode=DownloadMode.AUDIO_ONLY if audio_only else DownloadMode.VIDEO,
        max_download=concurrency,
        max_audio=concurrency,
        max_summarize=max(concurrency, 2),
        enable_escalation=escalate,
        skip_uids=skip_uids,
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
    profile: str = typer.Option("api", "--profile", help="归纳后端：api / gpu / cpu"),
    model: str = typer.Option("", "--model", help="覆盖默认归纳模型"),
    device: str = typer.Option("cuda:0", "--device", help="语音识别设备"),
    audio_only: bool = typer.Option(False, "--audio-only", help="只下音频，跳过画面 OCR"),
    escalate: bool = typer.Option(
        False, "--escalate", help="低置信度时自动换更强的模型重做"
    ),
    concurrency: int = typer.Option(3, "--concurrency", "-j", help="下载与归纳并发度"),
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
            )
            try:
                return await orchestrator.run_ranking(
                    limit=limit, category=category, today_only=today_only
                )
            finally:
                await orchestrator.aclose()

    result = asyncio.run(run())
    print_report(result, console=console)
    _dump(result, out)
    _persist(storage, result, mode="rank", platform=platform, profile=profile)
    raise typer.Exit(0 if result.succeeded else 1)


@app.command()
def creator(
    creator_id: str = typer.Option(..., "--id", help="创作者 ID（B 站为 mid）"),
    platform: str = typer.Option("bili", "--platform", "-p", help="平台"),
    limit: int = typer.Option(20, "--limit", "-n", help="最多取几条"),
    today: bool = typer.Option(False, "--today", help="只要今天发布的"),
    since: str = typer.Option("", "--since", help="起始日期 YYYY-MM-DD，优先于 --today"),
    profile: str = typer.Option("api", "--profile", help="归纳后端：api / gpu / cpu"),
    model: str = typer.Option("", "--model", help="覆盖默认归纳模型"),
    device: str = typer.Option("cuda:0", "--device", help="语音识别设备"),
    audio_only: bool = typer.Option(False, "--audio-only", help="只下音频，跳过画面 OCR"),
    escalate: bool = typer.Option(
        False, "--escalate", help="低置信度时自动换更强的模型重做"
    ),
    concurrency: int = typer.Option(3, "--concurrency", "-j", help="下载与归纳并发度"),
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
            )
            try:
                return await orchestrator.run_creator(
                    creator_id=creator_id, limit=limit, since=day
                )
            finally:
                await orchestrator.aclose()

    result = asyncio.run(run())
    print_report(result, console=console)
    _dump(result, out)
    _persist(storage, result, mode="creator", platform=platform, profile=profile)
    raise typer.Exit(0 if result.succeeded else 1)


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
    # 采集层用 warning 报告「某个热词被限流了」这类可恢复问题。
    # 不打开的话，被平台拒绝会表现成安静的「没有结果」，无从排查。
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
