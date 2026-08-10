"""浏览器平台直链下载后端。"""

from __future__ import annotations

import asyncio
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from vspider.download.base import (
    DownloadError,
    Downloader,
    DownloadMode,
    DownloadResult,
)
from vspider.models import Platform, VideoItem

_REFERERS: dict[Platform, str] = {
    Platform.DOUYIN: "https://www.douyin.com/",
    Platform.KUAISHOU: "https://www.kuaishou.com/",
    Platform.WEIBO: "https://m.weibo.cn/",
    Platform.XHS: "https://www.xiaohongshu.com/",
}

_SUPPORTED = frozenset(_REFERERS)
_CHUNK = 1 << 18


def _extractor_for(platform: Platform) -> Callable[[dict[str, Any]], str]:
    """按平台取直链提取函数。

    提取逻辑放在各平台的 discovery 模块里，和字段映射待在一起——
    它们依据的是同一份原始结构，分开放迟早会漂移。
    """
    if platform is Platform.DOUYIN:
        from vspider.discovery.douyin import extract_video_url
    elif platform is Platform.KUAISHOU:
        from vspider.discovery.kuaishou import extract_video_url
    elif platform is Platform.WEIBO:
        from vspider.discovery.weibo import extract_video_url
    elif platform is Platform.XHS:
        from vspider.discovery.xhs import extract_video_url
    else:
        raise DownloadError(f"{platform.value} 没有直链提取实现")
    return extract_video_url


class DirectUrlDownloader(Downloader):
    name = "direct"

    def __init__(
        self,
        session: object | None = None,
        timeout: int = 60,
        retries: int = 3,
    ) -> None:
        """
        Args:
            session: MediaCrawlerSession。用于取该平台真实的 cookie 与 UA；
                不传则用通用请求头，抖音大概率会 403。
        """
        self._session = session
        self._timeout = timeout
        self._retries = retries

    def supports(self, platform: Platform) -> bool:
        return platform in _SUPPORTED

    async def download(
        self,
        item: VideoItem,
        dest_dir: Path,
        mode: DownloadMode = DownloadMode.VIDEO,
    ) -> DownloadResult:
        if not self.supports(item.platform):
            raise DownloadError(f"{item.platform.value} 不由直链后端处理")

        dest_dir.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()

        url = _extractor_for(item.platform)(item.raw)
        if not url:
            url = await self._hydrate(item)
        if not url:
            raise DownloadError(
                f"{item.uid} 的原始数据里没有视频直链。"
                f"常见原因是这条其实不是视频（微博纯文字、小红书图文笔记）"
            )

        stem = f"{item.platform.value}_{item.video_id}"
        video_path = dest_dir / f"{stem}.mp4"
        headers = await self._headers(item.platform)

        await self._fetch(url, video_path, headers, item.uid)

        if mode is DownloadMode.AUDIO_ONLY:
            audio_path = dest_dir / f"{stem}.wav"
            await self._to_wav(video_path, audio_path)
            # 转完就删掉视频：AUDIO_ONLY 模式下留着只占空间。
            video_path.unlink(missing_ok=True)
            final, has_video = audio_path, False
        else:
            final, has_video = video_path, True

        return DownloadResult(
            item=item,
            path=final,
            mode=mode,
            size_bytes=final.stat().st_size,
            elapsed_sec=time.perf_counter() - started,
            backend=self.name,
            has_video_track=has_video,
        )

    async def _hydrate(self, item: VideoItem) -> str:
        """列表数据里没有直链时，再拉一次详情。

        小红书的搜索结果只给 note_card 摘要，**不含 video 字段**——
        哪怕这条笔记的 type 明确是 video 也一样，视频地址只在详情接口里。
        所以这一步不是可选的优化，是小红书能否下载的前提。

        代价是每条多一次请求，因此只在直链确实缺失时才走。
        """
        if self._session is None or item.platform is not Platform.XHS:
            return ""

        from vspider.discovery.xhs import extract_video_url

        try:
            client = await self._session.client(Platform.XHS)  # type: ignore[attr-defined]
            # xsec_token 是详情接口的必填参数，采集阶段特意保留了下来。
            detail = await client.get_note_by_id(
                note_id=item.video_id,
                xsec_source="pc_search",
                xsec_token=item.raw.get("xsec_token", ""),
            )
        except Exception as exc:  # noqa: BLE001
            raise DownloadError(
                f"取小红书笔记 {item.video_id} 详情失败：{type(exc).__name__}: {exc}"
            ) from exc

        note = (detail or {}).get("note_card") or detail or {}
        url = extract_video_url(note)
        if url:
            # 回填进 raw，后面若再取一次不必重复请求。
            item.raw.update(note)
        return url

    async def _headers(self, platform: Platform) -> dict[str, str]:
        headers = {
            "Referer": _REFERERS[platform],
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
            ),
        }
        if self._session is None:
            return headers

        # 复用浏览器会话里的真实 UA 与 cookie。抖音的视频地址会校验会话，
        # 拿一份临时造的请求头下载会被拒。
        try:
            client = await self._session.client(platform)  # type: ignore[attr-defined]
            for key in ("User-Agent", "user-agent"):
                if client.headers.get(key):
                    headers["User-Agent"] = client.headers[key]
                    break
            if client.headers.get("Cookie"):
                headers["Cookie"] = client.headers["Cookie"]
        except Exception:  # noqa: BLE001
            pass
        return headers

    async def _fetch(
        self, url: str, path: Path, headers: dict[str, str], uid: str
    ) -> None:
        # 细粒度超时：连接 10s、每次读 20s。整体 timeout 之外单独卡住读取时，
        # read 超时能让卡死的连接快速失败去重试，而不是干等到整体超时。
        # 之前一条被对端掐断的连接靠 120s 整体超时 × 3 次重试吃掉了好几分钟。
        timeout = httpx.Timeout(self._timeout, connect=10.0, read=20.0)
        last: Exception | None = None
        for attempt in range(self._retries):
            temp = path.with_suffix(path.suffix + ".part")
            try:
                async with httpx.AsyncClient(
                    timeout=timeout, follow_redirects=True
                ) as client:
                    async with client.stream("GET", url, headers=headers) as response:
                        response.raise_for_status()
                        with temp.open("wb") as sink:
                            async for chunk in response.aiter_bytes(_CHUNK):
                                sink.write(chunk)
                if temp.stat().st_size == 0:
                    raise DownloadError("下载完成但文件是空的")
                temp.replace(path)
                return
            except Exception as exc:  # noqa: BLE001
                last = exc
                temp.unlink(missing_ok=True)
                if attempt < self._retries - 1:
                    await asyncio.sleep(1.5 * (attempt + 1))

        raise DownloadError(f"下载 {uid} 失败：{type(last).__name__}: {last}")

    async def _to_wav(self, source: Path, target: Path) -> None:
        """转成 16k 单声道 wav，正好是 SenseVoice 期望的输入。"""
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise DownloadError("找不到 ffmpeg，无法转音频")

        process = await asyncio.create_subprocess_exec(
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-vn",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(target),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0 or not target.exists():
            raise DownloadError(
                f"ffmpeg 转音频失败：{stderr.decode('utf-8', 'replace')[-400:]}"
            )
