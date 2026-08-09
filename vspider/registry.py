"""组件装配。

把「按平台选采集器/下载器」和「按部署形态选推理后端」这两件事集中到一处，
CLI 与 Web 后端共用同一套装配逻辑，避免两边配置漂移。

三种部署形态（profile）对应老师「尽量本地部署」这个要求的三个档位：

    api    阿里云百炼。最快、零显存占用，用于开发调试和演示
    gpu    服务器上的 vLLM 起 Qwen3-8B。完全本地，无外部依赖
    cpu    本地 llama.cpp 的 llama-server 起量化模型。无显卡也能跑

三者都提供 OpenAI 兼容接口，所以业务代码一行都不用改。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from vspider.asr.base import AsrBackend
from vspider.discovery.base import RankingProvider
from vspider.download.base import Downloader
from vspider.models import Platform
from vspider.ocr.rapid import RapidOcr
from vspider.settings import load_env, require
from vspider.summarize.base import Summarizer

PLATFORM_ALIASES: dict[str, Platform] = {
    "bili": Platform.BILIBILI,
    "bilibili": Platform.BILIBILI,
    "b站": Platform.BILIBILI,
    "dy": Platform.DOUYIN,
    "douyin": Platform.DOUYIN,
    "抖音": Platform.DOUYIN,
    "ks": Platform.KUAISHOU,
    "kuaishou": Platform.KUAISHOU,
    "快手": Platform.KUAISHOU,
    "wb": Platform.WEIBO,
    "weibo": Platform.WEIBO,
    "微博": Platform.WEIBO,
    "xhs": Platform.XHS,
    "xiaohongshu": Platform.XHS,
    "小红书": Platform.XHS,
}


def resolve_platform(name: str) -> Platform:
    key = name.strip().lower()
    if key not in PLATFORM_ALIASES:
        supported = ", ".join(sorted({p.value for p in Platform}))
        raise ValueError(f"未知平台 {name!r}，可选：{supported}")
    return PLATFORM_ALIASES[key]


@dataclass
class Paths:
    models_root: Path
    data_root: Path

    @classmethod
    def from_env(cls) -> Paths:
        # 服务器上模型和数据都放数据盘，系统盘只有 30G 放不下。
        return cls(
            models_root=Path(
                os.environ.get("VSPIDER_MODELS_ROOT", "/root/autodl-tmp/models")
            ),
            data_root=Path(
                os.environ.get("VSPIDER_DATA_ROOT", "/root/autodl-tmp/data")
            ),
        )


# 每档的默认模型。api 档默认用 qwen-flash 而不是更强的 qwen3.8-max，
# 依据是实验 E1：归纳质量的瓶颈在输入是否完整，而不在模型大小，
# 补上 OCR 带来的提升远大于换更贵的模型。详见 docs/EXPERIMENTS.md。
_LLM_PROFILES: dict[str, dict[str, str]] = {
    "api": {
        "base_url_env": "DASHSCOPE_BASE_URL",
        "api_key_env": "DASHSCOPE_API_KEY",
        "default_model": "qwen-flash",
    },
    "gpu": {
        "base_url_env": "VSPIDER_VLLM_BASE_URL",
        "api_key_env": "",
        "default_model": "Qwen3-8B",
        "fallback_base_url": "http://127.0.0.1:8000/v1",
    },
    "cpu": {
        "base_url_env": "VSPIDER_LLAMA_BASE_URL",
        "api_key_env": "",
        "default_model": "local",
        "fallback_base_url": "http://127.0.0.1:8080/v1",
    },
}

# 升级档用的模型。只在低置信度时触发，所以可以放心用贵的。
ESCALATION_MODEL = os.environ.get("VSPIDER_ESCALATION_MODEL", "qwen-plus")


# 需要浏览器会话才能工作的平台。它们的接口都带 JS 签名，
# 且实测均要求登录态，绕不开真实浏览器。
BROWSER_PLATFORMS = frozenset(
    {Platform.DOUYIN, Platform.KUAISHOU, Platform.WEIBO, Platform.XHS}
)


def build_provider(
    platform: Platform, session: object | None = None
) -> RankingProvider:
    """构造平台采集器。

    Args:
        platform: 目标平台。
        session: MediaCrawlerSession 实例。BROWSER_PLATFORMS 中的平台必须提供；
            B 站直连官方接口，不需要。
    """
    if platform is Platform.BILIBILI:
        from vspider.discovery.bilibili import BilibiliRankingProvider

        return BilibiliRankingProvider()

    if platform in BROWSER_PLATFORMS:
        if session is None:
            raise ValueError(
                f"{platform.value} 需要浏览器会话，请传入 MediaCrawlerSession"
            )
        return _build_browser_provider(platform, session)

    raise NotImplementedError(f"{platform.value} 的榜单采集尚未接入")


def _build_browser_provider(platform: Platform, session: object) -> RankingProvider:
    if platform is Platform.DOUYIN:
        from vspider.discovery.douyin import DouyinRankingProvider

        return DouyinRankingProvider(session)  # type: ignore[arg-type]
    if platform is Platform.KUAISHOU:
        from vspider.discovery.kuaishou import KuaishouRankingProvider

        return KuaishouRankingProvider(session)  # type: ignore[arg-type]
    if platform is Platform.WEIBO:
        from vspider.discovery.weibo import WeiboRankingProvider

        return WeiboRankingProvider(session)  # type: ignore[arg-type]

    from vspider.discovery.xhs import XhsRankingProvider

    return XhsRankingProvider(session)  # type: ignore[arg-type]


def build_downloader(platform: Platform, session: object | None = None) -> Downloader:
    """按平台选下载后端。

    B 站走 yt-dlp：它需要处理 DASH 分片音视频合流，yt-dlp 在这方面成熟稳定。

    其余四个平台走直链：采集阶段已经从接口里拿到了视频地址，
    再让 yt-dlp 解析一遍页面纯属多余，而且它对抖音、快手的 extractor
    经常跟不上平台改版。传入 session 是为了复用浏览器的 cookie 与 UA——
    这些 CDN 会校验 Referer 和会话，请求头不对就 403。
    """
    if platform is Platform.BILIBILI:
        from vspider.download.ytdlp_backend import YtDlpDownloader

        return YtDlpDownloader()

    if platform in BROWSER_PLATFORMS:
        from vspider.download.direct import DirectUrlDownloader

        return DirectUrlDownloader(session=session)

    raise NotImplementedError(f"{platform.value} 的下载后端尚未接入")


def build_asr(paths: Paths | None = None, device: str = "cuda:0") -> AsrBackend:
    from vspider.asr.sensevoice import SenseVoiceAsr

    paths = paths or Paths.from_env()
    return SenseVoiceAsr(
        model_dir=str(paths.models_root / "SenseVoiceSmall"),
        vad_dir=str(paths.models_root / "fsmn-vad"),
        device=device,
    )


def build_ocr(workers: int | None = None) -> RapidOcr:
    return RapidOcr(workers=workers)


def build_summarizer(
    profile: str = "api",
    model: str = "",
    *,
    max_tokens: int = 1024,
) -> Summarizer:
    from vspider.summarize.openai_compat import OpenAICompatSummarizer

    if profile not in _LLM_PROFILES:
        raise ValueError(
            f"未知部署形态 {profile!r}，可选：{', '.join(_LLM_PROFILES)}"
        )
    spec = _LLM_PROFILES[profile]
    load_env()

    if profile == "api":
        base_url = require(spec["base_url_env"])
        api_key = require(spec["api_key_env"])
    else:
        # 本地后端不需要密钥，地址也允许缺省到约定端口。
        base_url = os.environ.get(spec["base_url_env"]) or spec["fallback_base_url"]
        api_key = ""

    # 关思考模式的传参方式随后端而异：DashScope 用顶层字段，vLLM 用
    # chat_template_kwargs（gpu），llama.cpp 两者都不认则不传（cpu）。
    thinking_via = {"api": "field", "gpu": "template", "cpu": "none"}[profile]

    return OpenAICompatSummarizer(
        base_url=base_url,
        model=model or spec["default_model"],
        api_key=api_key,
        max_tokens=max_tokens,
        thinking_via=thinking_via,
    )
