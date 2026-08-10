"""用真实 SQLite 历史自动录制约两分钟 Web 功能演示，不重新执行推理。"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

from playwright.async_api import Page, async_playwright

BASE = "http://127.0.0.1:6007"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "demo_video"


async def _overlay(page: Page, text: str, wait_ms: int) -> None:
    await page.evaluate(
        """text => {
          let el = document.getElementById("demo-caption");
          if (!el) {
            el = document.createElement("div");
            el.id = "demo-caption";
            Object.assign(el.style, {
              position:"fixed", left:"50%", bottom:"24px", transform:"translateX(-50%)",
              zIndex:"99999", maxWidth:"86%", padding:"11px 20px",
              borderRadius:"999px", color:"#fff", background:"rgba(8,12,9,.88)",
              border:"1px solid rgba(217,166,72,.65)", fontSize:"18px",
              fontFamily:"Microsoft YaHei, sans-serif", boxShadow:"0 8px 30px #0008",
              textAlign:"center", pointerEvents:"none"
            });
            document.body.appendChild(el);
          }
          el.textContent = text;
        }""",
        text,
    )
    await page.wait_for_timeout(wait_ms)


async def _click(page: Page, selector: str) -> None:
    locator = page.locator(selector).first
    await locator.scroll_into_view_if_needed()
    await locator.evaluate(
        """el => {
          el.style.outline = "3px solid #d9a648";
          el.style.outlineOffset = "3px";
        }"""
    )
    await page.wait_for_timeout(700)
    await locator.click()


async def _show_history(page: Page, run_id: str, caption: str) -> None:
    await page.goto(BASE, wait_until="networkidle")
    await _click(page, "#btnHistory")
    await page.wait_for_selector(".hrow")
    row = page.locator(".hrow", has_text=run_id).first
    await row.scroll_into_view_if_needed()
    await row.click()
    await page.wait_for_selector(".grid .card")
    await _overlay(page, caption, 4500)
    await page.evaluate("window.scrollTo({top: 460, behavior: 'smooth'})")
    await page.wait_for_timeout(3500)
    await page.evaluate(
        "window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'})"
    )
    await page.wait_for_timeout(3500)


def _pick(
    rows: list[dict], *, mode: str, platform: str, minimum: int = 1
) -> dict:
    return next(
        row
        for row in rows
        if row.get("mode") == mode
        and row.get("platform") == platform
        and int(row.get("succeeded") or 0) >= minimum
    )


async def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw_dir = OUT_DIR / "raw"
    if raw_dir.exists():
        shutil.rmtree(raw_dir)
    raw_dir.mkdir()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            record_video_dir=str(raw_dir),
            record_video_size={"width": 1440, "height": 900},
        )
        page = await context.new_page()
        await page.goto(BASE, wait_until="networkidle")
        rows = await page.evaluate("fetch('/api/history').then(r => r.json())")

        await _overlay(
            page,
            "VSpider：五平台视频发现、下载、GPU理解与结构化归纳",
            5000,
        )

        await _click(page, '#segMode button[data-v="rank"]')
        await page.select_option("#platform", "bili")
        await page.fill("#limit", "5")
        await page.select_option("#profile", "gpu")
        await _overlay(page, "场景一：当前榜单前5；可选择GPU、CPU或API后端", 6500)

        await _click(page, '#segMode button[data-v="creator"]')
        await page.fill("#creatorId", "9596327")
        await _overlay(page, "场景二：指定创作者，并可只筛选今天发布的视频", 6500)

        await _click(page, '#segMode button[data-v="search"]')
        await page.fill("#keyword", "人工智能")
        await _overlay(page, "拓展搜索：输入关键词，五个平台使用同一套理解流水线", 6500)

        await _click(page, '#segMode button[data-v="understand"]')
        await page.fill(
            "#handoffDir", "/root/autodl-tmp/data/acceptance/20260810/rank/xhs"
        )
        await _overlay(page, "混合部署：本机完成登录采集，服务器4090接手内容理解", 6500)

        await _click(page, "#btnHistory")
        await page.wait_for_selector(".hrow")
        await _overlay(page, "以下全部来自刚才验收的SQLite历史，不重新运行模型", 6000)
        await page.evaluate("window.scrollTo({top: 520, behavior: 'smooth'})")
        await page.wait_for_timeout(3500)

        bili_rank = _pick(rows, mode="rank", platform="bili", minimum=5)
        xhs_rank = _pick(rows, mode="understand", platform="xhs", minimum=5)
        wb_creator = _pick(rows, mode="understand", platform="wb", minimum=5)
        dy_search = _pick(rows, mode="understand", platform="dy", minimum=2)

        await _show_history(
            page,
            bili_rank["run_id"],
            "严格B站原榜单前5：摘要、要点、话题、情感、推广与置信度",
        )
        await _show_history(
            page,
            xhs_rank["run_id"],
            "小红书榜单前5：ASR与OCR互补，无人声或弱文本视频仍可归纳",
        )
        await _show_history(
            page,
            wb_creator["run_id"],
            "微博创作者今日5条：整批热点总览、主题聚类与优先观看推荐",
        )
        await _show_history(
            page,
            dy_search["run_id"],
            "抖音关键词搜索：下载后在4090完成ASR、OCR和Qwen结构化归纳",
        )

        await page.goto(
            f"{BASE}/api/history/{wb_creator['run_id']}/report.html",
            wait_until="networkidle",
        )
        await _overlay(page, "任意历史任务可导出自包含HTML或Markdown报告", 6000)
        await page.evaluate(
            "window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'})"
        )
        await page.wait_for_timeout(5000)
        await _overlay(
            page,
            "演示结束：五平台、三种场景、GPU理解、历史、总览、报告与断点续跑",
            6000,
        )

        video = page.video
        await context.close()
        await browser.close()
        webm_path = await video.path()

    output = OUT_DIR / "vspider_demo_2min.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(webm_path),
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output),
        ],
        check=True,
    )
    print(f"演示视频已生成：{output}")


if __name__ == "__main__":
    asyncio.run(main())
