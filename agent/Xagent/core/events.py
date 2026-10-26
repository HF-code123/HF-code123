"""Core 与任意前端之间的数据契约。

前端（tui / web / desktop / mobile）只消费这些事件，
不需要了解 OpenAI 客户端、工具注册表等内部实现。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TextDelta:
    """模型流式输出的一个文本片段。"""

    text: str


@dataclass(frozen=True)
class ToolCallStarted:
    """模型决定调用某个工具，参数已经解析为字典。"""

    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    """工具执行完成后的文本结果。"""

    call_id: str
    name: str
    result: str


@dataclass(frozen=True)
class TurnCompleted:
    """一轮用户请求结束，content 是本轮最终的助手文本。"""

    content: str


@dataclass(frozen=True)
class AgentError:
    """本轮执行失败。前端应展示错误，但不影响后续对话。"""

    message: str


AgentEvent = (
    TextDelta | ToolCallStarted | ToolResult | TurnCompleted | AgentError
)
