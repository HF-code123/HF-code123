"""聊天区域使用的展示型控件。

这些控件只负责渲染 AgentEvent 对应的内容，不参与任何业务流程。
未来 web / desktop 前端可以复用相同的事件语义，但使用各自框架的组件。
"""

from __future__ import annotations

import json
from typing import Any

from rich.text import Text
from textual.widgets import Markdown, Static

MAX_ARGUMENT_CHARS = 400
MAX_RESULT_CHARS = 1600


class UserMessage(Static):
    def __init__(self, content: str) -> None:
        body = Text()
        body.append("你\n", style="bold #7aa2f7")
        body.append(content)
        super().__init__(body, markup=False, classes="message user-message")


class AssistantMessage(Markdown):
    def __init__(self) -> None:
        super().__init__("", classes="message assistant-message")


class ToolMessage(Static):
    def __init__(self, tool_name: str, arguments: dict[str, Any]) -> None:
        self._tool_name = tool_name
        self._tool_arguments = _truncate(
            json.dumps(arguments, ensure_ascii=False), MAX_ARGUMENT_CHARS
        )
        self._tool_result = ""
        super().__init__(
            self._build_content(),
            markup=False,
            classes="message tool-message",
        )

    def set_result(self, result: str) -> None:
        self._tool_result = _truncate(result, MAX_RESULT_CHARS)
        self.update(self._build_content())

    def _build_content(self) -> Text:
        body = Text()
        body.append("工具 ", style="bold #e0af68")
        body.append(self._tool_name, style="bold #e0af68")
        if self._tool_arguments:
            body.append(f"\n参数: {self._tool_arguments}", style="#a9b1d6")
        if self._tool_result:
            body.append(f"\n结果: {self._tool_result}", style="#c0caf5")
        else:
            body.append("\n执行中...", style="#565f89")
        return body


class ErrorMessage(Static):
    def __init__(self, message: str) -> None:
        body = Text()
        body.append("错误\n", style="bold #f7768e")
        body.append(message)
        super().__init__(body, markup=False, classes="message error-message")


class NoticeMessage(Static):
    def __init__(self, message: str) -> None:
        super().__init__(
            Text(message, style="italic #565f89"),
            markup=False,
            classes="message notice-message",
        )


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...(已截断，共 {len(text)} 字符)"
