"""归纳后端抽象。

三种部署形态共用同一个接口，靠配置 profile 切换：

    api   云端服务商（阿里云百炼 DashScope）
    gpu   服务器上的 vLLM
    cpu   本地 llama.cpp 的 llama-server

因为这三者都提供 OpenAI 兼容的 /chat/completions，实际上只需要
OpenAICompatSummarizer 一个实现，区别仅在 base_url、模型名和是否需要 api_key。
这也是把归纳层做成可插拔的收益：切后端不用改任何业务代码。
"""

from __future__ import annotations

import abc

from vspider.fusion.context import FusionContext
from vspider.models import Summary


class Summarizer(abc.ABC):
    name: str

    @abc.abstractmethod
    async def summarize(self, context: FusionContext) -> Summary:
        """对一条视频产出结构化归纳。"""

    async def chat_json(self, messages: list[dict[str, str]]) -> dict:
        """自由对话并要求返回 JSON 对象。

        跨视频总览（digest）这类聚合任务的提示词不适合塞进 summarize 的
        固定模板，所以额外开一个通用入口。默认不支持。
        """
        raise NotImplementedError(f"{self.name} 不支持通用 JSON 对话")

    async def aclose(self) -> None:
        """释放底层连接。默认无操作。"""
