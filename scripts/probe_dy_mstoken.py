"""定位抖音搜索被拒的真正原因。

已排除登录：身份 cookie 齐全、HasUserLogin=1、pong 通过、热榜正常，
只有搜索返回 2483。所以问题出在请求参数，不在身份。

怀疑对象是 msToken。MediaCrawler 从 localStorage 的 xmst 键取它，
但抖音也会把 msToken 下发到 cookie 里。如果 localStorage 没有而 cookie 有，
客户端就会发出 msToken=None，被风控判为非法请求——
表现恰好就是「已登录却说请先登录」。

这个脚本做对照实验：同一个搜索请求，分别用
  A 原样（localStorage 取值，可能是 None）
  B 手动补上 cookie 里的 msToken
看结果是否不同。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vspider.mediacrawler.session import MediaCrawlerSession  # noqa: E402
from vspider.models import Platform  # noqa: E402
from vspider.settings import configure_stdio, load_env  # noqa: E402


async def main() -> int:
    configure_stdio()
    load_env()
    async with MediaCrawlerSession(headless=True) as session:
        client = await session.client(Platform.DOUYIN)
        page = await session.page(Platform.DOUYIN)
        jar = await session.cookies(Platform.DOUYIN)
        storage = await session.evaluate(page, "() => window.localStorage")

        ls_token = (storage or {}).get("xmst")
        ck_token = jar.get("msToken")
        print("=== msToken 来源对比 ===")
        print(f"  localStorage['xmst'] : {'有' if ls_token else '缺失'}"
              f"{f' 长度 {len(ls_token)}' if ls_token else ''}")
        print(f"  cookie['msToken']    : {'有' if ck_token else '缺失'}"
              f"{f' 长度 {len(ck_token)}' if ck_token else ''}")
        print(f"  localStorage 全部键   : {sorted((storage or {}).keys())}")

        print("\n=== A 原样搜索 ===")
        a = await client.search_info_by_keyword(keyword="美食", offset=0)
        print(f"  status_code={a.get('status_code')} {a.get('status_msg', '')}")
        print(f"  返回 {len(a.get('data') or [])} 条")

        if not ck_token:
            print("\ncookie 里也没有 msToken，假设不成立，需另找原因。")
            return 1

        print("\n=== B 把 cookie 的 msToken 写进 localStorage 后重试 ===")
        # 直接补到 localStorage：客户端下次读 xmst 就能拿到真实值。
        await page.evaluate(
            "(token) => window.localStorage.setItem('xmst', token)", ck_token
        )
        confirmed = await session.evaluate(
            page, "() => window.localStorage.getItem('xmst')"
        )
        print(f"  已写入，确认长度 {len(confirmed or '')}")

        b = await client.search_info_by_keyword(keyword="美食", offset=0)
        print(f"  status_code={b.get('status_code')} {b.get('status_msg', '')}")
        entries = b.get("data") or []
        print(f"  返回 {len(entries)} 条")

        if entries:
            print("\n结论：msToken 缺失就是根因，补上即可。")
        else:
            print("\n结论：补了 msToken 仍被拒，根因在别处（可能是搜索需要先访问搜索页）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
