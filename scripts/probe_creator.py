"""诊断 B 站创作者投稿接口的风控情况。

只打采集这一层，不下载不推理，用来快速判断 -352 究竟是签名问题、
指纹参数问题，还是出口 IP 被判定为异常来源。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vspider.discovery.bilibili import BilibiliRankingProvider  # noqa: E402
from vspider.discovery.wbi import bootstrap_cookies  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mid", default="490537571", help="创作者 mid")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--cookie", default=os.environ.get("BILI_COOKIE", ""))
    args = parser.parse_args()

    provider = BilibiliRankingProvider(cookie=args.cookie)
    try:
        print("== 出口 IP ==")
        response = await provider._client.get("https://api.bilibili.com/x/web-interface/zone")
        zone = response.json().get("data") or {}
        print(f"  {zone.get('addr')} {zone.get('country')} {zone.get('province')} {zone.get('isp')}")

        print("\n== 种 cookie ==")
        await bootstrap_cookies(provider._client)
        for name in ("buvid3", "buvid4", "b_nut"):
            value = provider._client.cookies.get(name)
            print(f"  {name} = {(value or '(缺失)')[:40]}")

        print("\n== WBI 密钥 ==")
        mixin = await provider._signer._ensure_key()
        print(f"  mixin_key = {mixin}")

        print(f"\n== 拉取 mid={args.mid} 的投稿 ==")
        items = await provider.fetch_creator_videos(args.mid, limit=args.limit)
        print(f"  拿到 {len(items)} 条")
        for item in items:
            when = item.publish_time.strftime("%Y-%m-%d %H:%M") if item.publish_time else "?"
            print(
                f"  [{when}] {item.duration_sec:>5}s  播放 {item.stats.play:>9,}  "
                f"{item.title[:40]}"
            )
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"\n失败：{type(exc).__name__}: {exc}")
        return 1
    finally:
        await provider.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
