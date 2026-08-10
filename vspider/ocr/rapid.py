"""RapidOCR 关键帧文字识别。"""

from __future__ import annotations

import asyncio
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from vspider.media.keyframe import Keyframe
from vspider.models import OcrFrame, OcrResult

# 常见平台水印和控件文字。
_NOISE_LITERALS = {
    "抖音", "快手", "小红书", "哔哩哔哩", "bilibili", "微博", "douyin", "kuaishou",
    "关注", "已关注", "点赞", "收藏", "分享", "评论", "转发", "更多",
    "作者", "原创", "广告", "推广", "播放", "弹幕", "投币",
    "长按点赞", "双击点赞", "点击关注", "一键三连", "求关注",
}
_WATERMARK_HINT = re.compile(r"(抖音|快手|小红书|bilibili|哔哩哔哩|微博)\s*(号|ID|id)?[:：]?")
_PURE_SYMBOLS = re.compile(r"[\d\s:：.、,，/\\|\-+*%]+")


def _default_workers() -> int:
    # onnxruntime 每个 session 自己也会用多线程，worker 开太多会互相抢核，
    # 反而更慢。取核数的一半、上限 6，是实测比较稳的折中。
    cpu_count = os.cpu_count() or 4
    return max(2, min(6, cpu_count // 2))


class RapidOcr:
    name = "rapidocr"

    def __init__(
        self,
        min_score: float = 0.60,
        min_length: int = 2,
        workers: int | None = None,
        intra_op_threads: int = 2,
    ) -> None:
        self._min_score = min_score
        self._min_length = min_length
        self._workers = workers or _default_workers()
        self._intra_op_threads = intra_op_threads
        self._local = threading.local()
        self._executor: ThreadPoolExecutor | None = None

    def _engine(self) -> Any:
        """取当前线程的引擎，首次访问时创建。"""
        engine = getattr(self._local, "engine", None)
        if engine is None:
            from rapidocr_onnxruntime import RapidOCR

            # 限制每个 session 的算子内并行度，避免 N 个 worker
            # 各自开满线程导致过度订阅。
            engine = RapidOCR(
                intra_op_num_threads=self._intra_op_threads,
                inter_op_num_threads=1,
            )
            self._local.engine = engine
        return engine

    def _pool(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self._workers, thread_name_prefix="ocr"
            )
        return self._executor

    async def recognize(self, frames: list[Keyframe]) -> OcrResult:
        started = time.perf_counter()
        if not frames:
            return OcrResult(backend=self.name, elapsed_sec=0.0)

        loop = asyncio.get_running_loop()
        pool = self._pool()
        tasks = [
            loop.run_in_executor(pool, self._recognize_one, frame.path)
            for frame in frames
        ]
        per_frame = await asyncio.gather(*tasks)

        results = [
            OcrFrame(timestamp=frame.timestamp, texts=texts)
            for frame, texts in zip(frames, per_frame)
            if texts
        ]
        return OcrResult(
            backend=self.name,
            frames=results,
            elapsed_sec=time.perf_counter() - started,
        )

    def _recognize_one(self, image_path: Path) -> list[str]:
        raw, _ = self._engine()(str(image_path))
        if not raw:
            return []

        texts: list[str] = []
        for entry in raw:
            # RapidOCR 返回每项为 [box, text, score]
            if len(entry) < 3:
                continue
            text = str(entry[1]).strip()
            try:
                score = float(entry[2])
            except (TypeError, ValueError):
                continue
            if score < self._min_score or len(text) < self._min_length:
                continue
            if _is_noise(text):
                continue
            texts.append(text)
        return texts

    async def aclose(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=False)
            self._executor = None


def _is_noise(text: str) -> bool:
    stripped = text.strip().strip("#＃").strip()
    if not stripped:
        return True
    if stripped.lower() in _NOISE_LITERALS:
        return True
    if _WATERMARK_HINT.match(stripped):
        return True
    # 纯数字或纯符号（播放量、时间码、进度条数字）不携带内容信息。
    if _PURE_SYMBOLS.fullmatch(stripped):
        return True
    return False
