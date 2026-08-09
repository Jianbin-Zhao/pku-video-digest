"""平台可用性验证：直接看能不能取到数据。

写这个模块是因为间接判据坑过两次。先用 cookie 判登录态，
小红书的 web_session 匿名访客也有，误判成已登录；改用官方 pong，
它在 IP 被风控时对着已登录的账号返回 False，又误判成未登录。

结论是：**唯一可靠的判据就是真的去取一次数据。**
能取到就是能用，取不到就是不能用，中间没有需要推断的东西。
所以这里不做任何猜测，只跑一遍真实调用并如实报告结果。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from vspider.models import Platform, VideoItem


@dataclass
class PlatformStatus:
    platform: Platform
    ok: bool
    count: int
    elapsed_sec: float
    rank_source: str = ""
    sample_title: str = ""
    error: str = ""
    login_hint: str = ""

    def line(self) -> str:
        mark = "可用" if self.ok else "不可用"
        head = (
            f"{self.platform.value:<4} {mark}  {self.count} 条  "
            f"{self.elapsed_sec:.1f}s"
        )
        if self.ok:
            title = " ".join(self.sample_title.split())[:34]
            return f"{head}  [{self.rank_source}] {title}"
        return f"{head}  {self.error[:90]}"


async def verify_data_access(
    platform: Platform, session: object | None, limit: int = 2
) -> PlatformStatus:
    """跑一次真实的榜单采集，报告能否取到数据。"""
    from vspider.registry import build_provider

    started = time.perf_counter()
    hint = ""
    if session is not None and hasattr(session, "login_hint"):
        try:
            state, reason = await session.login_hint(platform)  # type: ignore[attr-defined]
            label = {True: "已登录", False: "未登录", None: "登录态未知"}[state]
            hint = f"{label}（{reason}）"
        except Exception:  # noqa: BLE001
            hint = "登录态检查失败"

    try:
        provider = build_provider(platform, session)
        items: list[VideoItem] = await provider.fetch_ranking(limit=limit)
    except Exception as exc:  # noqa: BLE001
        return PlatformStatus(
            platform=platform,
            ok=False,
            count=0,
            elapsed_sec=time.perf_counter() - started,
            error=f"{type(exc).__name__}: {exc}",
            login_hint=hint,
        )

    elapsed = time.perf_counter() - started
    if not items:
        return PlatformStatus(
            platform=platform,
            ok=False,
            count=0,
            elapsed_sec=elapsed,
            error="调用没报错但返回 0 条（多为被限流或登录态失效）",
            login_hint=hint,
        )

    first = items[0]
    return PlatformStatus(
        platform=platform,
        ok=True,
        count=len(items),
        elapsed_sec=elapsed,
        rank_source=first.rank_source.value if first.rank_source else "-",
        sample_title=first.title or first.desc,
        login_hint=hint,
    )
