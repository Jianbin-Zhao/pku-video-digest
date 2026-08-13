"""服务器侧：对本机传来的已下载视频做内容理解。

混合部署的后半段。读取 fetch_local.py 导出的目录（若干 mp4 + items.json），
重建 VideoItem，跑 ASR / OCR / 归纳，输出归纳结果。

这条路专给浏览器平台（抖音/快手/微博/小红书）用——它们的采集在本机，
理解在服务器。B 站不用绕这一圈，直接在服务器 vspider rank 即可。

用法：
    python scripts/understand.py data/handoff/ks --profile api
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vspider.models import Platform, VideoItem  # noqa: E402
from vspider.pipeline.console import ConsoleReporter, print_report  # noqa: E402
from vspider.pipeline.orchestrator import (  # noqa: E402
    Orchestrator,
    PipelineConfig,
    RunResult,
)
from vspider.registry import Paths, build_asr, build_ocr, build_summarizer  # noqa: E402
from vspider.settings import (  # noqa: E402
    configure_stdio,
    load_env,
    local_now_naive,
)


class _NoDiscovery:
    """理解阶段用不到采集/下载，塞占位对象满足编排器构造签名。"""

    platform = Platform.BILIBILI

    async def fetch_ranking(self, *a, **k):  # noqa: ANN002, ANN003, ANN201, D102
        return []

    async def fetch_creator_videos(self, *a, **k):  # noqa: ANN002, ANN003, ANN201, D102
        return []


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("handoff_dir", help="fetch_local.py 导出的目录")
    parser.add_argument("--profile", default="gpu")
    parser.add_argument("--model", default="")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--out", default="")
    # plus：整批完成后生成跨视频总览（digest）。
    parser.add_argument("--digest", action="store_true", help="生成批次总览")
    # plus：转写充分时跳过抽帧与 OCR，加速。
    parser.add_argument("--fast", action="store_true", help="快速模式：转写充分时跳过 OCR")
    # plus：把本次结果入库并导出报告（html/md），验证端到端交付链路。
    parser.add_argument("--persist", action="store_true", help="结果入库到 SQLite")
    parser.add_argument("--report", default="", help="导出报告路径（按后缀选 html/md）")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    configure_stdio()
    import logging

    logging.basicConfig(level=logging.WARNING, format="[!] %(message)s")
    load_env()

    handoff = Path(args.handoff_dir)
    manifest = handoff / "items.json"
    if not manifest.exists():
        print(f"找不到清单 {manifest}，确认目录传对了没")
        return 1

    records = json.loads(manifest.read_text(encoding="utf-8"))
    prefetched: list[tuple[VideoItem, Path]] = []
    for record in records:
        local_file = record.pop("_local_file", "")
        path = handoff / local_file
        if not local_file or not path.exists():
            print(f"  跳过 {record.get('video_id')}：找不到文件 {local_file}")
            continue
        prefetched.append((VideoItem.model_validate(record), path))

    if not prefetched:
        if records:
            print("没有可理解的文件：清单里有记录，但本地视频文件缺失。")
            return 1
        print("清单为空：没有当天可处理的视频，按正常空结果退出。")
        return 0

    print(f"载入 {len(prefetched)} 个文件，开始内容理解\n")

    paths = Paths.from_env()
    reporter = ConsoleReporter(verbose=args.verbose)
    orchestrator = Orchestrator(
        provider=_NoDiscovery(),
        downloader=None,  # type: ignore[arg-type]
        asr=build_asr(paths, device=args.device),
        ocr=build_ocr(),
        summarizer=build_summarizer(profile=args.profile, model=args.model),
        config=PipelineConfig(
            data_root=paths.data_root,
            enable_digest=args.digest,
            skip_ocr_if_transcript_chars=200 if args.fast else 0,
        ),
        sink=reporter,
    )

    async def run() -> RunResult:
        try:
            return await orchestrator.run_prefetched(
                prefetched, scenario=f"prefetched:{handoff.name}"
            )
        finally:
            await orchestrator.aclose()

    result = await run()
    print_report(result)
    _print_digest(result)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "scenario": result.scenario,
            "elapsed_sec": round(result.elapsed_sec, 2),
            "success_rate": round(result.success_rate, 3),
            "digest": result.digest.model_dump(mode="json") if result.digest else None,
            "videos": [
                {
                    "item": r.item.model_dump(mode="json", exclude={"raw"}),
                    "summary": (
                        r.summary.model_dump(mode="json") if r.summary else None
                    ),
                    "error": r.error,
                }
                for r in result.results
            ],
        }
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n结果写入 {out}")

    # plus：入库 + 报告导出，验证「历史留存 → 可分享报告」这条交付链路。
    if args.persist or args.report:
        import uuid
        from vspider.storage import Storage

        storage = Storage()
        meta = {
            "run_id": uuid.uuid4().hex[:12],
            "mode": "understand",
            "platform": handoff.name,
            "profile": args.profile,
            "started_at": local_now_naive().isoformat(timespec="seconds"),
        }
        storage.save_run(result, meta)
        print(f"\n已入库 run_id={meta['run_id']}")
        if args.report:
            from vspider.report import render_html, render_markdown

            row = storage.get_run(meta["run_id"])
            report_path = Path(args.report)
            text = (
                render_markdown(row)
                if report_path.suffix == ".md"
                else render_html(row)
            )
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(text, encoding="utf-8")
            print(f"报告已导出：{report_path}")

    return 0 if result.succeeded else 1


def _print_digest(result: RunResult) -> None:
    d = result.digest
    if d is None:
        return
    print("\n—— 批次总览 ——")
    if d.headline:
        print(d.headline)
    for theme in d.themes:
        uids = f"（{'、'.join(theme.video_uids)}）" if theme.video_uids else ""
        print(f"  · {theme.name}：{theme.description}{uids}")
    for obs in d.observations:
        print(f"  - {obs}")
    if d.top_pick_uid:
        print(f"  ▶ 优先观看 {d.top_pick_uid}：{d.top_pick_reason}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
