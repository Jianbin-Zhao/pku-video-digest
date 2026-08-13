"""FastAPI Web 与 SSE 接口。"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from vspider.models import Platform, VideoItem
from vspider.pipeline.events import Event, EventKind
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
from vspider.settings import load_env, local_now_naive, local_today
from vspider.storage import Storage

STATIC_DIR = Path(__file__).resolve().parent / "static"


class _NoDiscovery:
    """understand 模式用不到发现/下载，占位满足编排器构造签名。"""

    platform = Platform.BILIBILI

    async def fetch_ranking(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
        return []

    async def fetch_creator_videos(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
        return []


class RunRequest(BaseModel):
    mode: str = "rank"  # rank | creator | search | understand
    platform: str = "bili"
    profile: str = "gpu"  # api | gpu | cpu
    model: str = ""
    device: str = "cuda:0"
    limit: int = Field(default=5, ge=1)
    category: str = "all"
    today: bool = False
    creator_id: str = ""
    keyword: str = ""  # search 模式的关键词
    handoff_dir: str = ""
    concurrency: int = Field(default=3, ge=1)
    resume: bool = False  # 断点续跑:跳过 SQLite 里已成功归纳过的视频
    fast: bool = False  # 快速模式:转写充分时跳过抽帧与 OCR
    digest: bool = True  # 整批完成后生成跨视频总览


@dataclass
class RunState:
    run_id: str
    meta: dict[str, Any]
    events: list[dict[str, Any]] = field(default_factory=list)
    status: str = "running"  # running | done | error
    result: dict[str, Any] | None = None
    error: str = ""
    _updated: asyncio.Event = field(default_factory=asyncio.Event)

    def push(self, event_dict: dict[str, Any]) -> None:
        self.events.append(event_dict)
        self._updated.set()

    @property
    def finished(self) -> bool:
        return self.status in ("done", "error")


class ComponentCache:
    """缓存重后端，避免每个 run 重载模型。"""

    def __init__(self) -> None:
        self._asr: dict[str, Any] = {}
        self._ocr: Any = None
        self._summarizer: dict[tuple[str, str], Any] = {}
        self._paths = Paths.from_env()

    def asr(self, device: str) -> Any:
        if device not in self._asr:
            self._asr[device] = build_asr(self._paths, device=device)
        return self._asr[device]

    def ocr(self) -> Any:
        if self._ocr is None:
            self._ocr = build_ocr()
        return self._ocr

    def summarizer(self, profile: str, model: str) -> Any:
        key = (profile, model)
        if key not in self._summarizer:
            self._summarizer[key] = build_summarizer(profile=profile, model=model)
        return self._summarizer[key]

    @property
    def paths(self) -> Paths:
        return self._paths


class RunManager:
    def __init__(self, storage: Storage) -> None:
        self._runs: dict[str, RunState] = {}
        self._cache = ComponentCache()
        self._storage = storage
        # ASR and the local LLM are shared across runs; serialize whole runs so
        # two Web requests cannot oversubscribe the same GPU unexpectedly.
        self._run_slot = asyncio.Semaphore(1)

    def get(self, run_id: str) -> RunState | None:
        return self._runs.get(run_id)

    def start(self, req: RunRequest) -> RunState:
        run_id = uuid.uuid4().hex[:12]
        meta = {
            "run_id": run_id,
            "mode": req.mode,
            "platform": req.platform,
            "profile": req.profile,
            "started_at": local_now_naive().isoformat(timespec="seconds"),
        }
        state = RunState(run_id=run_id, meta=meta)
        self._runs[run_id] = state
        asyncio.create_task(self._run(state, req))
        return state

    async def _sink(self, state: RunState, event: Event) -> None:
        state.push(event.to_dict())

    async def _run(self, state: RunState, req: RunRequest) -> None:
        queued = self._run_slot.locked()
        if queued:
            state.push(
                Event(
                    EventKind.LOG,
                    message="任务已入队，等待共享 GPU 资源",
                ).to_dict()
            )
        async with self._run_slot:
            if queued:
                state.push(
                    Event(EventKind.LOG, message="任务开始执行").to_dict()
                )
            await self._run_locked(state, req)

    async def _run_locked(self, state: RunState, req: RunRequest) -> None:
        try:
            platform = resolve_platform(req.platform)
            orchestrator, closer = await self._build(platform, req, state)
            try:
                if req.mode == "understand":
                    run = await self._run_understand(orchestrator, req)
                elif req.mode == "creator":
                    day = local_today() if req.today else None
                    run = await orchestrator.run_creator(
                        creator_id=req.creator_id, limit=req.limit, since=day
                    )
                elif req.mode == "search":
                    run = await orchestrator.run_search(
                        keyword=req.keyword, limit=req.limit
                    )
                else:
                    run = await orchestrator.run_ranking(
                        limit=req.limit, category=req.category, today_only=req.today
                    )
            finally:
                # 重后端由缓存复用。
                await orchestrator.aclose(shared=False)
                if closer is not None:
                    await closer()

            state.result = _run_to_dict(run, state.meta)
            state.status = "done"
            self._storage.save_run(run, state.meta)
        except Exception as exc:  # noqa: BLE001
            state.error = f"{type(exc).__name__}: {exc}"
            state.status = "error"
            state.push(
                Event(EventKind.LOG, message=f"运行失败：{state.error}").to_dict()
            )
        finally:
            state._updated.set()

    async def _build(
        self, platform: Platform, req: RunRequest, state: RunState
    ):  # noqa: ANN201
        """构造 orchestrator。返回 (orchestrator, 额外清理协程 or None)。"""
        paths = self._cache.paths
        skip = frozenset(self._storage.processed_uids()) if req.resume else frozenset()
        config = PipelineConfig(
            data_root=paths.data_root,
            max_download=req.concurrency,
            max_audio=req.concurrency,
            max_summarize=max(req.concurrency, 2),
            skip_uids=skip,
            enable_digest=req.digest,
            skip_ocr_if_transcript_chars=200 if req.fast else 0,
        )

        async def sink(event: Event) -> None:
            await self._sink(state, event)

        asr = self._cache.asr(req.device)
        ocr = self._cache.ocr()
        summarizer = self._cache.summarizer(req.profile, req.model)

        if req.mode == "understand":
            return (
                Orchestrator(
                    provider=_NoDiscovery(),
                    downloader=None,  # type: ignore[arg-type]
                    asr=asr,
                    ocr=ocr,
                    summarizer=summarizer,
                    config=config,
                    sink=sink,
                ),
                None,
            )

        session = None
        closer = None
        if platform in BROWSER_PLATFORMS:
            from vspider.mediacrawler.session import MediaCrawlerSession

            headed = os.environ.get("VSPIDER_WEB_HEADED", "") == "1"
            sess = MediaCrawlerSession(headless=not headed)
            session = await sess.__aenter__()

            async def _close() -> None:
                await sess.__aexit__(None, None, None)

            closer = _close

        orchestrator = Orchestrator(
            provider=build_provider(platform, session),
            downloader=build_downloader(platform, session=session),
            asr=asr,
            ocr=ocr,
            summarizer=summarizer,
            config=config,
            sink=sink,
        )
        return orchestrator, closer

    async def _run_understand(
        self, orchestrator: Orchestrator, req: RunRequest
    ) -> RunResult:
        handoff = Path(req.handoff_dir)
        manifest = handoff / "items.json"
        if not manifest.exists():
            raise FileNotFoundError(f"找不到清单 {manifest}")
        records = json.loads(manifest.read_text(encoding="utf-8"))
        prefetched: list[tuple[VideoItem, Path]] = []
        for record in records:
            local_file = record.pop("_local_file", "")
            path = handoff / local_file
            if local_file and path.exists():
                prefetched.append((VideoItem.model_validate(record), path))
        if not prefetched:
            if records:
                raise FileNotFoundError("清单里有记录，但本地视频文件缺失")
            return RunResult(
                results=[],
                elapsed_sec=0.0,
                scenario=f"understand:{handoff.name}",
            )
        return await orchestrator.run_prefetched(
            prefetched, scenario=f"understand:{handoff.name}"
        )


def _run_to_dict(run: RunResult, meta: dict[str, Any]) -> dict[str, Any]:
    return {
        **meta,
        "scenario": run.scenario,
        "elapsed_sec": round(run.elapsed_sec, 2),
        "success_rate": round(run.success_rate, 3),
        "digest": run.digest.model_dump(mode="json") if run.digest else None,
        "videos": [_video_to_dict(r) for r in run.results],
    }


def _video_to_dict(r: VideoResult) -> dict[str, Any]:
    return {
        "item": r.item.model_dump(mode="json", exclude={"raw"}),
        "summary": r.summary.model_dump(mode="json") if r.summary else None,
        "ocr_text": r.ocr.merged_text() if r.ocr else "",
        "transcript_chars": len(r.transcript.full_text) if r.transcript else 0,
        "timings": {k: round(v, 3) for k, v in r.stage_timings.items()},
        "error": r.error,
    }


def create_app() -> FastAPI:
    load_env()
    app = FastAPI(title="vspider", docs_url="/docs")
    storage = Storage()
    manager = RunManager(storage)

    allow_browser = os.environ.get("VSPIDER_WEB_BROWSER", "") == "1"

    _LOCAL_LLM = {
        "gpu": ("VSPIDER_VLLM_BASE_URL", "http://127.0.0.1:8000/v1", "vLLM"),
        "cpu": ("VSPIDER_LLAMA_BASE_URL", "http://127.0.0.1:8080/v1", "llama.cpp"),
    }

    async def _ensure_llm_reachable(profile: str) -> None:
        if profile not in _LOCAL_LLM:
            return
        env_name, fallback, engine = _LOCAL_LLM[profile]
        base = (os.environ.get(env_name) or fallback).rstrip("/")
        hint = (
            f"该引擎通常部署在 GPU 服务器上；本机运行请改选 api 档，"
            f"或先启动本地 {engine} 服务（也可在 .env 用 {env_name} 指向可用地址）。"
        )
        try:
            async with httpx.AsyncClient(timeout=3.0, trust_env=False) as client:
                resp = await client.get(f"{base}/models")
        except httpx.TransportError as exc:
            raise HTTPException(
                400,
                detail=(
                    f"{profile} 档的归纳引擎连不上（{engine} @ {base}，"
                    f"{type(exc).__name__}）。{hint}"
                ),
            ) from exc
        ok = resp.status_code == 200 and resp.headers.get(
            "content-type", ""
        ).startswith("application/json")
        if not ok:
            raise HTTPException(
                400,
                detail=(
                    f"{base} 有服务在监听但不是 {engine}"
                    f"（GET /models 返回 HTTP {resp.status_code}）。"
                    f"这个端口可能被其他程序占用。{hint}"
                ),
            )

    @app.post("/api/run")
    async def start_run(req: RunRequest) -> dict[str, str]:
        await _ensure_llm_reachable(req.profile)
        platform = resolve_platform(req.platform)
        if (
            req.mode in ("rank", "creator", "search")
            and platform in BROWSER_PLATFORMS
            and not allow_browser
        ):
            raise HTTPException(
                400,
                detail=(
                    f"{platform.value} 需要浏览器登录态，服务器直连不可用。"
                    f"请在本机跑 scripts/fetch_local.py 采集，再用 understand 模式指向该目录；"
                    f"或在本机部署 Web 端并设置 VSPIDER_WEB_BROWSER=1。"
                ),
            )
        state = manager.start(req)
        return {"run_id": state.run_id}

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str) -> dict[str, Any]:
        state = manager.get(run_id)
        if state is None:
            raise HTTPException(404, "run 不存在")
        return {
            "run_id": run_id,
            "status": state.status,
            "error": state.error,
            "result": state.result,
            "event_count": len(state.events),
        }

    @app.get("/api/runs/{run_id}/events")
    async def stream_events(run_id: str) -> StreamingResponse:
        state = manager.get(run_id)
        if state is None:
            raise HTTPException(404, "run 不存在")

        async def gen():  # noqa: ANN202
            idx = 0
            while True:
                while idx < len(state.events):
                    payload = json.dumps(state.events[idx], ensure_ascii=False)
                    yield f"data: {payload}\n\n"
                    idx += 1
                if state.finished and idx >= len(state.events):
                    final = json.dumps(
                        {"status": state.status, "result": state.result},
                        ensure_ascii=False,
                    )
                    yield f"event: end\ndata: {final}\n\n"
                    break
                state._updated.clear()
                try:
                    await asyncio.wait_for(state._updated.wait(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/history")
    async def history(limit: int = 50) -> list[dict[str, Any]]:
        return storage.list_runs(limit=limit)

    @app.get("/api/history/{run_id}")
    async def history_detail(run_id: str) -> dict[str, Any]:
        row = storage.get_run(run_id)
        if row is None:
            raise HTTPException(404, "记录不存在")
        return row

    @app.get("/api/history/{run_id}/report.{fmt}")
    async def export_report(run_id: str, fmt: str) -> Response:
        """把历史运行导出成报告。html 直接在浏览器打开，md 触发下载。"""
        if fmt not in ("html", "md"):
            raise HTTPException(400, "格式只支持 html / md")
        row = storage.get_run(run_id)
        if row is None:
            raise HTTPException(404, "记录不存在")

        from vspider.report import render_html, render_markdown

        if fmt == "html":
            return HTMLResponse(render_html(row))
        return Response(
            render_markdown(row),
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="report_{run_id}.md"'
            },
        )

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app


app = create_app()
