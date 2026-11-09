from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from core.events import (
    AgentError,
    AgentEvent,
    TextDelta,
    ToolCallStarted,
    ToolResult,
    TurnCompleted,
)
from tui.app import XagentApp
from tui.widgets import ChatView


class FakeAgent:
    @property
    def model_name(self) -> str:
        return "fake-model"

    def reset(self) -> None:
        pass

    async def stream_reply(self, user_text: str) -> AsyncIterator[AgentEvent]:
        yield TextDelta("先看看。")
        yield ToolCallStarted("call-1", "readFile", {"path": "a.txt"})
        yield ToolResult("call-1", "readFile", "文件内容")
        yield TextDelta("完成。")
        yield TurnCompleted("完成。")
        yield AgentError("示例错误")


def test_tui_renders_all_message_types() -> None:
    async def run_test() -> None:
        app = XagentApp(FakeAgent())
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app.query_one("#prompt").value = "测试"
            await pilot.press("enter")
            await pilot.pause(0.5)

            chat = app.query_one(ChatView)
            types = [type(child).__name__ for child in chat.children]
            assert types == [
                "NoticeMessage",
                "UserMessage",
                "AssistantMessage",
                "ToolMessage",
                "AssistantMessage",
                "ErrorMessage",
            ]
            assert app._busy is False

            await app.action_clear_chat()
            await pilot.pause(0.1)
            assert [type(child).__name__ for child in chat.children] == [
                "NoticeMessage"
            ]

    asyncio.run(run_test())
