"""plus 版 Web 全链路探针。

从命令行收一个 JSON 请求体，POST /api/run，流式读 SSE 到结束，
然后核对三件 plus 新增能力：
  1. digest 是否随 result 返回；
  2. 历史详情里 digest 是否落库；
  3. report.html / report.md 两个导出端点是否可用。

用法（key=value，避免 JSON 引号穿透多层 shell 被转义）：
    python scripts/probe_plus_run.py mode=search platform=bili keyword=人工智能 limit=2 fast=1
"""
from __future__ import annotations

import json
import sys

import httpx

BASE = "http://127.0.0.1:6006"


def _coerce(value: str) -> object:
    if value in ("true", "1"):
        return True
    if value in ("false", "0"):
        return False
    return int(value) if value.isdigit() else value


body: dict = {}
for arg in sys.argv[1:]:
    key, _, value = arg.partition("=")
    body[key] = _coerce(value)
print("请求体:", body)
r = httpx.post(f"{BASE}/api/run", json=body, timeout=30)
r.raise_for_status()
run_id = r.json()["run_id"]
print("run_id:", run_id)

kinds: dict[str, int] = {}
final: dict = {}
with httpx.stream("GET", f"{BASE}/api/runs/{run_id}/events", timeout=1200) as resp:
    for line in resp.iter_lines():
        if not line or line.startswith(":") or line.startswith("event:"):
            continue
        if line.startswith("data: "):
            payload = json.loads(line[6:])
            kind = payload.get("kind")
            if kind:
                kinds[kind] = kinds.get(kind, 0) + 1
                if kind in ("video_done", "video_failed", "run_done"):
                    print(f"  {kind}: {payload.get('message', '')[:70]}")
                if kind == "stage_done" and payload.get("stage") == "digest":
                    print(f"  [digest] {payload.get('message', '')[:70]}")
                if kind == "stage_skipped":
                    print(f"  [skip] {payload.get('message', '')[:70]}")
            elif "status" in payload:
                final = payload

print("事件统计:", kinds)
status = final.get("status")
result = final.get("result") or {}
print("最终状态:", status)
print("digest 在 result 中:", bool(result.get("digest")))
if result.get("digest"):
    d = result["digest"]
    print("  headline:", d.get("headline", ""))
    print("  themes:", [t.get("name") for t in d.get("themes", [])])

detail = httpx.get(f"{BASE}/api/history/{run_id}", timeout=30).json()
print("digest 已落库:", bool(detail.get("digest")))

for fmt in ("html", "md"):
    rep = httpx.get(f"{BASE}/api/history/{run_id}/report.{fmt}", timeout=30)
    print(f"report.{fmt}: HTTP {rep.status_code}, {len(rep.content)} bytes")

print("PROBE_OK" if status == "done" else "PROBE_FAILED")
