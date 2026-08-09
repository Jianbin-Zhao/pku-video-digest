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

    async def aclose(self) -> None:
        """释放底层连接。默认无操作。"""
