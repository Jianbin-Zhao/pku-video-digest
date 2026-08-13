from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import patch

from tools.remote import _sync_file, run


class _Handle:
    def __init__(self, files: dict[str, bytes], path: str, mode: str) -> None:
        self._files = files
        self._path = path
        self._mode = mode
        self._buffer = io.BytesIO(files.get(path, b""))

    def __enter__(self) -> "_Handle":
        return self

    def __exit__(self, *_: object) -> None:
        if "w" in self._mode:
            self._files[self._path] = self._buffer.getvalue()

    def read(self) -> bytes:
        return self._buffer.read()

    def write(self, data: bytes) -> int:
        return self._buffer.write(data)


class _FakeSftp:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.dirs = {"/"}

    def stat(self, path: str) -> SimpleNamespace:
        if path in self.files:
            return SimpleNamespace(st_size=len(self.files[path]))
        if path in self.dirs:
            return SimpleNamespace(st_size=0)
        raise FileNotFoundError(path)

    def mkdir(self, path: str) -> None:
        self.dirs.add(path)

    def put(self, local: str, remote: str) -> None:
        self.files[remote] = Path(local).read_bytes()

    def open(self, path: str, mode: str) -> _Handle:
        return _Handle(self.files, path, mode)

    def posix_rename(self, source: str, destination: str) -> None:
        self.files[destination] = self.files.pop(source)


class _FakeChannel:
    def __init__(self, chunks: list[bytes], status: int) -> None:
        self._chunks = chunks
        self._status = status

    def recv_ready(self) -> bool:
        return bool(self._chunks)

    def recv(self, _size: int) -> bytes:
        return self._chunks.pop(0)

    def exit_status_ready(self) -> bool:
        return not self._chunks

    def recv_exit_status(self) -> int:
        return self._status


class _FakeClient:
    def __init__(self, channel: _FakeChannel) -> None:
        self._channel = channel

    def exec_command(self, *_args: object, **_kwargs: object) -> tuple[None, object, None]:
        return None, SimpleNamespace(channel=self._channel), None

    def close(self) -> None:
        pass


class RemoteSyncTests(unittest.TestCase):
    def test_sync_file_is_incremental_and_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            local = Path(raw_dir) / "items.json"
            local.write_bytes(b"first")
            remote = PurePosixPath("/root/handoff/items.json")
            sftp = _FakeSftp()

            self.assertTrue(_sync_file(sftp, str(local), remote))
            self.assertFalse(_sync_file(sftp, str(local), remote))
            local.write_bytes(b"second")
            self.assertTrue(_sync_file(sftp, str(local), remote))
            self.assertEqual(sftp.files[str(remote)], b"second")
            self.assertFalse(any(".part." in path for path in sftp.files))

    def test_run_replaces_invalid_utf8_and_preserves_exit_status(self) -> None:
        client = _FakeClient(_FakeChannel([b"ok " + bytes([0xFF]) + b"\n", b"done\n"], status=7))
        output = io.StringIO()
        with patch("tools.remote._connect", return_value=client):
            with redirect_stdout(output):
                code = run("synthetic")

        self.assertEqual(code, 7)
        self.assertEqual(output.getvalue(), "ok \ufffd\ndone\n")


if __name__ == "__main__":
    unittest.main()
