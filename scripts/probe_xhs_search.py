"""xhs 搜索复测：关键词写死在代码里，绕开 Windows 控制台传参的编码坑。"""
import json

import httpx

BASE = "http://127.0.0.1:6006"

body = {
    "mode": "search",
    "platform": "xhs",
    "keyword": "人工智能",
    "limit": 2,
    "profile": "cpu",
    "device": "cpu",
    "fast": True,
    "digest": True,
}
r = httpx.post(f"{BASE}/api/run", json=body, timeout=30)
r.raise_for_status()
run_id = r.json()["run_id"]
print("run_id:", run_id)

final: dict = {}
with httpx.stream("GET", f"{BASE}/api/runs/{run_id}/events", timeout=1800) as resp:
    for line in resp.iter_lines():
        if not line.startswith("data: "):
            continue
        payload = json.loads(line[6:])
        kind = payload.get("kind")
        if kind:
            msg = (payload.get("message") or "")[:120]
            print(f"  [{kind}] {msg}".encode("utf-8", "replace").decode("utf-8"))
        elif "status" in payload:
            final = payload

print("最终状态:", final.get("status"))
if final.get("status") == "error":
    print("错误:", (final.get("error") or "")[:500])
result = final.get("result") or {}
print("digest:", bool(result.get("digest")))
print("PROBE_OK" if final.get("status") == "done" else "PROBE_FAILED")
