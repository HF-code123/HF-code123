"""工具层入口：按业务领域组装默认工具注册表。"""

from __future__ import annotations

from ..config import SearchConfig
from . import (
    datetime_tools,
    fetch_web,
    file_query_tools,
    file_write_tools,
    path_tools,
    run_cli,
    web_search,
)
from .registry import ToolRegistry, ToolSpec

__all__ = ["ToolRegistry", "ToolSpec", "build_default_registry"]


def build_default_registry(search: SearchConfig | None = None) -> ToolRegistry:
    """组装默认工具集。

    - 本地工具始终注册；
    - fetchWeb 只依赖系统 curl，始终可用；
    - runCli 调用本机命令行程序，始终可用（教学环境，无白名单/审批）；
    - searchWeb 需要 [search] 配置了 api_key，未配置时不注册，
      这样即使没有联网搜索额度，Agent 也能正常工作。
    """

    registry = ToolRegistry()
    datetime_tools.register(registry)
    file_query_tools.register(registry)
    file_write_tools.register(registry)
    path_tools.register(registry)
    fetch_web.register(registry)
    run_cli.register(registry)
    if search is not None and search.enabled:
        web_search.register(registry, search)
    return registry
