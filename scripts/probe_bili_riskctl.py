"""测量 B 站创作者接口的风控行为。

第一版把每个组合各试一次，结果在两轮之间完全不可复现——同一个「裸请求」
第一轮 -352、第二轮却成功。这说明主导变量根本不是参数组合，
而是按 IP 的请求频率：短时间内连发就会吃 HTTP 412。

所以改成测成功率：每个配置连发 N 次、间隔固定，统计通过比例，
配置之间留足冷却时间，避免上一个配置的额度透支污染下一个。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vspider.discovery.wbi import (  # noqa: E402
    DM_FINGERPRINT,
    WbiSigner,
    bootstrap_cookies,
    ensure_bili_ticket,
)

API_BASE = "https://api.bilibili.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# (名称, 种 buvid, 领 bili_ticket, 带 dm 指纹参数)
CONFIGS: list[tuple[str, bool, bool, bool]] = [
    ("仅 WBI 签名", False, False, False),
    ("+ buvid", True, False, False),
    ("+ buvid + bili_ticket", True, True, False),
    ("+ buvid + ticket + dm 参数", True, True, True),
]


def _make_client(mid: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=API_BASE,
        headers={
            **HEADERS,
            "Referer": f"https://space.bilibili.com/{mid}/video",
            "Origin": "https://space.bilibili.com",
        },
        timeout=15.0,
        trust_env=False,
        follow_redirects=True,
    )


async def _one_request(
    client: httpx.AsyncClient, signer: WbiSigner, mid: str, use_dm: bool
) -> str:
    params: dict[str, object] = {
        "mid": mid,
        "ps": 5,
        "pn": 1,
        "order": "pubdate",
        "platform": "web",
        "web_location": "1550101",
    }
    if use_dm:
        params.update(DM_FINGERPRINT)

    try:
        response = await client.get(
            "/x/space/wbi/arc/search", params=await signer.sign(params)
        )
    except httpx.HTTPError as exc:
        return f"网络错误 {type(exc).__name__}"

    if response.status_code == 412:
        return "HTTP 412"
    if response.status_code != 200:
        return f"HTTP {response.status_code}"
    payload = response.json()
    if payload.get("code") == -352:
        return "-352"
    if payload.get("code") != 0:
        return f"code={payload.get('code')}"
    return "成功"


async def run_config(
    mid: str,
    name: str,
    use_buvid: bool,
    use_ticket: bool,
    use_dm: bool,
    repeats: int,
    gap: float,
) -> Counter[str]:
    client = _make_client(mid)
    outcomes: Counter[str] = Counter()
    try:
        if use_buvid:
            await bootstrap_cookies(client)
        if use_ticket:
            try:
                await ensure_bili_ticket(client)
            except Exception as exc:  # noqa: BLE001
                outcomes[f"领票失败 {type(exc).__name__}"] += repeats
                return outcomes

        signer = WbiSigner(client)
        for index in range(repeats):
            outcomes[await _one_request(client, signer, mid, use_dm)] += 1
            if index < repeats - 1:
                await asyncio.sleep(gap)
        return outcomes
    finally:
        await client.aclose()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mid", default="490537571")
    parser.add_argument("--repeats", type=int, default=6, help="每个配置请求几次")
    parser.add_argument("--gap", type=float, default=3.0, help="同配置内请求间隔（秒）")
    parser.add_argument("--cooldown", type=float, default=25.0, help="配置之间冷却（秒）")
    args = parser.parse_args()

    print(
        f"目标 mid={args.mid}，每配置 {args.repeats} 次请求，"
        f"间隔 {args.gap}s，配置间冷却 {args.cooldown}s\n"
    )
    width = max(len(name) for name, *_ in CONFIGS)

    for index, (name, buvid, ticket, dm) in enumerate(CONFIGS):
        outcomes = await run_config(
            args.mid, name, buvid, ticket, dm, args.repeats, args.gap
        )
        ok = outcomes["成功"]
        detail = ", ".join(
            f"{key} ×{count}" for key, count in outcomes.most_common() if key != "成功"
        )
        print(
            f"  {name:<{width}}  成功 {ok}/{args.repeats}"
            + (f"   [{detail}]" if detail else "")
        )
        if index < len(CONFIGS) - 1:
            await asyncio.sleep(args.cooldown)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
