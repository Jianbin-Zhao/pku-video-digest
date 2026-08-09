"""OpenAI 兼容协议的归纳后端。

一份实现覆盖三种部署：阿里云百炼、服务器上的 vLLM、本地 llama-server。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from vspider.fusion.context import FusionContext
from vspider.models import Sentiment, Summary
from vspider.summarize.base import Summarizer
from vspider.summarize.prompts import build_messages

_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_BARE_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


class RetryableAPIError(RuntimeError):
    """限流或服务端临时故障，值得重试。"""


class OpenAICompatSummarizer(Summarizer):
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        *,
        temperature: float = 0.3,
        max_tokens: int = 1024,
        timeout: float = 120.0,
        use_json_mode: bool = True,
        enable_thinking: bool = False,
        thinking_via: str = "field",
    ) -> None:
        self.name = f"{model}@{httpx.URL(base_url).host}"
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.use_json_mode = use_json_mode
        # Qwen3 系列默认开思考模式，归纳这种任务用不上，
        # 开着会让单条耗时和输出 token 数都翻好几倍，直接关掉。
        self.enable_thinking = enable_thinking
        # 关思考的传参方式不同：DashScope 认顶层 enable_thinking 字段（field），
        # 而 vLLM 的 OpenAI 服务对未知顶层字段会直接 400，必须走
        # chat_template_kwargs（template）。llama.cpp 两者都不认，靠提示词里的
        # /no_think 兜底（Qwen3 的 chat 模板支持这个软开关）。
        self.thinking_via = thinking_via

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
            trust_env=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def summarize(self, context: FusionContext) -> Summary:
        started = time.perf_counter()
        text = await self._chat(build_messages(context.render()))
        summary = _parse_summary(text)
        summary.backend = self.name
        summary.elapsed_sec = time.perf_counter() - started
        return summary

    @retry(
        retry=retry_if_exception_type((RetryableAPIError, httpx.TransportError)),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def _chat(self, messages: list[dict[str, str]]) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        if self.use_json_mode:
            payload["response_format"] = {"type": "json_object"}
        if not self.enable_thinking:
            if self.thinking_via == "template":
                payload["chat_template_kwargs"] = {"enable_thinking": False}
            elif self.thinking_via == "field":
                payload["enable_thinking"] = False

        response = await self._client.post("/chat/completions", json=payload)

        if response.status_code == 429 or response.status_code >= 500:
            raise RetryableAPIError(
                f"HTTP {response.status_code}: {response.text[:300]}"
            )
        if response.status_code >= 400:
            raise RuntimeError(
                f"归纳接口返回 HTTP {response.status_code}: {response.text[:500]}"
            )

        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"归纳接口未返回 choices: {str(data)[:300]}")
        return choices[0].get("message", {}).get("content") or ""


def _parse_summary(text: str) -> Summary:
    """从模型输出里取出 JSON。

    即使要求了不要包裹代码块、也开了 JSON mode，
    仍有后端（尤其本地小模型）会加上 ```json 围栏或前置说明文字，
    所以这里逐级放宽地尝试解析。
    """
    raw = _extract_json(text)
    if raw is None:
        raise ValueError(f"模型输出中找不到 JSON: {text[:300]!r}")

    return Summary(
        one_liner=str(raw.get("one_liner") or "").strip(),
        key_points=[str(p).strip() for p in (raw.get("key_points") or []) if str(p).strip()],
        topics=[str(t).strip() for t in (raw.get("topics") or []) if str(t).strip()],
        sentiment=_coerce_sentiment(raw.get("sentiment")),
        is_promotion=bool(raw.get("is_promotion")),
        confidence=_coerce_confidence(raw.get("confidence")),
    )


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    for candidate in (text, _first_group(_JSON_FENCE, text), _first_group(_BARE_OBJECT, text)):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _first_group(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1) if match and match.groups() else (match.group(0) if match else None)


def _coerce_sentiment(value: Any) -> Sentiment:
    try:
        return Sentiment(str(value).strip().lower())
    except ValueError:
        return Sentiment.NEUTRAL


def _coerce_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
