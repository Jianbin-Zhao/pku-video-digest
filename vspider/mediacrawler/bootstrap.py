"""把 MediaCrawler 挂到 import 路径上，并压掉它的全局副作用。

MediaCrawler 是按「独立命令行程序」写的，不是按库写的：
它的模块从仓库根目录做绝对导入（`import config`、`from tools import utils`），
且 import 时就会初始化日志、读配置。所以直接 import 之前要先做两件事——
把仓库根加进 sys.path，以及把那些会干扰宿主程序的全局行为按住。
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
from collections.abc import Iterator
from pathlib import Path

_DEFAULT_ROOT = Path(__file__).resolve().parents[3] / "MediaCrawler"
_initialized = False

# 这些模块在 import 或构造时会用相对路径读文件，必须在仓库根目录下才能成功。
_CWD_SENSITIVE_MODULES = (
    # douyin/help.py 顶层就执行 execjs.compile(open('libs/douyin.js'))，
    # 也就是说 import 这一刻就要求 cwd 正确，晚一步都来不及。
    "media_platform.douyin.help",
    "media_platform.douyin.client",
    "media_platform.kuaishou.client",
    "media_platform.weibo.client",
    "media_platform.xhs.client",
)


def mediacrawler_root() -> Path:
    return Path(os.environ.get("MEDIACRAWLER_ROOT", str(_DEFAULT_ROOT)))


def ensure_importable() -> Path:
    """幂等地把 MediaCrawler 装进 import 路径，返回它的根目录。"""
    global _initialized
    root = mediacrawler_root()
    if _initialized:
        return root

    if not (root / "media_platform").is_dir():
        raise RuntimeError(
            f"在 {root} 下找不到 MediaCrawler。"
            f"请 clone 到该位置，或用环境变量 MEDIACRAWLER_ROOT 指定。"
        )

    if str(root) not in sys.path:
        # 插到最前面：它有 tools / config / base 这类极易与其他包重名的顶层模块，
        # 放在后面会被同名包抢先解析。
        sys.path.insert(0, str(root))

    _silence_logging()
    with working_directory(root):
        _preimport_cwd_sensitive_modules()
        _patch_kuaishou_graphql_path(root)
    _initialized = True
    return root


@contextlib.contextmanager
def working_directory(path: Path) -> Iterator[None]:
    """临时切换工作目录，退出时还原。"""
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _preimport_cwd_sensitive_modules() -> None:
    """在正确的工作目录下把依赖相对路径的模块先导入好。

    只需要成功一次：Python 会把它们缓存在 sys.modules 里，
    之后无论宿主程序的 cwd 在哪，再 import 都直接命中缓存。
    这样就不必让整个程序长期 chdir 到别人的仓库目录下。
    """
    for name in _CWD_SENSITIVE_MODULES:
        try:
            __import__(name)
        except Exception as exc:  # noqa: BLE001
            # 某个平台导入失败不该让其他平台一起用不了，
            # 真正调用到它时会以更清晰的形式报错。
            logging.getLogger(__name__).warning(
                "预导入 %s 失败：%s: %s", name, type(exc).__name__, exc
            )


def _patch_kuaishou_graphql_path(root: Path) -> None:
    """把快手 GraphQL 的查询目录改成绝对路径。

    它写死了相对路径 "media_platform/kuaishou/graphql/"，而这个对象是在
    客户端构造时才 new 的——那时宿主程序早就不在仓库目录下了。
    预导入救不了它，只能改路径。
    """
    try:
        from media_platform.kuaishou.graphql import KuaiShouGraphQL
    except Exception:  # noqa: BLE001
        return

    absolute = str(root / "media_platform" / "kuaishou" / "graphql") + os.sep
    original = KuaiShouGraphQL.load_graphql_queries

    def load_with_absolute_dir(self: object) -> None:
        self.graphql_dir = absolute  # type: ignore[attr-defined]
        original(self)

    KuaiShouGraphQL.load_graphql_queries = load_with_absolute_dir  # type: ignore[method-assign]


def _silence_logging() -> None:
    """把 MediaCrawler 及其依赖的日志降噪。

    它默认按 INFO 打印每一次请求，跑一批视频会刷出几百行，
    把流水线自己的进度完全淹没。这里只保留警告及以上。
    """
    for name in ("MediaCrawler", "httpx", "httpcore", "playwright", "asyncio"):
        logging.getLogger(name).setLevel(logging.WARNING)


def load_config_defaults(**overrides: object) -> None:
    """覆盖 MediaCrawler 的全局 config。

    它的客户端在运行时会读若干全局配置项（重试次数、是否启用代理池等）。
    这里显式设成适合被当作库调用的值，而不是依赖它仓库里的默认值——
    那份默认值是给命令行场景准备的，比如会开启数据库存储。
    """
    ensure_importable()
    import config  # noqa: PLC0415

    defaults: dict[str, object] = {
        "ENABLE_IP_PROXY": False,
        "SAVE_DATA_OPTION": "json",
        "ENABLE_GET_COMMENTS": False,
        "ENABLE_GET_SUB_COMMENTS": False,
        "ENABLE_GET_MEIDAS": False,
        "CRAWLER_MAX_SLEEP_SEC": 1,
    }
    defaults.update(overrides)
    for key, value in defaults.items():
        # 只覆盖它确实定义过的项，避免因为上游改名而悄悄写进一个没人读的属性。
        if hasattr(config, key):
            setattr(config, key, value)
