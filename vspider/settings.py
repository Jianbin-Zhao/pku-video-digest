"""配置加载。

自己解析 .env 而不引入 python-dotenv，是为了让这个模块在本地 venv 和
服务器 conda base 两套环境里都不依赖额外安装。
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TIMEZONE = "Asia/Shanghai"


def local_timezone() -> ZoneInfo | timezone:
    """Return the configured business timezone, falling back safely."""
    name = os.environ.get("VSPIDER_TIMEZONE", DEFAULT_TIMEZONE).strip()
    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except ZoneInfoNotFoundError:
        # Windows Python may not ship an IANA tz database. Asia/Shanghai has
        # no DST, so a fixed UTC+8 fallback preserves the workflow semantics.
        return timezone(timedelta(hours=8), name=DEFAULT_TIMEZONE)


def local_today() -> date:
    """Return today's date in the timezone used by platform workflows."""
    return datetime.now(local_timezone()).date()


def local_now_naive() -> datetime:
    """Return local wall-clock time without changing existing JSON shape."""
    return datetime.now(local_timezone()).replace(tzinfo=None)


def local_datetime_fromtimestamp(timestamp: float) -> datetime:
    """Convert a Unix timestamp to a naive local datetime for compatibility."""
    return datetime.fromtimestamp(timestamp, local_timezone()).replace(tzinfo=None)


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
