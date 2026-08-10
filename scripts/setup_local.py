"""安装本机采集环境。"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MEDIACRAWLER_ROOT = PROJECT_ROOT.parent / "MediaCrawler"
MEDIACRAWLER_URL = "https://github.com/NanmiCoder/MediaCrawler.git"
MEDIACRAWLER_COMMIT = "071c8c0acaece3e82f2532cffb19faeddc9ec1c3"


def _run(*args: str, cwd: Path | None = None) -> None:
    display = " ".join(args)
    print(f"\n> {display}")
    subprocess.run(args, cwd=cwd, check=True)


def _prepare_mediacrawler(update: bool) -> None:
    if not (MEDIACRAWLER_ROOT / ".git").is_dir():
        if MEDIACRAWLER_ROOT.exists():
            raise SystemExit(
                f"{MEDIACRAWLER_ROOT} 已存在但不是 Git 仓库，请移走后重试"
            )
        _run("git", "clone", "--filter=blob:none", MEDIACRAWLER_URL, str(MEDIACRAWLER_ROOT))
    elif update:
        _run("git", "fetch", "origin", MEDIACRAWLER_COMMIT, cwd=MEDIACRAWLER_ROOT)

    _run("git", "checkout", "--detach", MEDIACRAWLER_COMMIT, cwd=MEDIACRAWLER_ROOT)


def main(update: bool, skip_browser: bool) -> int:
    if sys.version_info < (3, 11):
        raise SystemExit("需要 Python 3.11 或更高版本")
    if shutil.which("git") is None:
        raise SystemExit("找不到 git，请先安装 Git 并加入 PATH")

    _prepare_mediacrawler(update)
    _run(
        sys.executable,
        "-m",
        "pip",
        "install",
        "-e",
        f"{PROJECT_ROOT}[download,serve]",
    )
    _run(sys.executable, "-m", "pip", "install", "-e", str(MEDIACRAWLER_ROOT))
    if not skip_browser:
        _run(sys.executable, "-m", "playwright", "install", "chromium")

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print(
            "\n[需要处理] 找不到 ffmpeg/ffprobe。Windows 请下载静态构建，"
            "把 bin 目录加入 PATH；Linux 执行 apt install ffmpeg。"
        )
        return 2

    print("\n本机采集环境安装完成。下一步先运行登录脚本，再运行验收。")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--update", action="store_true", help="已有 MediaCrawler 时也重新 fetch 固定提交"
    )
    parser.add_argument(
        "--skip-browser", action="store_true", help="跳过 Playwright Chromium 下载"
    )
    args = parser.parse_args()
    raise SystemExit(main(args.update, args.skip_browser))
