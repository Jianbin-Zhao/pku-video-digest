"""在租用的 GPU 服务器上执行命令 / 同步文件。

连接参数从环境变量读取，不落盘，避免把凭据写进仓库：

    VSPIDER_SSH_HOST  VSPIDER_SSH_PORT  VSPIDER_SSH_USER  VSPIDER_SSH_PASSWORD

用法：
    python tools/remote.py run "nvidia-smi"
    python tools/remote.py put D:\\local\\dir /root/remote/dir
    python tools/remote.py sync D:\\local\\handoff /root/remote/handoff
    python tools/remote.py get /root/remote/file D:\\local\\file
"""

from __future__ import annotations

import hashlib
import os
import stat
import sys
import time
from pathlib import Path, PurePosixPath

import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vspider.settings import load_env  # noqa: E402

_SKIP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    ".cache",
    ".browser",
    "data",
    ".pytest_cache",
    ".mypy_cache",
    "dist",
    "build",
}


def _connect() -> paramiko.SSHClient:
    load_env()
    host = os.environ["VSPIDER_SSH_HOST"]
    port = int(os.environ.get("VSPIDER_SSH_PORT", "22"))
    user = os.environ.get("VSPIDER_SSH_USER", "root")
    password = os.environ["VSPIDER_SSH_PASSWORD"]

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        port=port,
        username=user,
        password=password,
        timeout=30,
        banner_timeout=30,
        auth_timeout=30,
    )
    return client


def run(command: str, timeout: float | None = None) -> int:
    # Windows 控制台默认 GBK，远端输出是 UTF-8，直接写会 UnicodeEncodeError。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    client = _connect()
    try:
        # get_pty 让远端把 stdout/stderr 合流并保留进度条等即时输出，
        # 长时间的 pip / 模型下载才能看到过程而不是最后一次性吐出来。
        _, stdout, _ = client.exec_command(command, get_pty=True, timeout=timeout)
        channel = stdout.channel
        while True:
            if channel.recv_ready():
                raw = channel.recv(4096)
                if not raw:
                    break
                sys.stdout.write(raw.decode("utf-8", errors="replace"))
                sys.stdout.flush()
                continue
            if channel.exit_status_ready():
                break
            time.sleep(0.05)
        # Drain bytes that arrived between the last readiness check and exit.
        while channel.recv_ready():
            sys.stdout.write(channel.recv(4096).decode("utf-8", errors="replace"))
            sys.stdout.flush()
        return channel.recv_exit_status()
    finally:
        client.close()


def _ensure_remote_dir(sftp: paramiko.SFTPClient, path: PurePosixPath) -> None:
    parts: list[PurePosixPath] = []
    current = path
    while str(current) not in ("/", "."):
        parts.append(current)
        current = current.parent
    for part in reversed(parts):
        try:
            sftp.stat(str(part))
        except FileNotFoundError:
            sftp.mkdir(str(part))


def put(local: str, remote: str) -> int:
    client = _connect()
    try:
        sftp = client.open_sftp()
        local_path = os.path.abspath(local)
        remote_path = PurePosixPath(remote)

        if os.path.isfile(local_path):
            _ensure_remote_dir(sftp, remote_path.parent)
            sftp.put(local_path, str(remote_path))
            print(f"put {local_path} -> {remote_path}")
            return 0

        count = 0
        for root, dirs, files in os.walk(local_path):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            rel = os.path.relpath(root, local_path)
            target_dir = remote_path if rel == "." else remote_path / rel.replace(os.sep, "/")
            _ensure_remote_dir(sftp, target_dir)
            for name in files:
                if name.endswith((".pyc", ".pyo")):
                    continue
                sftp.put(os.path.join(root, name), str(target_dir / name))
                count += 1
        print(f"put {count} files -> {remote_path}")
        return 0
    finally:
        client.close()


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remote_text(sftp: paramiko.SFTPClient, path: PurePosixPath) -> str:
    try:
        with sftp.open(str(path), "rb") as handle:
            return handle.read().decode("utf-8").strip()
    except OSError:
        return ""


def _atomic_rename(
    sftp: paramiko.SFTPClient, source: PurePosixPath, destination: PurePosixPath
) -> None:
    try:
        sftp.posix_rename(str(source), str(destination))
    except (AttributeError, IOError):
        # Older SFTP servers may not implement POSIX rename. The fallback is
        # still safer than writing directly to the final media filename.
        sftp.rename(str(source), str(destination))


def _sync_file(
    sftp: paramiko.SFTPClient, local_path: str, remote_path: PurePosixPath
) -> bool:
    """Upload a file atomically when its checksum differs."""
    checksum = _sha256(local_path)
    checksum_path = PurePosixPath(f"{remote_path}.sha256")
    try:
        attrs = sftp.stat(str(remote_path))
        if attrs.st_size == os.path.getsize(local_path) and _remote_text(
            sftp, checksum_path
        ) == checksum:
            return False
    except OSError:
        pass

    _ensure_remote_dir(sftp, remote_path.parent)
    temporary = PurePosixPath(f"{remote_path}.part.{os.getpid()}")
    temporary_checksum = PurePosixPath(f"{checksum_path}.part.{os.getpid()}")
    sftp.put(local_path, str(temporary))
    _atomic_rename(sftp, temporary, remote_path)
    with sftp.open(str(temporary_checksum), "wb") as handle:
        handle.write((checksum + "\n").encode("utf-8"))
    _atomic_rename(sftp, temporary_checksum, checksum_path)
    return True


def sync(local: str, remote: str) -> int:
    """Incrementally sync a handoff directory without exposing secrets."""
    client = _connect()
    try:
        sftp = client.open_sftp()
        local_path = os.path.abspath(local)
        remote_path = PurePosixPath(remote)
        if os.path.isfile(local_path):
            if os.path.basename(local_path) in {".env", ".env.server"}:
                raise SystemExit("sync refuses to upload environment secret files")
            changed = _sync_file(sftp, local_path, remote_path)
            print(f"sync {'updated' if changed else 'unchanged'} {remote_path}")
            return 0

        if not os.path.isdir(local_path):
            raise SystemExit(f"local path does not exist: {local_path}")

        updated = 0
        unchanged = 0
        for root, dirs, files in os.walk(local_path):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            rel = os.path.relpath(root, local_path)
            target_dir = (
                remote_path if rel == "." else remote_path / rel.replace(os.sep, "/")
            )
            _ensure_remote_dir(sftp, target_dir)
            for name in files:
                if name.endswith((".pyc", ".pyo")) or name in {".env", ".env.server"}:
                    continue
                destination = target_dir / name
                if _sync_file(sftp, os.path.join(root, name), destination):
                    updated += 1
                else:
                    unchanged += 1
        print(f"sync updated={updated} unchanged={unchanged} -> {remote_path}")
        return 0
    finally:
        client.close()


def get(remote: str, local: str) -> int:
    client = _connect()
    try:
        sftp = client.open_sftp()
        attrs = sftp.stat(remote)
        if stat.S_ISDIR(attrs.st_mode or 0):
            raise SystemExit("get 暂不支持目录，请先在远端打包")
        os.makedirs(os.path.dirname(os.path.abspath(local)) or ".", exist_ok=True)
        sftp.get(remote, local)
        print(f"get {remote} -> {local}")
        return 0
    finally:
        client.close()


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    action = sys.argv[1]
    if action in {"put", "sync", "get"} and len(sys.argv) != 4:
        print(__doc__)
        return 2
    if action == "run":
        return run(" ".join(sys.argv[2:]))
    if action == "put":
        return put(sys.argv[2], sys.argv[3])
    if action == "sync":
        return sync(sys.argv[2], sys.argv[3])
    if action == "get":
        return get(sys.argv[2], sys.argv[3])
    print(f"未知动作 {action!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
