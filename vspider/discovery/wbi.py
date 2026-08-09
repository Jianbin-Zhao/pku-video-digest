"""B 站 WBI 签名。

B 站从 2023 年起给一批 Web 接口加了 WBI 风控签名，创作者投稿列表
（`/x/space/wbi/arc/search`）就是其中之一，不带签名会直接返回 -403。

签名算法本身是公开且固定的：

  1. 从 `/x/web-interface/nav` 拿到两张图片 URL，文件名即 img_key 与 sub_key
  2. 两个 key 拼起来，按一张固定的 64 位重排表打乱，取前 32 位得到 mixin_key
  3. 请求参数加上时间戳 wts，按键名排序后 urlencode
  4. w_rid = md5(排序后的 query + mixin_key)

自己实现而不是引 MediaCrawler，是因为这段逻辑只有几十行且无状态，
引一个需要启动真实浏览器的依赖来换它并不划算——那个依赖留给
小红书、抖音这些真正需要浏览器环境的平台。
"""

from __future__ import annotations

import hashlib
import hmac
import random
import time
import urllib.parse
from hashlib import md5
from typing import Any

import httpx

# 官方前端 JS 里硬编码的重排表，取值固定。
_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

# 这几个字符必须从参数值里剔除，否则服务端算出的签名与本地不一致。
_STRIP_CHARS = str.maketrans("", "", "!'()*")

# WBI key 每天更新一次，缓存半小时足够，也避免每次请求都多打一次 nav。
_KEY_TTL_SEC = 1800


class WbiSigner:
    """持有 WBI 密钥并对参数签名。密钥按 TTL 懒加载。"""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._mixin_key = ""
        self._fetched_at = 0.0

    async def _ensure_key(self) -> str:
        now = time.time()
        if self._mixin_key and now - self._fetched_at < _KEY_TTL_SEC:
            return self._mixin_key

        response = await self._client.get("/x/web-interface/nav")
        response.raise_for_status()
        # nav 在未登录时返回 code=-101，但 wbi_img 照样给，所以不能按 code 判失败。
        data = response.json().get("data") or {}
        wbi_img = data.get("wbi_img") or {}
        img_key = _filename(wbi_img.get("img_url", ""))
        sub_key = _filename(wbi_img.get("sub_url", ""))
        if not img_key or not sub_key:
            raise RuntimeError("未能从 /x/web-interface/nav 取到 WBI 密钥")

        raw = img_key + sub_key
        self._mixin_key = "".join(raw[index] for index in _MIXIN_KEY_ENC_TAB)[:32]
        self._fetched_at = now
        return self._mixin_key

    async def sign(self, params: dict[str, Any]) -> dict[str, Any]:
        """返回补上 wts 与 w_rid 的新参数字典。"""
        mixin_key = await self._ensure_key()
        signed = dict(params)
        signed["wts"] = int(time.time())

        query = urllib.parse.urlencode(
            {
                key: str(signed[key]).translate(_STRIP_CHARS)
                for key in sorted(signed)
            }
        )
        signed["w_rid"] = md5((query + mixin_key).encode()).hexdigest()
        return signed


# 真实浏览器会带上这组 WebGL 环境指纹参数，网上大量教程也称它是过风控的必要条件。
#
# 实测结论相反（见 docs/EXPERIMENTS.md E6）：带上它们不仅没有提高成功率，
# 反而更容易触发 HTTP 412。因此**生产路径不使用**，这里保留仅供
# scripts/probe_bili_riskctl.py 做对照实验，删掉就无法复现那个结论了。
DM_FINGERPRINT: dict[str, str] = {
    "dm_img_list": "[]",
    # base64 of "WebGL 1.0 (OpenGL ES 2.0 Chromium)"
    "dm_img_str": "V2ViR0wgMS4wIChPcGVuR0wgRVMgMi4wIENocm9taXVtKQ",
    # base64 of a common ANGLE renderer string
    "dm_cover_img_str": (
        "QU5HTEUgKEludGVsLCBJbnRlbChSKSBVSEQgR3JhcGhpY3MgRGlyZWN0M0QxMSB2c181"
        "XzAgcHNfNV8wLCBEM0QxMS0zMS4wLjEwMS4yMTExKUdvb2dsZSBJbmMuIChJbnRlbCk"
    ),
    "dm_img_inter": '{"ds":[],"wh":[0,0,0],"of":[0,0,0]}',
}


async def bootstrap_cookies(client: httpx.AsyncClient) -> None:
    """给客户端种上匿名访客指纹 cookie。

    未登录访问 space 系列接口时，光有正确签名还不够：缺少 buvid3 / b_nut
    会被判定为异常客户端并返回 -352。

    两步都要做：finger/spi 发放 buvid3 与 buvid4，
    但 b_nut（首次访问时间戳）只有访问站点主页才会下发，两者缺一不可。
    """
    if client.cookies.get("buvid3") and client.cookies.get("b_nut"):
        return

    try:
        response = await client.get("/x/frontend/finger/spi")
        data = response.json().get("data") or {}
        for name, key in (("buvid3", "b_3"), ("buvid4", "b_4")):
            value = data.get(key)
            if value:
                client.cookies.set(name, value, domain=".bilibili.com", path="/")
    except (httpx.HTTPError, ValueError):
        # 拿不到指纹不该让整个请求直接失败，继续走后面的主页兜底。
        pass

    if not client.cookies.get("b_nut"):
        try:
            await client.get("https://www.bilibili.com/", follow_redirects=True)
        except httpx.HTTPError:
            return
        # 主页在极少数情况下也不下发 b_nut，本地补一个合法值即可，
        # 服务端只看它是不是一个像样的秒级时间戳。
        if not client.cookies.get("b_nut"):
            client.cookies.set(
                "b_nut", str(int(time.time())), domain=".bilibili.com", path="/"
            )


def _filename(url: str) -> str:
    return url.rsplit("/", 1)[-1].split(".")[0] if url else ""


# bili_ticket 的签名密钥与 key_id 都硬编码在官方前端里。
_TICKET_HMAC_KEY = "XgwSnGZ1p"
_TICKET_KEY_ID = "ec02"


async def ensure_bili_ticket(client: httpx.AsyncClient) -> str:
    """申领并种上 bili_ticket。

    这是 2024 年新增的一道校验。只有 WBI 签名和 buvid 时，
    space 系列接口会直接返回 HTTP 412（而不是带 code 的 JSON），
    补上这个 cookie 才放行。

    签名方式是用固定密钥对时间戳做 HMAC-SHA256，服务端换票返回，
    有效期三天，所以拿到后缓存在 client 的 cookie jar 里复用。
    """
    if client.cookies.get("bili_ticket"):
        return client.cookies.get("bili_ticket") or ""

    ts = int(time.time())
    hexsign = hmac.new(
        _TICKET_HMAC_KEY.encode(), f"ts{ts}".encode(), hashlib.sha256
    ).hexdigest()

    response = await client.post(
        "/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket",
        params={
            "key_id": _TICKET_KEY_ID,
            "hexsign": hexsign,
            "context[ts]": str(ts),
            "csrf": "",
        },
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        raise RuntimeError(
            f"申领 bili_ticket 失败 code={payload.get('code')} "
            f"message={payload.get('message')!r}"
        )

    ticket = (payload.get("data") or {}).get("ticket") or ""
    if ticket:
        client.cookies.set("bili_ticket", ticket, domain=".bilibili.com", path="/")
    return ticket


def gen_uuid() -> str:
    """生成 _uuid cookie。

    官方前端的格式是五段随机十六进制加上毫秒时间戳后三位，末尾固定 "infoc"。
    服务端只校验格式，不校验来源，所以本地生成即可。
    """
    pool = "0123456789ABCDEF"
    groups = [8, 4, 4, 4, 12]
    parts = [
        "".join(random.choice(pool) for _ in range(size)) for size in groups
    ]
    tail = f"{int(time.time() * 1000) % 100000:05d}"
    return "-".join(parts) + tail + "infoc"
