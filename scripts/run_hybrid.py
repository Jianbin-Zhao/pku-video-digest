"""Run the local-collection -> remote-GPU-understanding workflow.

This is an additive convenience command. The existing fetch_local.py,
remote.py put, and understand.py commands remain available independently.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

PLATFORMS = ("bili", "dy", "ks", "wb", "xhs")


def _run(command: list[str], *, cwd: Path) -> None:
    print("+", " ".join(command))
    subprocess.run(command, cwd=cwd, check=True)


def _remote_command(args: argparse.Namespace, remote_dir: str) -> str:
    remote_project = shlex.quote(args.remote_project)
    remote_python = shlex.quote(args.remote_python)
    remote_handoff = shlex.quote(remote_dir)
    report_remote = remote_dir.rstrip("/") + "/report.html"

    command = (
        f"cd {remote_project} && {remote_python} scripts/understand.py "
        f"{remote_handoff} --profile {shlex.quote(args.profile)} "
        f"--device {shlex.quote(args.device)} --persist "
        f"--report {shlex.quote(report_remote)}"
    )
    if args.model:
        command += f" --model {shlex.quote(args.model)}"
    if args.digest:
        command += " --digest"
    if args.fast:
        command += " --fast"
    return command


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect locally, sync incrementally, and understand on the GPU server."
    )
    parser.add_argument("platform", choices=PLATFORMS)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--creator", default="")
    source.add_argument("--keyword", default="")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--today", action="store_true")
    parser.add_argument("--show-browser", action="store_true")
    parser.add_argument("--download-attempts", type=int, default=2)
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--remote-dir", default="")
    parser.add_argument("--profile", default="gpu")
    parser.add_argument("--model", default="")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--digest", action="store_true", default=True)
    parser.add_argument("--no-digest", action="store_false", dest="digest")
    parser.add_argument(
        "--report",
        default="",
        help="local report path; defaults to <out-dir>/report.html",
    )
    parser.add_argument(
        "--remote-project",
        default=os.environ.get("VSPIDER_REMOTE_PROJECT", "/root/vspider"),
    )
    parser.add_argument(
        "--remote-python",
        default=os.environ.get("VSPIDER_REMOTE_PYTHON", "/root/miniconda3/bin/python"),
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    out_dir = (
        Path(args.out_dir)
        if args.out_dir
        else project_root / "data" / "handoff" / f"hybrid_{args.platform}"
    ).resolve()
    remote_dir = args.remote_dir or f"/root/autodl-tmp/data/handoff/hybrid_{args.platform}"
    report_path = (
        Path(args.report) if args.report else out_dir / "report.html"
    ).resolve()

    fetch = [
        sys.executable,
        str(project_root / "scripts" / "fetch_local.py"),
        args.platform,
        "--limit",
        str(args.limit),
        "--download-attempts",
        str(args.download_attempts),
        "--out-dir",
        str(out_dir),
    ]
    if args.creator:
        fetch.extend(("--creator", args.creator))
    if args.keyword:
        fetch.extend(("--keyword", args.keyword))
    if args.today:
        fetch.append("--today")
    if args.show_browser:
        fetch.append("--show-browser")
    _run(fetch, cwd=project_root)

    manifest = out_dir / "items.json"
    if manifest.exists() and manifest.read_text(encoding="utf-8").strip() == "[]":
        print("no videos were collected; the empty creator-today result is valid")
        return 0

    remote_tool = str(project_root / "tools" / "remote.py")
    _run([sys.executable, remote_tool, "sync", str(out_dir), remote_dir], cwd=project_root)

    remote_result = subprocess.run(
        [sys.executable, remote_tool, "run", _remote_command(args, remote_dir)],
        cwd=project_root,
        check=False,
    )
    print("remote understand exit code:", remote_result.returncode)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_result = subprocess.run(
        [
            sys.executable,
            remote_tool,
            "get",
            remote_dir.rstrip("/") + "/report.html",
            str(report_path),
        ],
        cwd=project_root,
        check=False,
    )
    if report_result.returncode != 0 and remote_result.returncode == 0:
        return report_result.returncode
    return remote_result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
