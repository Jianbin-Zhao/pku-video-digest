"""端到端自测 Web:POST 一个 understand 运行,读 SSE 事件流直到结束。

验证三件事:任务能启动、事件能实时推、最终 result 落库可查。
"""
from __future__ import annotations

import json
import sys

import httpx

BASE = "http://127.0.0.1:6006"
handoff = sys.argv[1] if len(sys.argv) > 1 else "/root/autodl-tmp/data/handoff/scene2/wb"

resume = "resume" in sys.argv
body = {
    "mode": "understand", "profile": "cpu", "device": "cpu",
    "handoff_dir": handoff, "resume": resume,
}
print("resume:", resume)
r = httpx.post(f"{BASE}/api/run", json=body, timeout=30)
r.raise_for_status()
run_id = r.json()["run_id"]
print("run_id:", run_id)

kinds: dict[str, int] = {}
with httpx.stream("GET", f"{BASE}/api/runs/{run_id}/events", timeout=400) as resp:
    for line in resp.iter_lines():
        if not line or line.startswith(":"):
            continue
        if line.startswith("event: end"):
            print("[SSE] end")
            continue
        if line.startswith("data: "):
            payload = json.loads(line[6:])
            kind = payload.get("kind")
            if kind:
                kinds[kind] = kinds.get(kind, 0) + 1
                if kind in ("video_done", "run_done", "video_failed"):
                    print(f"  {kind}: {payload.get('message','')[:60]}")
            elif "status" in payload:
                print("[end payload] status =", payload.get("status"),
                      "videos =", len((payload.get("result") or {}).get("videos", [])))

print("事件统计:", kinds)

snap = httpx.get(f"{BASE}/api/runs/{run_id}", timeout=30).json()
print("最终状态:", snap["status"])

hist = httpx.get(f"{BASE}/api/history", timeout=30).json()
print("历史记录条数:", len(hist), "| 最近:", hist[0] if hist else None)
