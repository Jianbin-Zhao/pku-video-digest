"""热词搜索、互动量重排和熔断。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from vspider.models import VideoItem

_LOG = logging.getLogger(__name__)

_CANDIDATE_MULTIPLIER = 3


def _unwrap_error(exc: BaseException) -> str:
    """提取 tenacity 包装的原始错误。"""
    last_attempt = getattr(exc, "last_attempt", None)
    if last_attempt is not None and last_attempt.exception() is not None:
        exc = last_attempt.exception()
    text = str(exc) or type(exc).__name__
    if "没有权限" in text:
        return f"{text}（账号被平台临时风控，请过几小时再试或换账号）"
    return f"{type(exc).__name__}: {text}"


@dataclass
class KeywordBudget:
    """单次采集的请求预算。"""

    max_keywords: int = 6
    # 连续失败这么多次就停。设 2 而不是 1，是为了容忍单次网络抖动；
    # 但绝不能不设上限——被限流时继续请求只会让封禁更久。
    max_consecutive_failures: int = 2
    # 词与词之间的间隔。这是最有效的防限流手段，不要为了跑得快调小。
    delay_sec: float = 1.0
    # 单个热词的硬超时。MediaCrawler 的客户端内部用 tenacity 自动重试，
    # 一次 ConnectTimeout 会在里面重试很多轮，单次调用能拖到两三分钟，
    # 叠上熔断的两次就是五六分钟的「假死」。这个超时不管底层重试多少轮，
    # 到点就切，把最坏情况钉死在可控范围。
    per_keyword_timeout: float = 30.0

    _consecutive: int = field(default=0, init=False)
    failures: list[str] = field(default_factory=list, init=False)

    def record_success(self) -> None:
        self._consecutive = 0

    def record_failure(self, reason: str) -> None:
        self._consecutive += 1
        self.failures.append(reason)

    @property
    def tripped(self) -> bool:
        return self._consecutive >= self.max_consecutive_failures


class KeywordRejected(Exception):
    """平台明确拒绝了这次请求（限流、要求登录等），而不是「搜不到」。

    必须和「返回空结果」区分开：前者应该触发熔断，后者只是换个词继续。
    """


async def collect_by_keywords(
    keywords: list[str],
    search: Callable[[str], Awaitable[list[dict[str, Any]]]],
    parse: Callable[[dict[str, Any]], VideoItem | None],
    limit: int,
    platform_name: str,
    budget: KeywordBudget | None = None,
) -> list[VideoItem]:
    """逐个热词搜索并汇总，按互动量降序返回。

    Args:
        keywords: 候选热词，按热度排好序。
        search: 传入热词，返回该词的原始条目列表。
              被平台拒绝时应抛 KeywordRejected，以便触发熔断。
        parse: 把单个原始条目转成 VideoItem，不是视频就返回 None。
        limit: 最终需要的条数。
        platform_name: 仅用于日志。
        budget: 请求预算，默认见 KeywordBudget。
    """
    budget = budget or KeywordBudget()
    collected: dict[str, VideoItem] = {}
    enough = max(limit * _CANDIDATE_MULTIPLIER, limit)

    for index, keyword in enumerate(keywords[: budget.max_keywords]):
        try:
            entries = await asyncio.wait_for(
                search(keyword), timeout=budget.per_keyword_timeout
            )
        except asyncio.TimeoutError:
            budget.record_failure(f"超时（>{budget.per_keyword_timeout:.0f}s）")
            _LOG.warning(
                "%s 搜索 %r 超过 %.0fs 无响应，跳过（多为网络不通或 IP 被限）",
                platform_name,
                keyword,
                budget.per_keyword_timeout,
            )
            if budget.tripped:
                _LOG.warning("%s 连续超时，停止本次采集", platform_name)
                break
            continue
        except KeywordRejected as exc:
            budget.record_failure(str(exc))
            _LOG.warning("%s 搜索 %r 被拒：%s", platform_name, keyword, exc)
            if budget.tripped:
                _LOG.warning(
                    "%s 连续 %d 次被拒，立即停止本次采集。"
                    "继续请求只会加深限流，请隔几分钟再试",
                    platform_name,
                    budget.max_consecutive_failures,
                )
                break
            await asyncio.sleep(budget.delay_sec * 2)
            continue
        except Exception as exc:  # noqa: BLE001
            detail = _unwrap_error(exc)
            budget.record_failure(detail)
            _LOG.warning(
                "%s 搜索 %r 出错：%s",
                platform_name,
                keyword,
                detail[:140],
            )
            if budget.tripped:
                _LOG.warning("%s 连续出错，停止本次采集", platform_name)
                break
            await asyncio.sleep(budget.delay_sec)
            continue

        budget.record_success()
        for entry in entries:
            item = parse(entry)
            if item is not None and item.video_id not in collected:
                item.tags = item.tags or [keyword]
                collected[item.video_id] = item

        if len(collected) >= enough:
            _LOG.debug(
                "%s 用 %d 个词就攒够 %d 条候选",
                platform_name,
                index + 1,
                len(collected),
            )
            break
        await asyncio.sleep(budget.delay_sec)

    if not collected and budget.failures:
        _LOG.warning(
            "%s 一条都没取到，%d 次请求全部失败。首个原因：%s",
            platform_name,
            len(budget.failures),
            budget.failures[0][:120],
        )

    return sorted(
        collected.values(), key=lambda i: i.stats.engagement(), reverse=True
    )
