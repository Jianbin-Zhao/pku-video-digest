"""运行结果导出为可分享的报告（Markdown / 自包含 HTML）。

输入统一用 storage.get_run() 返回的字典（runs 行 + videos 列表 + digest），
CLI 和 Web 共用同一套渲染，保证两边导出的报告一致。

HTML 刻意做成零依赖单文件：内联样式、不引外部字体和脚本，
发给任何人打开都不需要网络和环境。
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any

_SENTIMENT_LABELS = {
    "positive": "积极",
    "neutral": "中性",
    "negative": "消极",
    "mixed": "混合",
}

_MODE_LABELS = {
    "rank": "今日榜单",
    "creator": "创作者作品",
    "search": "关键词搜索",
    "understand": "混合部署理解",
}

_PLATFORM_LABELS = {
    "bili": "哔哩哔哩",
    "dy": "抖音",
    "ks": "快手",
    "wb": "微博",
    "xhs": "小红书",
}


def _fmt_count(n: Any) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "0"
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f} 亿"
    if n >= 10_000:
        return f"{n / 10_000:.1f} 万"
    return str(n)


def _run_title(run: dict[str, Any]) -> str:
    mode = _MODE_LABELS.get(run.get("mode", ""), run.get("mode", ""))
    platform = _PLATFORM_LABELS.get(run.get("platform", ""), run.get("platform", ""))
    scenario = run.get("scenario") or ""
    keyword = ""
    if scenario.startswith("search:"):
        keyword = scenario.split(":", 2)[-1]
    title = f"{platform} · {mode}"
    if keyword:
        title += f"「{keyword}」"
    return title


def render_markdown(run: dict[str, Any]) -> str:
    """把一次运行渲染成 Markdown 报告。"""
    videos = run.get("videos") or []
    ok = [v for v in videos if not v.get("error") and v.get("one_liner")]
    digest = run.get("digest") or None

    lines: list[str] = []
    lines.append(f"# {_run_title(run)} 内容归纳报告")
    lines.append("")
    lines.append(
        f"> 生成时间 {run.get('started_at') or datetime.now().isoformat(timespec='seconds')}"
        f" · 归纳后端 `{run.get('profile', '')}`"
        f" · 成功 {len(ok)}/{len(videos)}"
        f" · 总耗时 {run.get('elapsed_sec', 0)}s"
    )
    lines.append("")

    if digest:
        lines.append("## 批次总览")
        lines.append("")
        if digest.get("headline"):
            lines.append(f"**{digest['headline']}**")
            lines.append("")
        for theme in digest.get("themes") or []:
            uids = "、".join(theme.get("video_uids") or [])
            lines.append(
                f"- **{theme.get('name', '')}** — {theme.get('description', '')}"
                + (f"（{uids}）" if uids else "")
            )
        if digest.get("observations"):
            lines.append("")
            lines.append("观察：")
            for obs in digest["observations"]:
                lines.append(f"- {obs}")
        if digest.get("top_pick_uid"):
            lines.append("")
            lines.append(
                f"优先观看：`{digest['top_pick_uid']}` — {digest.get('top_pick_reason', '')}"
            )
        lines.append("")

    lines.append("## 逐条归纳")
    lines.append("")
    for i, v in enumerate(videos, 1):
        title = v.get("title") or v.get("uid", "")
        lines.append(f"### {i}. {title}")
        lines.append("")
        lines.append(
            f"- 平台：{_PLATFORM_LABELS.get(v.get('platform', ''), v.get('platform', ''))}"
            f" · 作者：{v.get('author_name', '')}"
            f" · 时长：{v.get('duration_sec', 0)}s"
        )
        if v.get("url"):
            lines.append(f"- 链接：{v['url']}")
        if v.get("error"):
            lines.append(f"- **处理失败**：{v['error']}")
            lines.append("")
            continue
        lines.append(f"- 一句话：**{v.get('one_liner', '')}**")
        if v.get("key_points"):
            lines.append("- 要点：")
            for p in v["key_points"]:
                lines.append(f"  - {p}")
        if v.get("topics"):
            lines.append(f"- 话题：{'、'.join(v['topics'])}")
        sentiment = _SENTIMENT_LABELS.get(v.get("sentiment", ""), v.get("sentiment", ""))
        promo = "是" if v.get("is_promotion") else "否"
        lines.append(
            f"- 情绪：{sentiment} · 广告推广：{promo}"
            f" · 置信度：{v.get('confidence', 0)}"
        )
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("*由 vspider 自动生成：视频下载 → 语音转写 + 画面文字识别 → LLM 结构化归纳*")
    return "\n".join(lines)


_HTML_STYLE = """
:root { color-scheme: light; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif;
  background: #f6f5f2; color: #1c1b18; line-height: 1.7;
  padding: 48px 20px 80px;
}
.page { max-width: 860px; margin: 0 auto; }
header.report { border-bottom: 2px solid #1c1b18; padding-bottom: 20px; margin-bottom: 28px; }
header.report h1 { font-size: 26px; letter-spacing: -0.01em; }
header.report .meta { color: #6f6a5f; font-size: 13px; margin-top: 8px; }
section.digest {
  background: #14231c; color: #eef3ee; border-radius: 14px;
  padding: 24px 26px; margin-bottom: 32px;
}
section.digest h2 { font-size: 13px; letter-spacing: 0.12em; color: #9db8a6; margin-bottom: 10px; }
section.digest .headline { font-size: 19px; font-weight: 600; margin-bottom: 14px; }
section.digest ul { list-style: none; }
section.digest li { padding: 6px 0; border-top: 1px solid rgba(255,255,255,.08); font-size: 14px; }
section.digest li b { color: #cfe4d6; }
section.digest .pick { margin-top: 14px; font-size: 14px; color: #cfe4d6; }
article.video {
  background: #fff; border: 1px solid #e4e1d8; border-radius: 14px;
  padding: 22px 24px; margin-bottom: 16px;
}
article.video h3 { font-size: 17px; margin-bottom: 6px; }
article.video .line { color: #6f6a5f; font-size: 13px; margin-bottom: 10px; }
article.video .one-liner { font-size: 15px; font-weight: 600; margin-bottom: 10px; }
article.video ul { padding-left: 20px; font-size: 14px; }
article.video .tags { margin-top: 10px; font-size: 12px; color: #6f6a5f; }
article.video .error { color: #a03c2e; font-size: 14px; }
a { color: #2f5e46; }
footer { margin-top: 36px; color: #a09a8c; font-size: 12px; text-align: center; }
@media print { body { background: #fff; padding: 0; } }
"""


def render_html(run: dict[str, Any]) -> str:
    """把一次运行渲染成零依赖单文件 HTML 报告。"""
    videos = run.get("videos") or []
    ok = [v for v in videos if not v.get("error") and v.get("one_liner")]
    digest = run.get("digest") or None
    e = html.escape

    parts: list[str] = []
    parts.append("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>")
    parts.append("<meta name='viewport' content='width=device-width, initial-scale=1'>")
    parts.append(f"<title>{e(_run_title(run))} 报告</title>")
    parts.append(f"<style>{_HTML_STYLE}</style></head><body><div class='page'>")

    parts.append("<header class='report'>")
    parts.append(f"<h1>{e(_run_title(run))} 内容归纳报告</h1>")
    parts.append(
        f"<div class='meta'>{e(str(run.get('started_at') or ''))}"
        f" · 归纳后端 {e(str(run.get('profile') or ''))}"
        f" · 成功 {len(ok)}/{len(videos)}"
        f" · 总耗时 {run.get('elapsed_sec', 0)}s</div>"
    )
    parts.append("</header>")

    if digest:
        parts.append("<section class='digest'><h2>批次总览</h2>")
        if digest.get("headline"):
            parts.append(f"<div class='headline'>{e(digest['headline'])}</div>")
        themes = digest.get("themes") or []
        observations = digest.get("observations") or []
        if themes or observations:
            parts.append("<ul>")
            for t in themes:
                parts.append(
                    f"<li><b>{e(t.get('name', ''))}</b> {e(t.get('description', ''))}</li>"
                )
            for obs in observations:
                parts.append(f"<li>{e(obs)}</li>")
            parts.append("</ul>")
        if digest.get("top_pick_uid"):
            parts.append(
                f"<div class='pick'>优先观看 {e(digest['top_pick_uid'])} — "
                f"{e(digest.get('top_pick_reason', ''))}</div>"
            )
        parts.append("</section>")

    for i, v in enumerate(videos, 1):
        parts.append("<article class='video'>")
        title = v.get("title") or v.get("uid", "")
        if v.get("url"):
            parts.append(
                f"<h3>{i}. <a href='{e(v['url'])}' target='_blank'>{e(title)}</a></h3>"
            )
        else:
            parts.append(f"<h3>{i}. {e(title)}</h3>")
        platform = _PLATFORM_LABELS.get(v.get("platform", ""), v.get("platform", ""))
        parts.append(
            f"<div class='line'>{e(platform)} · {e(v.get('author_name') or '')}"
            f" · {v.get('duration_sec', 0)}s"
            f" · 转写 {v.get('transcript_chars', 0)} 字"
            f" · 画面文字 {v.get('ocr_chars', 0)} 字</div>"
        )
        if v.get("error"):
            parts.append(f"<div class='error'>处理失败：{e(v['error'])}</div>")
        else:
            parts.append(f"<div class='one-liner'>{e(v.get('one_liner', ''))}</div>")
            points = v.get("key_points") or []
            if points:
                parts.append("<ul>")
                for p in points:
                    parts.append(f"<li>{e(p)}</li>")
                parts.append("</ul>")
            sentiment = _SENTIMENT_LABELS.get(
                v.get("sentiment", ""), v.get("sentiment", "")
            )
            promo = "广告" if v.get("is_promotion") else "非广告"
            topics = "、".join(v.get("topics") or [])
            parts.append(
                f"<div class='tags'>{e(topics)} · {e(sentiment)} · {e(promo)}"
                f" · 置信度 {v.get('confidence', 0)}</div>"
            )
        parts.append("</article>")

    parts.append(
        "<footer>由 vspider 自动生成：视频下载 → 语音转写 + 画面文字识别 → LLM 结构化归纳</footer>"
    )
    parts.append("</div></body></html>")
    return "".join(parts)
