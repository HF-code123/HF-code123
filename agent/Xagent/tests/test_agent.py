from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any

from core.agent import ChatAgent
from core.events import (
    AgentError,
    AgentEvent,
    TextDelta,
    ToolCallStarted,
    ToolResult,
    TurnCompleted,
)
from core.llm import StreamItem, StreamText, StreamToolCalls, ToolCall
from core.tools.registry import ToolRegistry, ToolSpec


class FakeLLM:
    def __init__(self, turns: list[list[StreamItem]]) -> None:
        self._turns = turns
        self.calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []

    @property
    def model_name(self) -> str:
        return "fake-model"

    async def stream(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
    ) -> AsyncIterator[StreamItem]:
        self.calls.append((list(messages), list(tools)))
        turn = self._turns.pop(0)
        for item in turn:
            yield item


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="echo",
            description="回显文本",
            parameters={"type": "object", "properties": {}},
            handler=lambda arguments: f"echo:{arguments['text']}",
        )
    )
    return registry


async def _collect(agent: ChatAgent, text: str) -> list[AgentEvent]:
    return [event async for event in agent.stream_reply(text)]


def test_agent_executes_tool_and_completes_turn() -> None:
    llm = FakeLLM(
        [
            [
                StreamText("先调用工具。"),
                StreamToolCalls((ToolCall("call-1", "echo", '{"text": "hi"}'),)),
            ],
            [StreamText("最终回答")],
        ]
    )
    agent = ChatAgent(llm, _registry(), system_prompt="system")

    events = asyncio.run(_collect(agent, "你好"))

    assert [type(event) for event in events] == [
        TextDelta,
        ToolCallStarted,
        ToolResult,
        TextDelta,
        TurnCompleted,
    ]
    assert isinstance(events[1], ToolCallStarted)
    assert events[1].arguments == {"text": "hi"}
    assert isinstance(events[2], ToolResult)
    assert events[2].result == "echo:hi"
    assert isinstance(events[4], TurnCompleted)
    assert events[4].content == "最终回答"

    roles = [message["role"] for message in agent.messages]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
    assert llm.calls[0][1][0]["function"]["name"] == "echo"


def test_agent_reports_invalid_arguments() -> None:
    llm = FakeLLM(
        [
            [StreamToolCalls((ToolCall("call-1", "echo", "{bad"),))],
            [StreamText("已恢复")],
        ]
    )
    agent = ChatAgent(llm, _registry())

    events = asyncio.run(_collect(agent, "test"))

    started = next(event for event in events if isinstance(event, ToolCallStarted))
    result = next(event for event in events if isinstance(event, ToolResult))
    assert started.arguments == {}
    assert "不是合法 JSON" in result.result
    assert isinstance(events[-1], TurnCompleted)


def test_agent_translates_exceptions_to_error_event() -> None:
    class BrokenLLM(FakeLLM):
        async def stream(self, messages, tools):
            raise RuntimeError("网络中断")
            yield StreamText("never")

    agent = ChatAgent(BrokenLLM([]), _registry())

    events = asyncio.run(_collect(agent, "test"))

    assert len(events) == 1
    assert isinstance(events[0], AgentError)
    assert "网络中断" in events[0].message


def test_agent_reset_keeps_system_prompt() -> None:
    agent = ChatAgent(FakeLLM([]), _registry(), system_prompt="system")

    asyncio.run(_collect(agent, "   "))
    agent.reset()

    assert agent.messages == [{"role": "system", "content": "system"}]
