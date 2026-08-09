"""配置加载。

自己解析 .env 而不引入 python-dotenv，是为了让这个模块在本地 venv 和
服务器 conda base 两套环境里都不依赖额外安装。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def configure_stdio() -> None:
    """把标准输出切成 UTF-8。

    Windows 控制台默认是 GBK，而各平台的视频标题里普遍带
    emoji、不间断空格（\\xa0）这类 GBK 编不出来的字符，
    直接 print 会抛 UnicodeEncodeError——数据明明抓对了，
    却死在打印上。errors="replace" 保证再离谱的字符也只是显示成问号。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def load_env(path: Path | None = None, override: bool = False) -> dict[str, str]:
    """把 .env 里的键值读进 os.environ 并返回读到的内容。"""
    env_path = path or PROJECT_ROOT / ".env"
    loaded: dict[str, str] = {}
    if not env_path.exists():
        return loaded

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        loaded[key] = value
        if override or key not in os.environ:
            os.environ[key] = value
    return loaded


def require(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value:
        raise SystemExit(
            f"缺少环境变量 {key}。请在 {PROJECT_ROOT / '.env'} 中配置"
            f"（可参考 .env.example）。"
        )
    return value
