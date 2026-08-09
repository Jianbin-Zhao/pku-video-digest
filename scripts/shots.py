"""用 Playwright 给本地 Web 界面出几张截图，验证界面已就绪。"""
from __future__ import annotations

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:6006/"
OUT = Path(r"D:\pku_exam_plus\data\reports\shots")
OUT.mkdir(parents=True, exist_ok=True)


async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900},
                                      device_scale_factor=2)
        await page.goto(BASE, wait_until="networkidle")
        await page.wait_for_timeout(600)
        await page.screenshot(path=str(OUT / "01_dashboard.png"))
        print("01_dashboard 完成")

        # 切到「搜索」场景，露出关键词输入（plus 场景三）
        await page.click('#segMode button[data-v="search"]')
        await page.fill("#keyword", "人工智能")
        await page.select_option("#profile", "gpu")
        await page.wait_for_timeout(300)
        await page.screenshot(path=str(OUT / "02_search_mode.png"))
        print("02_search_mode 完成")

        # 历史列表
        await page.click("#btnHistory")
        await page.wait_for_selector(".hrow", timeout=8000)
        await page.wait_for_timeout(400)
        await page.screenshot(path=str(OUT / "03_history.png"), full_page=True)
        print("03_history 完成")

        # 历史详情（第一条含总览：search_bili_ai）
        await page.click(".hrow")
        await page.wait_for_selector(".digest", timeout=8000)
        await page.wait_for_timeout(700)
        await page.screenshot(path=str(OUT / "04_history_detail.png"), full_page=True)
        print("04_history_detail 完成")

        # 导出的自包含 HTML 报告
        await page.goto(BASE + "api/history/97cf3860aebe/report.html",
                        wait_until="networkidle")
        await page.wait_for_timeout(500)
        await page.screenshot(path=str(OUT / "05_report.png"), full_page=True)
        print("05_report 完成")

        await browser.close()


asyncio.run(main())
