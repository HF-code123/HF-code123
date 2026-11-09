"""聊天记录容器：只负责添加消息和滚动，不关心 Agent 如何产生事件。"""

from __future__ import annotations

from textual.containers import VerticalScroll

from .messages import (
    AssistantMessage,
    ErrorMessage,
    NoticeMessage,
    ToolMessage,
    UserMessage,
)


class ChatView(VerticalScroll):
    async def add_user(self, content: str) -> UserMessage:
        widget = UserMessage(content)
        await self.mount(widget)
        self.scroll_to_bottom()
        return widget

    async def add_assistant(self) -> AssistantMessage:
        widget = AssistantMessage()
        await self.mount(widget)
        self.scroll_to_bottom()
        return widget

    async def add_tool(self, name: str, arguments: dict) -> ToolMessage:
        widget = ToolMessage(name, arguments)
        await self.mount(widget)
        self.scroll_to_bottom()
        return widget

    async def add_error(self, message: str) -> ErrorMessage:
        widget = ErrorMessage(message)
        await self.mount(widget)
        self.scroll_to_bottom()
        return widget

    async def add_notice(self, message: str) -> NoticeMessage:
        widget = NoticeMessage(message)
        await self.mount(widget)
        self.scroll_to_bottom()
        return widget

    async def clear_messages(self) -> None:
        await self.remove_children()

    def scroll_to_bottom(self) -> None:
        self.scroll_end(animate=False)
