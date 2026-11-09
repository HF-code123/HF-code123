"""Xagent 核心层。

本包不依赖任何具体界面（TUI / Web / Desktop / Mobile），
只暴露配置、事件契约、Agent 接口与组装入口。
"""

from __future__ import annotations

from .agent import ChatAgent
from .config import AgentConfig, AppConfig, LLMConfig, load_config
from .contracts import ChatAgentProtocol
from .events import (
    AgentError,
    AgentEvent,
    TextDelta,
    ToolCallStarted,
    ToolResult,
    TurnCompleted,
)
from .factory import build_agent

__all__ = [
    "AgentConfig",
    "AgentError",
    "AgentEvent",
    "AppConfig",
    "ChatAgent",
    "ChatAgentProtocol",
    "LLMConfig",
    "TextDelta",
    "ToolCallStarted",
    "ToolResult",
    "TurnCompleted",
    "build_agent",
    "load_config",
]
