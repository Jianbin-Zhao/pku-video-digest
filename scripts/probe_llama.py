"""冒烟测试 llama.cpp CPU 服务:JSON 模式可用性 + 出字速度。"""
from __future__ import annotations

import json
import time

import httpx

payload = {
    "model": "local",
    "messages": [
        {
            "role": "user",
            "content": (
                "只输出 JSON,格式 {\"one_liner\": \"...\", \"key_points\": [\"...\"]}。"
                "视频内容:台风白海豚在广东登陆,多地停课停工,风力12级。"
            ),
        }
    ],
    "response_format": {"type": "json_object"},
    "max_tokens": 160,
    "temperature": 0.3,
}

t0 = time.time()
r = httpx.post(
    "http://127.0.0.1:8080/v1/chat/completions", json=payload, timeout=300
)
dt = time.time() - t0
r.raise_for_status()
data = r.json()
content = data["choices"][0]["message"]["content"]
usage = data.get("usage", {})
ct = usage.get("completion_tokens", 0)
print(f"耗时 {dt:.1f}s, completion_tokens={ct}, 速度 {ct / dt:.1f} tok/s")
print("内容:", content[:400])
json.loads(content)
print("JSON 解析: OK")
