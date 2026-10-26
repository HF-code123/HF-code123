"""前端依赖的抽象接口。

TUI 只依赖 ChatAgentProtocol，而不是具体的 ChatAgent 实现，
这样测试时可以注入假 Agent，未来 web / desktop / mobile 也可以复用同一套接口。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from .events import AgentEvent


class ChatAgentProtocol(Protocol):
    """所有前端与 Agent 交互的唯一入口。"""

    @property
    def model_name(self) -> str:
        """当前使用的模型名称，供前端展示。"""
        ...

    def stream_reply(self, user_text: str) -> AsyncIterator[AgentEvent]:
        """处理一条用户消息，并流式产生 AgentEvent。"""
        ...

    def reset(self) -> None:
        """清空对话上下文。"""
        ...
