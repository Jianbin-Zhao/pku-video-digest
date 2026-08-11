from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vspider.download.base import DownloadMode
from vspider.download.direct import DirectUrlDownloader
from vspider.download.ytdlp_backend import YtDlpDownloader
from vspider.models import Platform, VideoItem


class _FakeProcess:
    def __init__(self, returncode: int, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr


class DownloaderTests(unittest.TestCase):
    def test_direct_downloader_uses_existing_video(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            out_dir = Path(raw_dir)
            cached = out_dir / "dy_cached.mp4"
            cached.write_bytes(b"cached video")
            item = VideoItem(
                platform=Platform.DOUYIN,
                video_id="cached",
                url="https://example.invalid/video",
            )

            result = asyncio.run(
                DirectUrlDownloader().download(item, out_dir, DownloadMode.VIDEO)
            )

            self.assertEqual(result.path, cached)
            self.assertEqual(result.size_bytes, len(b"cached video"))

    def test_ytdlp_retries_process_failure_and_then_caches(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            out_dir = Path(raw_dir)
            calls: list[tuple[object, ...]] = []

            async def fake_exec(*args: object, **kwargs: object) -> _FakeProcess:
                calls.append(args)
                if len(calls) == 1:
                    return _FakeProcess(1, stderr=b"temporary timeout")
                output = Path(args[args.index("-o") + 1])  # type: ignore[arg-type]
                output = Path(str(output).replace(".%(ext)s", ".mp4"))
                output.write_bytes(b"downloaded video")
                return _FakeProcess(0)

            async def no_sleep(_: float) -> None:
                return None

            item = VideoItem(
                platform=Platform.BILIBILI,
                video_id="BV-test",
                url="https://example.invalid/video",
            )
            with (
                patch.object(YtDlpDownloader, "_resolve_binary", return_value=["fake"]),
                patch(
                    "vspider.download.ytdlp_backend.asyncio.create_subprocess_exec",
                    side_effect=fake_exec,
                ),
                patch("vspider.download.ytdlp_backend.asyncio.sleep", new=no_sleep),
            ):
                downloader = YtDlpDownloader(retries=2)
                result = asyncio.run(downloader.download(item, out_dir))
                cached = asyncio.run(downloader.download(item, out_dir))

            self.assertEqual(len(calls), 2)
            self.assertEqual(result.path, cached.path)
            self.assertEqual(result.path.read_bytes(), b"downloaded video")


if __name__ == "__main__":
    unittest.main()
