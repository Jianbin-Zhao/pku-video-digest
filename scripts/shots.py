"""生成 README 使用的 Web 截图。"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from playwright.async_api import async_playwright

BASE = os.environ.get("VSPIDER_WEB_URL", "http://127.0.0.1:6006").rstrip("/")
OUT = Path(__file__).resolve().parent.parent / "docs" / "images"
OUT.mkdir(parents=True, exist_ok=True)


async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            viewport={"width": 1440, "height": 900}, device_scale_factor=1
        )
        await page.goto(BASE, wait_until="networkidle")
        await page.click('#segMode button[data-v="search"]')
        await page.fill("#keyword", "人工智能")
        await page.select_option("#profile", "gpu")
        await page.screenshot(path=str(OUT / "01_dashboard.png"))

        await page.click("#btnHistory")
        await page.wait_for_selector(".hrow", timeout=8000)
        await page.screenshot(path=str(OUT / "02_history.png"), full_page=True)

        rows = await page.evaluate("fetch('/api/history').then(r => r.json())")
        run = next(
            row
            for row in rows
            if row.get("platform") == "wb" and int(row.get("succeeded") or 0) >= 5
        )
        await page.locator(".hrow", has_text=run["run_id"]).click()
        await page.wait_for_selector(".digest", timeout=8000)
        await page.screenshot(path=str(OUT / "03_history_detail.png"), full_page=True)

        await page.goto(
            f"{BASE}/api/history/{run['run_id']}/report.html",
            wait_until="networkidle",
        )
        await page.screenshot(path=str(OUT / "04_report.png"), full_page=True)

        await browser.close()
        print(f"截图已写入 {OUT}")


asyncio.run(main())
