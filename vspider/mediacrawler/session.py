"""共享 Playwright 会话和 MediaCrawler 客户端。"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vspider.mediacrawler.bootstrap import ensure_importable, load_config_defaults
from vspider.models import Platform

DEFAULT_BROWSER_DATA = Path(__file__).resolve().parents[2] / ".browser"


@dataclass(frozen=True)
class PlatformSpec:
    platform: Platform
    index_url: str
    client_module: str
    client_class: str
    cookie_urls: tuple[str, ...]
    mobile_ua: bool = False
    extra_headers: dict[str, str] = field(default_factory=dict)
    cookie_env: str = ""
    login_cookie_keys: tuple[str, ...] = ()
    login_required_for: tuple[str, ...] = ()
    landing_url: str = ""
    init_scripts: tuple[tuple[str, str], ...] = ()


SPECS: dict[Platform, PlatformSpec] = {
    Platform.DOUYIN: PlatformSpec(
        platform=Platform.DOUYIN,
        index_url="https://www.douyin.com",
        client_module="media_platform.douyin.client",
        client_class="DouYinClient",
        cookie_urls=(
            "https://douyin.com",
            "https://www.douyin.com",
            "https://creator.douyin.com",
            "https://douhot.douyin.com",
            "https://live.douyin.com",
        ),
        extra_headers={
            "Host": "www.douyin.com",
            "Origin": "https://www.douyin.com/",
            "Referer": "https://www.douyin.com/",
            "Content-Type": "application/json;charset=UTF-8",
        },
        cookie_env="DY_COOKIE",
        login_cookie_keys=("sessionid", "sessionid_ss", "sid_tt"),
        login_required_for=("搜索", "榜单（依赖搜索）"),
    ),
    Platform.KUAISHOU: PlatformSpec(
        platform=Platform.KUAISHOU,
        index_url="https://www.kuaishou.com",
        client_module="media_platform.kuaishou.client",
        client_class="KuaiShouClient",
        cookie_urls=("https://www.kuaishou.com",),
        extra_headers={
            "Origin": "https://www.kuaishou.com",
            "Referer": "https://www.kuaishou.com",
            "Content-Type": "application/json;charset=UTF-8",
        },
        cookie_env="KS_COOKIE",
        login_cookie_keys=("passToken", "userId", "kuaishou.server.web_st"),
        landing_url="https://www.kuaishou.com/?isHome=1",
        # 捕获页面里的 __NS_hxfalcon 签名入口。
        init_scripts=(("media_platform.kuaishou.help", "KS_SIGN_CAPTURE_SCRIPT"),),
    ),
    Platform.WEIBO: PlatformSpec(
        platform=Platform.WEIBO,
        index_url="https://m.weibo.cn",
        client_module="media_platform.weibo.client",
        client_class="WeiboClient",
        cookie_urls=("https://m.weibo.cn", "https://weibo.com"),
        mobile_ua=True,
        extra_headers={
            "Origin": "https://m.weibo.cn",
            "Referer": "https://m.weibo.cn",
            "Content-Type": "application/json;charset=UTF-8",
        },
        cookie_env="WB_COOKIE",
        login_cookie_keys=("SUB", "SUBP"),
    ),
    Platform.XHS: PlatformSpec(
        platform=Platform.XHS,
        index_url="https://www.xiaohongshu.com",
        client_module="media_platform.xhs.client",
        client_class="XiaoHongShuClient",
        cookie_urls=("https://www.xiaohongshu.com",),
        extra_headers={
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9",
            "content-type": "application/json;charset=UTF-8",
            "origin": "https://www.xiaohongshu.com",
            "referer": "https://www.xiaohongshu.com/",
        },
        cookie_env="XHS_COOKIE",
        login_cookie_keys=("web_session",),
        login_required_for=("搜索", "笔记详情"),
    ),
}


class MediaCrawlerSession:
    """持有浏览器与各平台客户端。用作异步上下文管理器。

        async with MediaCrawlerSession() as session:
            client = await session.client(Platform.DOUYIN)
    """

    def __init__(
        self,
        headless: bool = True,
        user_data_dir: str = "",
        nav_timeout_ms: int = 45_000,
    ) -> None:
        ensure_importable()
        load_config_defaults()
        self._headless = headless
        self._user_data_dir = (
            user_data_dir
            or os.environ.get("VSPIDER_BROWSER_DATA")
            or str(DEFAULT_BROWSER_DATA)
        )
        self._nav_timeout_ms = nav_timeout_ms

        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._pages: dict[Platform, Any] = {}
        self._clients: dict[Platform, Any] = {}

    async def __aenter__(self) -> MediaCrawlerSession:
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        chromium = self._playwright.chromium

        if self._user_data_dir:
            self._context = await chromium.launch_persistent_context(
                user_data_dir=self._user_data_dir,
                headless=self._headless,
                viewport={"width": 1920, "height": 1080},
                accept_downloads=True,
            )
        else:
            self._browser = await chromium.launch(headless=self._headless)
            self._context = await self._browser.new_context(
                viewport={"width": 1920, "height": 1080}
            )

        await self._install_stealth()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        for closer in (self._context, self._browser):
            if closer is not None:
                try:
                    await closer.close()
                except Exception:  # noqa: BLE001, S110
                    pass
        if self._playwright is not None:
            await self._playwright.stop()

    async def _install_stealth(self) -> None:
        """注入 MediaCrawler 自带的反检测脚本。

        无头 Chromium 会暴露 navigator.webdriver 等特征，抖音和小红书都会据此拦截。
        这个脚本是仓库里现成的，直接用。
        """
        stealth = ensure_importable() / "libs" / "stealth.min.js"
        if stealth.exists():
            await self._context.add_init_script(path=str(stealth))

    async def page(self, platform: Platform) -> Any:
        """取该平台的页面，首次调用时创建并打开首页。"""
        if platform in self._pages:
            return self._pages[platform]

        spec = _require_spec(platform)
        page = await self._context.new_page()
        page.set_default_navigation_timeout(self._nav_timeout_ms)

        for module_path, attribute in spec.init_scripts:
            module = __import__(module_path, fromlist=[attribute])
            await page.add_init_script(getattr(module, attribute))

        await self._inject_cookie(spec)
        target = spec.landing_url or spec.index_url
        try:
            await page.goto(target, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            # 页面未完全渲染不影响签名。
            from tools import utils  # noqa: PLC0415

            utils.logger.warning(f"[vspider] 打开 {target} 超时：{exc}")

        await self._settle(page)
        self._pages[platform] = page
        return page

    async def _settle(self, page: Any) -> None:
        """等待页面跳转结束。"""
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:  # noqa: BLE001, S110
            pass

    async def evaluate(self, page: Any, script: str, attempts: int = 3) -> Any:
        """执行页面 JS，跳转失败时重试。"""
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                return await page.evaluate(script)
            except Exception as exc:  # noqa: BLE001
                last = exc
                if "context was destroyed" not in str(exc).lower():
                    raise
                await asyncio.sleep(1.5 * (attempt + 1))
                await self._settle(page)
        raise RuntimeError(f"页面 JS 执行反复失败：{last}")

    async def _inject_cookie(self, spec: PlatformSpec) -> None:
        """注入 .env 中配置的 Cookie。"""
        if not spec.cookie_env:
            return
        raw = os.environ.get(spec.cookie_env, "").strip()
        if not raw:
            return

        domain = "." + ".".join(spec.index_url.split("//")[-1].split(".")[-2:])
        cookies = []
        for pair in raw.split(";"):
            name, _, value = pair.strip().partition("=")
            if name and value:
                cookies.append(
                    {"name": name, "value": value, "domain": domain, "path": "/"}
                )
        if cookies:
            await self._context.add_cookies(cookies)

    async def client(self, platform: Platform) -> Any:
        """取该平台的 MediaCrawler 客户端，已带好 cookie、UA 与签名所需的页面。"""
        if platform in self._clients:
            return self._clients[platform]

        spec = _require_spec(platform)
        page = await self.page(platform)

        from tools import utils  # noqa: PLC0415

        cookie_str, cookie_dict = await utils.convert_browser_context_cookies(
            self._context, urls=list(spec.cookie_urls)
        )
        user_agent = (
            utils.get_mobile_user_agent()
            if spec.mobile_ua
            else await self.evaluate(page, "() => navigator.userAgent")
        )

        headers = {"User-Agent": user_agent, "Cookie": cookie_str}
        headers.update(spec.extra_headers)
        if "user-agent" in spec.extra_headers or spec.platform is Platform.XHS:
            headers["user-agent"] = user_agent
            headers["Cookie"] = cookie_str

        module = __import__(spec.client_module, fromlist=[spec.client_class])
        client_cls = getattr(module, spec.client_class)
        client = client_cls(
            proxy=None,
            headers=headers,
            playwright_page=page,
            cookie_dict=cookie_dict,
        )
        self._clients[platform] = client
        return client

    async def cookies(self, platform: Platform) -> dict[str, str]:
        from tools import utils  # noqa: PLC0415

        spec = _require_spec(platform)
        _, cookie_dict = await utils.convert_browser_context_cookies(
            self._context, urls=list(spec.cookie_urls)
        )
        return cookie_dict or {}

    async def login_hint(self, platform: Platform) -> tuple[bool | None, str]:
        """返回登录提示；最终状态以真实数据请求为准。"""
        client = await self.client(platform)
        spec = _require_spec(platform)
        jar = await self.cookies(platform)
        has_cookie = any(jar.get(key) for key in spec.login_cookie_keys)

        pong = getattr(client, "pong", None)
        if pong is not None:
            try:
                ok = (
                    await pong(browser_context=self._context)
                    if platform is Platform.DOUYIN
                    else await pong()
                )
            except Exception as exc:  # noqa: BLE001
                return (None, f"pong 异常（{type(exc).__name__}），无法确认")
            if ok:
                return (True, "pong 确认")
            if has_cookie:
                return (None, "pong 否认但身份 cookie 存在，无法确认")
            return (False, "pong 否认且无身份 cookie")

        return (has_cookie, "仅凭 cookie 判断")

    async def is_logged_in(self, platform: Platform) -> bool:
        """登录轮询中将“不确定”视为已登录。"""
        state, _ = await self.login_hint(platform)
        return state is not False

    async def refresh_cookies(self, platform: Platform) -> None:
        """同步浏览器 Cookie 到客户端。"""
        client = self._clients.get(platform)
        if client is None:
            return
        spec = _require_spec(platform)
        await client.update_cookies(
            browser_context=self._context, urls=list(spec.cookie_urls)
        )


def _require_spec(platform: Platform) -> PlatformSpec:
    spec = SPECS.get(platform)
    if spec is None:
        raise NotImplementedError(
            f"{platform.value} 不走 MediaCrawler 适配层"
            f"（B 站是自己直连官方接口的）"
        )
    return spec
