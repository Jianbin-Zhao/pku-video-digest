"""下载、ASR、OCR 和归纳流水线。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from vspider.asr.base import AsrBackend
from vspider.discovery.base import RankingProvider
from vspider.download.base import Downloader, DownloadMode
from vspider.fusion.context import FusionContext
from vspider.media.audio import extract_audio, has_audio_stream, probe_duration
from vspider.media.keyframe import extract_keyframes
from vspider.models import Comment, Digest, OcrResult, Summary, Transcript, VideoItem
from vspider.ocr.rapid import RapidOcr
from vspider.pipeline.events import Event, EventKind, EventSink, Stage, noop_sink
from vspider.summarize.base import Summarizer


@dataclass
class PipelineConfig:
    data_root: Path = Path("data")
    download_mode: DownloadMode = DownloadMode.VIDEO

    max_download: int = 3
    max_audio: int = 3
    max_keyframe: int = 2
    max_summarize: int = 4

    # 0 表示不限时。
    max_duration_sec: int = 1800
    keyframe_max: int = 24

    escalate_below: float = 0.55
    enable_escalation: bool = False

    skip_uids: frozenset[str] = frozenset()

    enable_digest: bool = False

    # 转写达到阈值时跳过 OCR；0 表示关闭。
    skip_ocr_if_transcript_chars: int = 0


@dataclass
class VideoResult:
    item: VideoItem
    video_path: Path | None = None
    audio_path: Path | None = None
    transcript: Transcript | None = None
    ocr: OcrResult | None = None
    comments: list[Comment] = field(default_factory=list)
    summary: Summary | None = None
    stage_timings: dict[str, float] = field(default_factory=dict)
    error: str = ""
    escalated: bool = False

    @property
    def ok(self) -> bool:
        return self.summary is not None and not self.error

    @property
    def total_sec(self) -> float:
        return sum(self.stage_timings.values())


@dataclass
class RunResult:
    results: list[VideoResult]
    elapsed_sec: float
    scenario: str
    digest: Digest | None = None

    @property
    def succeeded(self) -> list[VideoResult]:
        return [r for r in self.results if r.ok]

    @property
    def failed(self) -> list[VideoResult]:
        return [r for r in self.results if not r.ok]

    @property
    def success_rate(self) -> float:
        return len(self.succeeded) / len(self.results) if self.results else 0.0


class Orchestrator:
    def __init__(
        self,
        provider: RankingProvider,
        downloader: Downloader,
        asr: AsrBackend,
        ocr: RapidOcr,
        summarizer: Summarizer,
        config: PipelineConfig | None = None,
        sink: EventSink = noop_sink,
        escalated_summarizer: Summarizer | None = None,
    ) -> None:
        self._provider = provider
        self._downloader = downloader
        self._asr = asr
        self._ocr = ocr
        self._summarizer = summarizer
        self._escalated = escalated_summarizer
        self._config = config or PipelineConfig()
        self._sink = sink

        self._sem_download = asyncio.Semaphore(self._config.max_download)
        self._sem_audio = asyncio.Semaphore(self._config.max_audio)
        self._sem_keyframe = asyncio.Semaphore(self._config.max_keyframe)
        self._sem_summarize = asyncio.Semaphore(self._config.max_summarize)
        # FunASR 不支持并发；OCR 内部已有线程池。
        self._lock_asr = asyncio.Lock()
        self._lock_ocr = asyncio.Lock()

    async def _emit(self, event: Event) -> None:
        await self._sink(event)

    async def run_ranking(
        self,
        limit: int = 5,
        category: str = "all",
        today_only: bool = False,
    ) -> RunResult:
        started = time.perf_counter()
        scenario = f"ranking:{self._provider.platform.value}:{category}"
        await self._emit(
            Event(
                EventKind.RUN_START,
                message=f"拉取{self._provider.platform.value}榜单前 {limit}",
                data={"scenario": scenario, "limit": limit, "today_only": today_only},
            )
        )

        stage_started = time.perf_counter()
        await self._emit(Event(EventKind.STAGE_START, stage=Stage.DISCOVER))
        # 多取一些，避免时长过滤后数量不足。
        raw = await self._provider.fetch_ranking(
            limit=limit * 3, category=category, today_only=today_only
        )
        kept, dropped = self._filter_by_duration(raw)
        items = kept[:limit]
        await self._report_dropped([d for d in dropped if (d.rank or 999) <= limit])
        await self._emit(
            Event(
                EventKind.STAGE_DONE,
                stage=Stage.DISCOVER,
                elapsed_sec=time.perf_counter() - stage_started,
                message=f"命中 {len(items)} 条",
                data={"items": [_item_brief(i) for i in items]},
            )
        )

        return await self._process(items, scenario, started)

    async def run_search(
        self,
        keyword: str,
        limit: int = 5,
    ) -> RunResult:
        """场景三：按关键词全网搜索，下载并归纳。

        题面只要求榜单与创作者两个场景，这是 plus 版的拓展：
        给定任意话题，搜出当前热度最高的几条视频做内容归纳，
        配合批次总览就是一份"某话题今日舆情速览"。
        """
        started = time.perf_counter()
        scenario = f"search:{self._provider.platform.value}:{keyword}"
        await self._emit(
            Event(
                EventKind.RUN_START,
                message=f"在{self._provider.platform.value}搜索「{keyword}」",
                data={"scenario": scenario, "limit": limit, "keyword": keyword},
            )
        )

        stage_started = time.perf_counter()
        await self._emit(Event(EventKind.STAGE_START, stage=Stage.DISCOVER))
        # 搜索结果常混入超长课程，扩大候选池后再筛。
        raw = await self._provider.search_videos(keyword, limit=limit * 6)
        kept, dropped = self._filter_by_duration(raw)
        items = kept[:limit]
        await self._report_dropped([d for d in dropped if (d.rank or 999) <= limit])
        await self._emit(
            Event(
                EventKind.STAGE_DONE,
                stage=Stage.DISCOVER,
                elapsed_sec=time.perf_counter() - stage_started,
                message=f"命中 {len(items)} 条",
                data={"items": [_item_brief(i) for i in items]},
            )
        )

        return await self._process(items, scenario, started)

    async def run_prefetched(
        self,
        prefetched: list[tuple[VideoItem, Path]],
        scenario: str = "prefetched",
    ) -> RunResult:
        """对已经下载好的视频文件跑「理解」部分，跳过发现和下载。

        这是混合部署的落地入口：浏览器平台（抖音/快手/微博/小红书）
        必须在本机采集下载（干净家庭 IP + 登录态），而 ASR/OCR/归纳
        要在 GPU 服务器上算。两台机器之间用「本机下载出文件 + 元数据，
        传到服务器，在服务器上从这里接手」的方式衔接。

        B 站不需要走这条路——它从服务器直连官方接口即可，用 run_ranking。
        """
        started = time.perf_counter()
        await self._emit(
            Event(
                EventKind.RUN_START,
                message=f"对 {len(prefetched)} 个已下载文件做内容理解",
                data={"scenario": scenario},
            )
        )

        async def understand(item: VideoItem, path: Path) -> VideoResult:
            result = VideoResult(item=item, video_path=path)
            await self._emit(
                Event(EventKind.VIDEO_START, video_uid=item.uid, message=item.title)
            )
            try:
                if not path.exists():
                    raise FileNotFoundError(f"找不到已下载文件：{path}")
                await self._stage_audio_and_asr(result)
                await self._stage_keyframe_and_ocr(result)
                await self._stage_summarize(result)
                await self._emit(
                    Event(
                        EventKind.VIDEO_DONE,
                        video_uid=item.uid,
                        elapsed_sec=result.total_sec,
                        message=result.summary.one_liner if result.summary else "",
                        data={"timings": result.stage_timings},
                    )
                )
            except Exception as exc:  # noqa: BLE001
                result.error = f"{type(exc).__name__}: {exc}"
                await self._emit(
                    Event(
                        EventKind.VIDEO_FAILED,
                        video_uid=item.uid,
                        message=result.error,
                    )
                )
            return result

        if self._config.skip_uids:
            skipped = [p for p in prefetched if p[0].uid in self._config.skip_uids]
            for item, _ in skipped:
                await self._emit(
                    Event(
                        EventKind.LOG,
                        video_uid=item.uid,
                        message=f"断点续跑：{item.title} 已归纳过，跳过",
                    )
                )
            prefetched = [p for p in prefetched if p[0].uid not in self._config.skip_uids]

        results = await asyncio.gather(
            *(understand(item, path) for item, path in prefetched)
        )
        digest = await self._maybe_digest(list(results), scenario)
        elapsed = time.perf_counter() - started
        run = RunResult(
            results=list(results),
            elapsed_sec=elapsed,
            scenario=scenario,
            digest=digest,
        )
        await self._emit(
            Event(
                EventKind.RUN_DONE,
                elapsed_sec=elapsed,
                message=f"完成 {len(run.succeeded)}/{len(run.results)}，耗时 {elapsed:.1f}s",
                data={"success_rate": round(run.success_rate, 3), "scenario": scenario},
            )
        )
        return run

    async def run_creator(
        self,
        creator_id: str,
        limit: int = 20,
        since: date | None = None,
    ) -> RunResult:
        started = time.perf_counter()
        scenario = f"creator:{self._provider.platform.value}:{creator_id}"
        await self._emit(
            Event(
                EventKind.RUN_START,
                message=f"拉取创作者 {creator_id} 的作品",
                data={"scenario": scenario, "since": str(since) if since else None},
            )
        )

        stage_started = time.perf_counter()
        await self._emit(Event(EventKind.STAGE_START, stage=Stage.COLLECT))
        raw = await self._provider.fetch_creator_videos(
            creator_id=creator_id, limit=limit, since=since
        )
        items, dropped = self._filter_by_duration(raw)
        await self._report_dropped(dropped)
        await self._emit(
            Event(
                EventKind.STAGE_DONE,
                stage=Stage.COLLECT,
                elapsed_sec=time.perf_counter() - stage_started,
                message=f"命中 {len(items)} 条",
                data={"items": [_item_brief(i) for i in items]},
            )
        )

        return await self._process(items, scenario, started)

    def _filter_by_duration(
        self, items: list[VideoItem]
    ) -> tuple[list[VideoItem], list[VideoItem]]:
        """按时长切成保留和淘汰两组。

        淘汰的那组要单独返回而不是默默丢掉：榜单前几名里出现几小时的
        直播回放是常事，用户看到结果从第 2 名开始必须知道第 1 名去哪了。
        """
        limit = self._config.max_duration_sec
        if limit <= 0:
            return list(items), []
        kept: list[VideoItem] = []
        dropped: list[VideoItem] = []
        for item in items:
            (kept if item.duration_sec <= limit else dropped).append(item)
        return kept, dropped

    async def _drop_processed(self, items: list[VideoItem]) -> list[VideoItem]:
        """断点续跑：滤掉此前已成功归纳的视频，并逐条发 LOG 说明跳过原因。"""
        skip = self._config.skip_uids
        if not skip:
            return items
        kept: list[VideoItem] = []
        for item in items:
            if item.uid in skip:
                await self._emit(
                    Event(
                        EventKind.LOG,
                        video_uid=item.uid,
                        message=f"断点续跑：{item.title} 已归纳过，跳过",
                    )
                )
            else:
                kept.append(item)
        return kept

    async def _report_dropped(self, dropped: list[VideoItem]) -> None:
        for item in dropped:
            await self._emit(
                Event(
                    EventKind.LOG,
                    stage=Stage.DISCOVER,
                    message=(
                        f"跳过 #{item.rank} {item.title}"
                        f"（时长 {item.duration_sec}s 超过上限 "
                        f"{self._config.max_duration_sec}s）"
                    ),
                )
            )

    async def _process(
        self, items: list[VideoItem], scenario: str, started: float
    ) -> RunResult:
        items = await self._drop_processed(items)
        results = await asyncio.gather(
            *(self._process_one(item) for item in items), return_exceptions=False
        )
        digest = await self._maybe_digest(list(results), scenario)
        elapsed = time.perf_counter() - started
        run = RunResult(
            results=list(results),
            elapsed_sec=elapsed,
            scenario=scenario,
            digest=digest,
        )
        await self._emit(
            Event(
                EventKind.RUN_DONE,
                elapsed_sec=elapsed,
                message=(
                    f"完成 {len(run.succeeded)}/{len(run.results)}，"
                    f"耗时 {elapsed:.1f}s"
                ),
                data={
                    "success_rate": round(run.success_rate, 3),
                    "scenario": scenario,
                },
            )
        )
        return run

    async def _maybe_digest(
        self, results: list[VideoResult], scenario: str
    ) -> Digest | None:
        """整批归纳完成后按需生成跨视频总览。

        失败不抛出：总览是锦上添花，不能让它拖垮已经完成的整批结果。
        单条成功也照做——"这批只有一条"本身也是有效的总览输入。
        """
        if not self._config.enable_digest:
            return None
        succeeded = [r for r in results if r.ok]
        if not succeeded:
            return None

        from vspider.summarize.digest import build_digest

        await self._emit(Event(EventKind.STAGE_START, stage=Stage.DIGEST))
        started = time.perf_counter()
        try:
            digest = await build_digest(self._summarizer, succeeded, scenario)
        except Exception as exc:  # noqa: BLE001
            await self._emit(
                Event(
                    EventKind.STAGE_FAILED,
                    stage=Stage.DIGEST,
                    elapsed_sec=time.perf_counter() - started,
                    message=f"{type(exc).__name__}: {exc}",
                )
            )
            return None
        await self._emit(
            Event(
                EventKind.STAGE_DONE,
                stage=Stage.DIGEST,
                elapsed_sec=digest.elapsed_sec,
                message=digest.headline,
                data={"digest": digest.model_dump(mode="json")},
            )
        )
        return digest

    async def _process_one(self, item: VideoItem) -> VideoResult:
        result = VideoResult(item=item)
        await self._emit(
            Event(
                EventKind.VIDEO_START,
                video_uid=item.uid,
                message=item.title,
                data=_item_brief(item),
            )
        )
        try:
            await self._stage_download(result)
            await self._stage_audio_and_asr(result)
            await self._stage_keyframe_and_ocr(result)
            await self._stage_summarize(result)
            await self._emit(
                Event(
                    EventKind.VIDEO_DONE,
                    video_uid=item.uid,
                    elapsed_sec=result.total_sec,
                    message=result.summary.one_liner if result.summary else "",
                    data={
                        "timings": result.stage_timings,
                        "escalated": result.escalated,
                    },
                )
            )
        except Exception as exc:  # noqa: BLE001
            # 单条失败不影响整批。
            result.error = f"{type(exc).__name__}: {exc}"
            await self._emit(
                Event(
                    EventKind.VIDEO_FAILED,
                    video_uid=item.uid,
                    message=result.error,
                    data={"title": item.title},
                )
            )
        return result

    async def _timed(self, result: VideoResult, stage: Stage, coro_factory):  # noqa: ANN001
        await self._emit(Event(EventKind.STAGE_START, stage=stage, video_uid=result.item.uid))
        started = time.perf_counter()
        try:
            value = await coro_factory()
        except Exception as exc:  # noqa: BLE001
            elapsed = time.perf_counter() - started
            result.stage_timings[stage.value] = elapsed
            await self._emit(
                Event(
                    EventKind.STAGE_FAILED,
                    stage=stage,
                    video_uid=result.item.uid,
                    elapsed_sec=elapsed,
                    message=f"{type(exc).__name__}: {exc}",
                )
            )
            raise
        elapsed = time.perf_counter() - started
        result.stage_timings[stage.value] = elapsed
        return value, elapsed

    async def _stage_download(self, result: VideoResult) -> None:
        async def run():  # noqa: ANN202
            async with self._sem_download:
                return await self._downloader.download(
                    result.item,
                    self._config.data_root / "videos",
                    mode=self._config.download_mode,
                )

        download, elapsed = await self._timed(result, Stage.DOWNLOAD, run)
        result.video_path = download.path
        await self._emit(
            Event(
                EventKind.STAGE_DONE,
                stage=Stage.DOWNLOAD,
                video_uid=result.item.uid,
                elapsed_sec=elapsed,
                message=f"{download.size_bytes / 1024 / 1024:.1f} MB",
                data={"size_bytes": download.size_bytes, "path": str(download.path)},
            )
        )

    async def _stage_audio_and_asr(self, result: VideoResult) -> None:
        assert result.video_path is not None
        video_path = result.video_path

        if not await has_audio_stream(video_path):
            # 无音轨时继续走 OCR。
            await self._emit(
                Event(
                    EventKind.STAGE_SKIPPED,
                    stage=Stage.ASR,
                    video_uid=result.item.uid,
                    message="视频无音轨，交由画面文字识别处理",
                )
            )
            return

        async def run_audio():  # noqa: ANN202
            async with self._sem_audio:
                dest = (
                    self._config.data_root
                    / "audio"
                    / f"{result.item.platform.value}_{result.item.video_id}.wav"
                )
                return await extract_audio(video_path, dest)

        audio_path, elapsed = await self._timed(result, Stage.AUDIO, run_audio)
        result.audio_path = audio_path
        await self._emit(
            Event(
                EventKind.STAGE_DONE,
                stage=Stage.AUDIO,
                video_uid=result.item.uid,
                elapsed_sec=elapsed,
            )
        )

        async def run_asr():  # noqa: ANN202
            async with self._lock_asr:
                return await self._asr.transcribe(audio_path)

        transcript, elapsed = await self._timed(result, Stage.ASR, run_asr)
        result.transcript = transcript
        duration = float(result.item.duration_sec) or await probe_duration(video_path)
        speed = duration / transcript.elapsed_sec if transcript.elapsed_sec else 0.0
        await self._emit(
            Event(
                EventKind.STAGE_DONE,
                stage=Stage.ASR,
                video_uid=result.item.uid,
                elapsed_sec=elapsed,
                message=(
                    f"{len(transcript.segments)} 段 / {len(transcript.full_text)} 字"
                    f"，{speed:.1f}× 实时"
                ),
                data={
                    "segments": len(transcript.segments),
                    "chars": len(transcript.full_text),
                    "realtime_factor": round(speed, 2),
                    "empty": transcript.is_empty,
                },
            )
        )

    async def _stage_keyframe_and_ocr(self, result: VideoResult) -> None:
        assert result.video_path is not None
        video_path = result.video_path
        item = result.item

        threshold = self._config.skip_ocr_if_transcript_chars
        title = (item.title or item.desc or "").strip()
        weak_metadata = not title or title.startswith(
            ("（无标题视频笔记", "无标题视频笔记")
        )
        # 无标题视频提高阈值，保留 OCR 兜底。
        effective_threshold = threshold * 2 if weak_metadata else threshold
        if (
            threshold > 0
            and result.transcript is not None
            and len(result.transcript.full_text) >= effective_threshold
        ):
            await self._emit(
                Event(
                    EventKind.STAGE_SKIPPED,
                    stage=Stage.OCR,
                    video_uid=item.uid,
                    message=(
                        f"快速模式：转写已有 {len(result.transcript.full_text)} 字"
                        f"（≥{effective_threshold}），跳过抽帧与画面识别"
                    ),
                )
            )
            return

        async def run_keyframe():  # noqa: ANN202
            async with self._sem_keyframe:
                dest = (
                    self._config.data_root
                    / "frames"
                    / f"{item.platform.value}_{item.video_id}"
                )
                return await extract_keyframes(
                    video_path, dest, max_frames=self._config.keyframe_max
                )

        frames, elapsed = await self._timed(result, Stage.KEYFRAME, run_keyframe)
        await self._emit(
            Event(
                EventKind.STAGE_DONE,
                stage=Stage.KEYFRAME,
                video_uid=item.uid,
                elapsed_sec=elapsed,
                message=f"{len(frames)} 帧",
                data={"frames": len(frames)},
            )
        )

        async def run_ocr():  # noqa: ANN202
            async with self._lock_ocr:
                return await self._ocr.recognize(frames)

        ocr_result, elapsed = await self._timed(result, Stage.OCR, run_ocr)
        result.ocr = ocr_result
        merged = ocr_result.merged_text()
        await self._emit(
            Event(
                EventKind.STAGE_DONE,
                stage=Stage.OCR,
                video_uid=item.uid,
                elapsed_sec=elapsed,
                message=f"{len(ocr_result.frames)}/{len(frames)} 帧有文字，去重后 {len(merged)} 字",
                data={"frames_with_text": len(ocr_result.frames), "chars": len(merged)},
            )
        )

    async def _stage_summarize(self, result: VideoResult) -> None:
        context = FusionContext(
            item=result.item,
            transcript=result.transcript,
            ocr=result.ocr,
            comments=result.comments,
        )

        async def run() -> Summary:
            async with self._sem_summarize:
                summary = await self._summarizer.summarize(context)

            should_escalate = (
                self._config.enable_escalation
                and self._escalated is not None
                and summary.confidence < self._config.escalate_below
            )
            if not should_escalate:
                return summary

            # 低置信度时重做一次。
            await self._emit(
                Event(
                    EventKind.LOG,
                    stage=Stage.SUMMARIZE,
                    video_uid=result.item.uid,
                    message=(
                        f"置信度 {summary.confidence} 低于阈值 "
                        f"{self._config.escalate_below}，升级重做"
                    ),
                )
            )
            assert self._escalated is not None
            async with self._sem_summarize:
                upgraded = await self._escalated.summarize(context)
            if upgraded.confidence >= summary.confidence:
                result.escalated = True
                return upgraded
            return summary

        summary, elapsed = await self._timed(result, Stage.SUMMARIZE, run)
        result.summary = summary

        await self._emit(
            Event(
                EventKind.STAGE_DONE,
                stage=Stage.SUMMARIZE,
                video_uid=result.item.uid,
                elapsed_sec=elapsed,
                message=summary.one_liner,
                data={
                    "confidence": summary.confidence,
                    "signal_sources": context.signal_sources,
                    "escalated": result.escalated,
                    "summary": summary.model_dump(mode="json"),
                },
            )
        )

    async def aclose(self, *, shared: bool = True) -> None:
        """关闭持有的组件。

        Args:
            shared: 是否连推理后端（ASR/OCR/归纳器）一起关。Web 服务把这些
                重后端缓存起来跨 run 复用，必须传 False 只关采集器与下载器，
                否则第二次运行会拿到一个 client 已关闭的归纳器直接失败。
        """
        closables: tuple[object | None, ...] = (self._provider, self._downloader)
        if shared:
            closables += (self._asr, self._ocr, self._summarizer, self._escalated)
        for closable in closables:
            if closable is None:
                continue
            close = getattr(closable, "aclose", None)
            if close is not None:
                try:
                    await close()
                except Exception:  # noqa: BLE001, S110
                    pass


def _item_brief(item: VideoItem) -> dict[str, object]:
    return {
        "uid": item.uid,
        "platform": item.platform.value,
        "video_id": item.video_id,
        "title": item.title,
        "url": item.url,
        "author_name": item.author_name,
        "cover_url": item.cover_url,
        "duration_sec": item.duration_sec,
        "rank": item.rank,
        "rank_source": item.rank_source.value if item.rank_source else None,
        "publish_time": item.publish_time.isoformat() if item.publish_time else None,
        "stats": item.stats.model_dump(),
    }
