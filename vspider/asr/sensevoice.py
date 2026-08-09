"""SenseVoice-Small 语音识别后端。

选它的依据是 FunASR 官方在 184 条中文长音频（共 192 分钟）上的实测：
SenseVoice-Small 在 CPU 上 17.2 倍实时、中文 CER 7.81%，
而 Whisper-large-v3 在 H100 上只有 13.4 倍实时、CER 20.02%。
换句话说这个模型跑在 CPU 上就比 Whisper 跑在顶级 GPU 上又快又准，
原因是它是非自回归结构（一次前向出全部结果），且专门针对中文训练。

实现上刻意分两步走：先用 FSMN-VAD 拿到语音段边界，再逐段识别。
比直接把整个文件丢给模型多写几十行，但换来两个必需的东西：
一是带时间戳的分段（界面上点文稿跳转到视频位置要用），
二是可控的显存占用（SenseVoice 单次前向的开销随音频时长线性增长，
整段丢一个一小时的视频进去会直接 OOM）。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from vspider.asr.base import AsrBackend
from vspider.models import Transcript, TranscriptSegment

# 单段上限 30 秒。太长会顶高峰值显存，太短会切碎语义、拉低识别率。
_MAX_SEGMENT_MS = 30_000
_SAMPLE_RATE = 16_000


class SenseVoiceAsr(AsrBackend):
    name = "sensevoice-small"

    def __init__(
        self,
        model_dir: str,
        vad_dir: str,
        device: str = "cuda:0",
        batch_size: int = 16,
        language: str = "auto",
    ) -> None:
        self._model_dir = model_dir
        self._vad_dir = vad_dir
        self._device = device
        self._batch_size = batch_size
        self._language = language
        self._asr: Any = None
        self._vad: Any = None
        self._postprocess: Any = None

    def _ensure_loaded(self) -> None:
        """首次调用时才加载模型。

        延迟加载让"只跑采集不跑识别"的场景（比如调试榜单接口）
        不必白等十几秒的模型初始化。
        """
        if self._asr is not None:
            return

        from funasr import AutoModel
        from funasr.utils.postprocess_utils import rich_transcription_postprocess

        self._postprocess = rich_transcription_postprocess
        # disable_pbar 是必需的：funasr 每次 generate 都会打一条 tqdm 进度条，
        # 多条视频并发时这些进度条会互相覆盖，把流水线自己的日志彻底冲掉。
        common = {
            "device": self._device,
            "disable_update": True,
            "disable_pbar": True,
            "disable_log": True,
        }
        self._vad = AutoModel(
            model=self._vad_dir,
            max_single_segment_time=_MAX_SEGMENT_MS,
            **common,
        )
        self._asr = AutoModel(model=self._model_dir, **common)

    async def transcribe(self, audio_path: Path) -> Transcript:
        # funasr 是同步且计算密集的，丢到线程池里跑，
        # 否则会把整个 asyncio 事件循环卡住，下载和采集全部停摆。
        return await asyncio.to_thread(self._transcribe_sync, audio_path)

    def _transcribe_sync(self, audio_path: Path) -> Transcript:
        started = time.perf_counter()
        self._ensure_loaded()

        import soundfile

        waveform, sample_rate = soundfile.read(str(audio_path), dtype="float32")
        if waveform.ndim > 1:
            waveform = waveform.mean(axis=1)
        if sample_rate != _SAMPLE_RATE:
            raise ValueError(
                f"{audio_path.name} 采样率是 {sample_rate}，需先转成 {_SAMPLE_RATE}"
            )

        spans = self._detect_spans(audio_path, len(waveform) / sample_rate)
        if not spans:
            return Transcript(
                backend=self.name,
                language=self._language,
                elapsed_sec=time.perf_counter() - started,
            )

        chunks = [
            waveform[int(start * sample_rate) : int(end * sample_rate)]
            for start, end in spans
        ]
        segments: list[TranscriptSegment] = []
        for offset in range(0, len(chunks), self._batch_size):
            batch = chunks[offset : offset + self._batch_size]
            results = self._asr.generate(
                input=batch,
                cache={},
                language=self._language,
                use_itn=True,
                batch_size=len(batch),
                disable_pbar=True,
            )
            for index, result in enumerate(results):
                raw = result.get("text", "") if isinstance(result, dict) else str(result)
                text = self._postprocess(raw).strip()
                if not text:
                    continue
                start, end = spans[offset + index]
                segments.append(TranscriptSegment(start=start, end=end, text=text))

        return Transcript(
            backend=self.name,
            language=self._language,
            full_text=" ".join(s.text for s in segments),
            segments=segments,
            elapsed_sec=time.perf_counter() - started,
        )

    def _detect_spans(self, audio_path: Path, duration: float) -> list[tuple[float, float]]:
        """用 VAD 切出语音段，返回秒为单位的 (start, end)。"""
        results = self._vad.generate(input=str(audio_path), disable_pbar=True)
        spans: list[tuple[float, float]] = []
        for result in results:
            for pair in result.get("value", []) if isinstance(result, dict) else []:
                if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                    continue
                start, end = float(pair[0]) / 1000.0, float(pair[1]) / 1000.0
                # VAD 偶尔给出越界或倒置的区间，这里做一次夹紧，
                # 否则切片会拿到空数组让后面的 batch 维度对不上。
                start = max(0.0, min(start, duration))
                end = max(start, min(end, duration))
                if end - start >= 0.2:
                    spans.append((start, end))
        return spans
