"""聊天区域使用的展示型控件。

这些控件只负责渲染 AgentEvent 对应的内容，不参与任何业务流程。
未来 web / desktop 前端可以复用相同的事件语义，但使用各自框架的组件。

视觉规范（对齐目标设计稿）：
- 用户消息：橙色左条 + 「你」标签
- 工具调用：可折叠卡片，绿色左条标题（▶/▼ /工具名(参数) · 耗时），
  展开后显示 参数（JSON）与 结果（蓝色左条）两段
- 助手回复：蓝色左条 + 「xAgent」标签
"""

from __future__ import annotations

import json
import time
from typing import Any

from rich.text import Text
from textual.containers import Vertical
from textual.widgets import Markdown, Static

MAX_ARGUMENT_CHARS = 400
MAX_RESULT_CHARS = 1600

USER_LABEL_STYLE = "bold #e0af68"
ASSISTANT_LABEL_STYLE = "bold #7aa2f7"
TOOL_TITLE_STYLE = "bold #9ece6a"


class UserMessage(Static):
    def __init__(self, content: str) -> None:
        body = Text()
        body.append("你\n", style=USER_LABEL_STYLE)
        body.append(content)
        super().__init__(body, markup=False, classes="message user-message")


class AssistantMessage(Vertical):
    """助手回复：标签 + Markdown 正文；对外暴露 update() 保持原 API。"""

    def __init__(self) -> None:
        super().__init__(classes="message assistant-message")
        self._label = Static("xAgent", markup=False, classes="assistant-label")
        self._body = Markdown("", classes="assistant-body")

    def compose(self):
        yield self._label
        yield self._body

    def update(self, content: str) -> None:  # type: ignore[override]
        self._body.update(content or "_（本轮没有文本回答）_")


class ToolMessage(Vertical):
    """工具调用卡片：标题行可点击折叠/展开。

    标题格式：`▼ / 名称(参数摘要) · 耗时`；运行中显示 `… 执行中`。
    """

    def __init__(self, tool_name: str, arguments: dict[str, Any]) -> None:
        self._tool_name = tool_name
        self._tool_arguments = arguments
        self._args_json = _truncate(
            json.dumps(arguments, ensure_ascii=False, indent=2),
            MAX_ARGUMENT_CHARS,
        )
        self._tool_result = ""
        self._elapsed_ms: int | None = None
        self._started_at = time.monotonic()
        self._expanded = True
        super().__init__(classes="message tool-message")

    def compose(self):
        yield Static(self._header_text(), markup=False, classes="tool-header")
        yield Static(
            Text("参数\n", style="bold #565f89")
            .append(self._args_summary(), style="#a9b1d6"),
            markup=False,
            classes="tool-params",
        )
        yield Static(
            Text("结果\n", style="bold #565f89").append("执行中...", style="#565f89"),
            markup=False,
            classes="tool-result",
        )

    # ---------- 渲染 ----------

    def _args_summary(self) -> str:
        """把参数压成 name(k=v, k=v) 摘要，用于标题行。"""

        if not self._tool_arguments:
            return "()"
        parts = []
        for key, value in self._tool_arguments.items():
            text = str(value)
            if len(text) > 24:
                text = text[:24] + "…"
            parts.append(f"{key}={text}")
        summary = ", ".join(parts)
        if len(summary) > 60:
            summary = summary[:60] + "…"
        return f"({summary})"

    def _header_text(self) -> Text:
        arrow = "▼" if self._expanded else "▶"
        body = Text()
        body.append(f"{arrow} / {self._tool_name}", style=TOOL_TITLE_STYLE)
        body.append(f"{self._args_summary()}", style="#565f89")
        if self._elapsed_ms is None:
            body.append(" · 执行中", style="#565f89")
        else:
            body.append(f" · {self._elapsed_ms} ms", style="#565f89")
        return body

    def _body_widgets(self) -> tuple[Static, Static]:
        params = self.query_one(".tool-params", Static)
        result = self.query_one(".tool-result", Static)
        return params, result

    def _refresh(self) -> None:
        self.query_one(".tool-header", Static).update(self._header_text())
        if self._expanded:
            params, result = self._body_widgets()
            params.update(
                Text("参数\n", style="bold #565f89").append(
                    self._args_json, style="#a9b1d6"
                )
            )
            result.update(self._result_text())

    def _result_text(self) -> Text:
        if not self._tool_result:
            return Text("结果\n", style="bold #565f89").append(
                "执行中...", style="#565f89"
            )
        return Text("结果\n", style="bold #565f89").append(
            self._tool_result, style="#c0caf5"
        )

    # ---------- 交互 ----------

    def on_click(self) -> None:
        self._expanded = not self._expanded
        for widget in self.query(".tool-params, .tool-result"):
            widget.display = self._expanded
        self.query_one(".tool-header", Static).update(self._header_text())

    def set_result(self, result: str) -> None:
        self._tool_result = _truncate(result, MAX_RESULT_CHARS)
        self._elapsed_ms = int((time.monotonic() - self._started_at) * 1000)
        self._refresh()


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
