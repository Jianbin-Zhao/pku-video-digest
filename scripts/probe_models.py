"""探测百炼账号可用的模型，并对同一条输入横向对比不同档位的归纳质量。

不靠文档猜测，直接问服务端要列表，再拿真实数据跑一遍。
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from vspider.settings import load_env, require  # noqa: E402

CANDIDATES = [
    "qwen-flash",
    "qwen-plus",
    "qwen-turbo",
    "qwen-max",
    "qwen3-max",
    "qwen3.7-plus",
    "qwen3.7-max",
    "qwen3.8-plus",
    "qwen3.8-max",
    "qwen-vl-plus",
    "qwen-vl-max",
    "qwen3-vl-plus",
    "qwen-omni-turbo",
    "qwen3-asr-flash",
    "text-embedding-v4",
    "deepseek-v4-flash",
    "deepseek-v4-pro",
]


async def main() -> int:
    load_env()
    key = require("DASHSCOPE_API_KEY")
    base = require("DASHSCOPE_BASE_URL")

    async with httpx.AsyncClient(
        base_url=base.rstrip("/"),
        headers={"Authorization": f"Bearer {key}"},
        timeout=30,
        trust_env=False,
    ) as client:
        print("== /models 列表 ==")
        try:
            response = await client.get("/models")
            if response.status_code == 200:
                names = sorted(m.get("id", "") for m in response.json().get("data", []))
                print(f"  共 {len(names)} 个：")
                for name in names:
                    print(f"    {name}")
            else:
                print(f"  HTTP {response.status_code}: {response.text[:200]}")
        except Exception as exc:  # noqa: BLE001
            print(f"  失败: {exc}")

        print("\n== 逐个探测候选模型（发 1 token 请求看是否放行）==")
        for model in CANDIDATES:
            try:
                response = await client.post(
                    "/chat/completions",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "hi"}],
                        "max_tokens": 1,
                    },
                )
                if response.status_code == 200:
                    print(f"  [可用]   {model}")
                else:
                    detail = response.json().get("error", {}).get("message", "")
                    print(f"  [不可用] {model}  HTTP {response.status_code} {detail[:90]}")
            except Exception as exc:  # noqa: BLE001
                print(f"  [错误]   {model}  {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
