from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

from tools.remote import _sync_file


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


if __name__ == "__main__":
    unittest.main()
