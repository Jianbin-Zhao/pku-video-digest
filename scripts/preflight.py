"""运行任务前检查验收机器，避免跑到中途才发现缺依赖或服务。

示例：
    python scripts/preflight.py --side collect
    python scripts/preflight.py --side understand --profile gpu --device cuda:0
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from vspider.settings import configure_stdio, load_env  # noqa: E402


class Checks:
    def __init__(self) -> None:
        self.failed = 0

    def check(self, ok: bool, label: str, hint: str = "") -> None:
        print(f"[{'OK' if ok else 'FAIL'}] {label}")
        if not ok:
            self.failed += 1
            if hint:
                print(f"  处理：{hint}")


def _module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _models_endpoint(base_url: str) -> bool:
    try:
        request = Request(base_url.rstrip("/") + "/models")
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return response.status == 200 and isinstance(payload.get("data"), list)
    except Exception:
        return False


def main(side: str, profile: str, device: str) -> int:
    configure_stdio()
    load_env()
    checks = Checks()
    checks.check(sys.version_info >= (3, 11), "Python >= 3.11")
    checks.check(shutil.which("ffmpeg") is not None, "ffmpeg 在 PATH")
    checks.check(shutil.which("ffprobe") is not None, "ffprobe 在 PATH")

    if side == "collect":
        root = Path(os.environ.get("MEDIACRAWLER_ROOT", PROJECT_ROOT.parent / "MediaCrawler"))
        checks.check(
            (root / "media_platform").is_dir(),
            f"MediaCrawler: {root}",
            "运行 python scripts/setup_local.py",
        )
        for module in ("playwright", "execjs", "parsel", "httpx"):
            checks.check(_module(module), f"Python 模块 {module}")
        checks.check(
            (PROJECT_ROOT / ".browser").exists(),
            "浏览器登录目录",
            "先运行 scripts/login.py；B站另运行 scripts/login_bili.py",
        )
    else:
        models_root = Path(
            os.environ.get("VSPIDER_MODELS_ROOT", "/root/autodl-tmp/models")
        )
        checks.check(
            (models_root / "SenseVoiceSmall").is_dir(), "SenseVoiceSmall 模型"
        )
        checks.check((models_root / "fsmn-vad").is_dir(), "FSMN-VAD 模型")
        for module in ("torch", "funasr", "rapidocr_onnxruntime", "httpx"):
            checks.check(_module(module), f"Python 模块 {module}")

        if device.startswith("cuda") and _module("torch"):
            import torch

            checks.check(torch.cuda.is_available(), "CUDA 可用")

        if profile == "api":
            checks.check(bool(os.environ.get("DASHSCOPE_API_KEY")), "DashScope API Key")
        else:
            env_key = (
                "VSPIDER_VLLM_BASE_URL"
                if profile == "gpu"
                else "VSPIDER_LLAMA_BASE_URL"
            )
            fallback = (
                "http://127.0.0.1:8000/v1"
                if profile == "gpu"
                else "http://127.0.0.1:8080/v1"
            )
            base_url = os.environ.get(env_key, fallback)
            checks.check(
                _models_endpoint(base_url),
                f"{profile} 归纳服务 {base_url}",
                "先启动对应的 vLLM 或 llama.cpp 服务",
            )

    if checks.failed:
        print(f"\n预检失败：{checks.failed} 项")
        return 1
    print("\n预检通过，可以开始验收。")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--side", choices=("collect", "understand"), required=True)
    parser.add_argument("--profile", choices=("api", "gpu", "cpu"), default="api")
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()
    raise SystemExit(main(args.side, args.profile, args.device))
