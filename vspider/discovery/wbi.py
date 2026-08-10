"""B站 WBI 签名与匿名身份。"""

from __future__ import annotations

import hashlib
import hmac
import random
import time
import urllib.parse
from hashlib import md5
from typing import Any

import httpx

_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

_STRIP_CHARS = str.maketrans("", "", "!'()*")

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


# 仅供风控对照实验，生产请求不使用。
DM_FINGERPRINT: dict[str, str] = {
    "dm_img_list": "[]",
    "dm_img_str": "V2ViR0wgMS4wIChPcGVuR0wgRVMgMi4wIENocm9taXVtKQ",
    "dm_cover_img_str": (
        "QU5HTEUgKEludGVsLCBJbnRlbChSKSBVSEQgR3JhcGhpY3MgRGlyZWN0M0QxMSB2c181"
        "XzAgcHNfNV8wLCBEM0QxMS0zMS4wLjEwMS4yMTExKUdvb2dsZSBJbmMuIChJbnRlbCk"
    ),
    "dm_img_inter": '{"ds":[],"wh":[0,0,0],"of":[0,0,0]}',
}


async def bootstrap_cookies(client: httpx.AsyncClient) -> None:
    """初始化匿名访客 Cookie。"""
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
        pass

    if not client.cookies.get("b_nut"):
        try:
            await client.get("https://www.bilibili.com/", follow_redirects=True)
        except httpx.HTTPError:
            return
        if not client.cookies.get("b_nut"):
            client.cookies.set(
                "b_nut", str(int(time.time())), domain=".bilibili.com", path="/"
            )


def _filename(url: str) -> str:
    return url.rsplit("/", 1)[-1].split(".")[0] if url else ""


_TICKET_HMAC_KEY = "XgwSnGZ1p"
_TICKET_KEY_ID = "ec02"


async def ensure_bili_ticket(client: httpx.AsyncClient) -> str:
    """申领并保存 bili_ticket。"""
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
