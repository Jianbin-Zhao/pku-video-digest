"""扫码登录 B 站并保存 Cookie。"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"


def _save_cookie(cookie_header: str) -> None:
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    replacement = f"BILI_COOKIE={cookie_header}"
    for index, line in enumerate(lines):
        if line.strip().startswith("BILI_COOKIE="):
            lines[index] = replacement
            break
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(replacement)
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def main(timeout: int) -> int:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto("https://www.bilibili.com", wait_until="domcontentloaded")
        print("B站窗口已打开，请点击登录并扫码。")
        print(f"最多等待 {timeout} 秒；检测到 SESSDATA 后自动保存并关窗。")

        for waited in range(0, timeout, 2):
            cookies = await context.cookies("https://www.bilibili.com")
            if any(cookie["name"] == "SESSDATA" for cookie in cookies):
                cookie_header = "; ".join(
                    f"{cookie['name']}={cookie['value']}" for cookie in cookies
                )
                _save_cookie(cookie_header)
                await asyncio.sleep(2)
                await browser.close()
                print(f"登录成功，已保存 {len(cookies)} 项 Cookie 到 .env。")
                return 0
            await asyncio.sleep(2)
            if waited and waited % 30 == 0:
                print(f"仍在等待扫码……（{waited}/{timeout} 秒）")

        await browser.close()
        print("等待超时，未检测到 SESSDATA。")
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.timeout)))
