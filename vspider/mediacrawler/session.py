"""共享浏览器会话，按平台产出已签名的 MediaCrawler 客户端。

为什么必须带浏览器：抖音每次请求都要从页面的 localStorage 取 msToken、
再在页面里跑 JS 算 a_bogus；小红书的 x-s/x-t 同理。这些签名逻辑是站点 JS 的一部分，
没有活的页面就算不出来。MediaCrawler 的客户端因此都要求传入一个 playwright page。

为什么共享一个浏览器：每个平台需要自己的页面（cookie 域、localStorage 都不通用），
但整个进程只需要一个浏览器实例。启动 Chromium 要好几秒，
四个平台各起一个既慢又吃内存。

页面按平台懒加载：只跑 B 站时完全不会启动浏览器
（B 站的接口在本项目里是自己直连的，不走这一层）。
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vspider.mediacrawler.bootstrap import ensure_importable, load_config_defaults
from vspider.models import Platform

# 登录态默认落在项目根的 .browser 下（已在 .gitignore 中排除）。
DEFAULT_BROWSER_DATA = Path(__file__).resolve().parents[2] / ".browser"


@dataclass(frozen=True)
class PlatformSpec:
    platform: Platform
    index_url: str
    client_module: str
    client_class: str
    # 取 cookie 时要覆盖的域。抖音的登录态散落在多个子域上，少取一个就会被判未登录。
    cookie_urls: tuple[str, ...]
    # 微博走的是移动端 m.weibo.cn 接口，必须配移动端 UA，否则返回的是桌面版 HTML。
    mobile_ua: bool = False
    extra_headers: dict[str, str] = field(default_factory=dict)
    # 存放该平台登录 Cookie 的环境变量名。
    cookie_env: str = ""
    # 判定已登录的 cookie 键：出现任意一个即视为登录成功。
    login_cookie_keys: tuple[str, ...] = ()
    # 需要登录才能用的能力，用于在开跑前给出明确提示而不是跑到一半失败。
    login_required_for: tuple[str, ...] = ()
    # 首次打开的地址，留空则用 index_url。有的平台首页会跳到别处，
    # 需要显式指定带参数的入口。
    landing_url: str = ""
    # 需要在页面加载前注入的脚本，写成 (模块路径, 变量名)。
    # 必须是 init script：这类脚本要在站点自身的 JS 之前执行才能挂上钩子。
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
        # 实测：热榜匿名可用，但搜索会返回
        # status_code=2483「请先登录，再继续搜索吧」。
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
        # 快手的列表接口全部要 __NS_hxfalcon 签名，而签名只能靠页面自带的
        # JS 环境算。这段脚本负责在页面初始化时把那个环境的入口捕获下来，
        # 不注入的话所有列表接口一律返回 result:50。
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
        # 默认就用持久化目录：抖音和小红书的搜索都要求登录，
        # 每次跑都重新扫码不现实。扫一次存下来，后续无人值守也能跑。
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

        # 注入必须早于 goto：init script 只对之后加载的页面生效。
        for module_path, attribute in spec.init_scripts:
            module = __import__(module_path, fromlist=[attribute])
            await page.add_init_script(getattr(module, attribute))

        await self._inject_cookie(spec)
        target = spec.landing_url or spec.index_url
        try:
            await page.goto(target, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            # 首页加载超时不必致命：签名只需要页面的 JS 上下文，
            # 页面元素有没有渲染完不影响。
            from tools import utils  # noqa: PLC0415

            utils.logger.warning(f"[vspider] 打开 {target} 超时：{exc}")

        await self._settle(page)
        self._pages[platform] = page
        return page

    async def _settle(self, page: Any) -> None:
        """等页面不再跳转。

        抖音和小红书都是前端路由，domcontentloaded 之后还会再跳几次
        （抖音跳推荐页、微博跳访客认证）。这期间执行 JS 会直接抛
        "Execution context was destroyed"。而签名恰恰全程依赖在页面里执行 JS，
        所以必须先把页面等稳。
        """
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:  # noqa: BLE001, S110
            # networkidle 在长轮询的站点上永远等不到，超时是正常情况。
            pass

    async def evaluate(self, page: Any, script: str, attempts: int = 3) -> Any:
        """在页面里执行 JS，跳转导致失败时重试。

        即便开页时已经等稳，站点仍可能在任意时刻自行跳转，
        所以调用点也要能容错。
        """
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
        """把 .env 里配置的登录 Cookie 注入浏览器。

        匿名访问在几个平台上限制差异很大：微博和快手大体能用，
        抖音会频繁触发验证，小红书的搜索基本必须登录。
        所以支持直接贴浏览器里的 Cookie，省掉扫码。
        """
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
        # 小红书那套 header 是小写键名，大小写混用会让它自己的签名逻辑取不到值。
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
        """粗略判断登录态。返回 (True 已登录 / False 未登录 / None 不确定, 依据)。

        这个方法只是**提示**，不能当结论用。两种间接信号都踩过坑：

        - 只看 cookie 会误判成已登录：小红书的 web_session 匿名访客也有。
        - 只看 pong 会误判成未登录：它内部是「拿到肯定答复才返回 True」，
          IP 被风控、接口改版、网络抖动都会让它返回 False，
          而此时账号其实是登录着的。

        所以 pong 返回 False 时只报「不确定」，真正的结论交给
        verify_data_access——直接看能不能取到数据。
        """
        client = await self.client(platform)
        spec = _require_spec(platform)
        jar = await self.cookies(platform)
        has_cookie = any(jar.get(key) for key in spec.login_cookie_keys)

        pong = getattr(client, "pong", None)
        if pong is not None:
            try:
                # 只有抖音的 pong 需要浏览器上下文，其余平台不接参数。
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
        """login_hint 的布尔化版本，「不确定」按已登录处理。

        用在登录流程的轮询里：宁可提前结束等待让用户去跑真实验证，
        也不要因为判不准就一直转圈，白等几分钟。
        """
        state, _ = await self.login_hint(platform)
        return state is not False

    async def refresh_cookies(self, platform: Platform) -> None:
        """把浏览器当前的 cookie 同步回客户端。

        扫码登录完成、或平台下发了新的风控 cookie 之后需要调用，
        否则客户端还在用登录前那份。
        """
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
