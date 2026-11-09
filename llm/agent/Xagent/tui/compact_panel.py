"""/compact 完成后的浮动对照面板。

左栏显示压缩前的原始上下文（逐条消息），右栏显示压缩摘要；
两栏均可滚动，Esc 或点击「关闭」按钮退出面板。
"""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Markdown, Static

# 工具结果在面板中最多展示的字符数（完整内容在归档文件里）
MAX_TOOL_RESULT_CHARS = 600


def _render_message(message: dict[str, Any]) -> str:
    """把一条原始消息渲染成面板里可读的文本。"""

    role = message.get("role", "?")
    if role == "user":
        return f"【用户】\n{message.get('content') or ''}"
    if role == "assistant":
        parts = ["【助手】"]
        content = message.get("content")
        if content:
            parts.append(str(content))
        for call in message.get("tool_calls") or []:
            function = call.get("function", {})
            parts.append(
                f"调用工具 {function.get('name', '?')}"
                f"({function.get('arguments', '')})"
            )
        return "\n".join(parts)
    if role == "tool":
        content = str(message.get("content") or "")
        if len(content) > MAX_TOOL_RESULT_CHARS:
            content = (
                content[:MAX_TOOL_RESULT_CHARS]
                + "…（过长已截断，完整内容见归档文件）"
            )
        return f"【工具结果 {message.get('tool_call_id', '')}】\n{content}"
    return f"【{role}】\n{message.get('content') or ''}"


class CompactResultPanel(ModalScreen[None]):
    """压缩结果对照面板：左原始上下文，右压缩摘要。"""

    BINDINGS = [("escape", "close_panel", "关闭")]

    def __init__(self, result) -> None:
        super().__init__()
        self._result = result

    def compose(self) -> ComposeResult:
        original_text = "\n\n".join(
            _render_message(message)
            for message in self._result.original_messages
        )
        header = (
            f"上下文压缩完成 · 归档编号 {self._result.archive_id} · "
            f"{self._result.tokens_before / 1000:.1f}k → "
            f"{self._result.tokens_after / 1000:.1f}k · "
            f"共 {len(self._result.original_messages)} 条原始消息"
        )
        with Vertical(id="compact-panel"):
            yield Static(
                f"{header}（Esc 或点击「关闭」退出）",
                id="compact-header",
            )
            with Horizontal(id="compact-body"):
                with VerticalScroll(id="compact-original"):
                    yield Static(original_text, markup=False)
                with VerticalScroll(id="compact-summary"):
                    yield Markdown(self._result.summary)
            yield Button("关闭", id="compact-close", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "compact-close":
            self.action_close_panel()

    def action_close_panel(self) -> None:
        self.app.pop_screen()
        self.app.query_one("#prompt").focus()
