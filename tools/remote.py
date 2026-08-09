"""在租用的 GPU 服务器上执行命令 / 同步文件。

连接参数从环境变量读取，不落盘，避免把凭据写进仓库：

    VSPIDER_SSH_HOST  VSPIDER_SSH_PORT  VSPIDER_SSH_USER  VSPIDER_SSH_PASSWORD

用法：
    python tools/remote.py run "nvidia-smi"
    python tools/remote.py put D:\\local\\dir /root/remote/dir
    python tools/remote.py get /root/remote/file D:\\local\\file
"""

from __future__ import annotations

import os
import stat
import sys
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
        for line in iter(stdout.readline, ""):
            sys.stdout.write(line)
            sys.stdout.flush()
        return stdout.channel.recv_exit_status()
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
    if action == "run":
        return run(" ".join(sys.argv[2:]))
    if action == "put":
        return put(sys.argv[2], sys.argv[3])
    if action == "get":
        return get(sys.argv[2], sys.argv[3])
    print(f"未知动作 {action!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
