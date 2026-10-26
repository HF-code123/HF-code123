"""Agent 编排层。

ChatAgent 是 core 层的应用服务：它把「对话上下文 + LLM 客户端 + 工具注册表」
编排成一轮完整的问答流程，并只通过 AgentEvent 与前端通信。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from .conversation import Conversation
from .events import (
    AgentError,
    AgentEvent,
    TextDelta,
    ToolCallStarted,
    ToolResult,
    TurnCompleted,
)
from .llm import LLMClientProtocol, StreamText, StreamToolCalls, ToolCall
from .tools.registry import ToolRegistry


class ChatAgent:
    def __init__(
        self,
        llm: LLMClientProtocol,
        tools: ToolRegistry,
        *,
        system_prompt: str = "",
        max_tool_rounds: int = 8,
    ) -> None:
        if max_tool_rounds <= 0:
            raise ValueError("max_tool_rounds 必须大于 0")
        self._llm = llm
        self._tools = tools
        self._conversation = Conversation(system_prompt)
        self._max_tool_rounds = max_tool_rounds

    @property
    def model_name(self) -> str:
        return self._llm.model_name

    @property
    def messages(self) -> list[dict[str, Any]]:
        """只读快照，便于调试、持久化或未来接入其他前端。"""

        return self._conversation.messages

    def reset(self) -> None:
        self._conversation.clear()

    async def stream_reply(self, user_text: str) -> AsyncIterator[AgentEvent]:
        """处理一条用户消息，期间可能多次调用工具，直到模型给出最终回答。"""

        text = user_text.strip()
        if not text:
            return

        self._conversation.add_user(text)
        try:
            for _ in range(self._max_tool_rounds):
                content_parts: list[str] = []
                tool_calls: tuple[ToolCall, ...] = ()

                async for item in self._llm.stream(
                    self._conversation.messages,
                    self._tools.definitions(),
                ):
                    if isinstance(item, StreamText):
                        content_parts.append(item.text)
                        yield TextDelta(item.text)
                    elif isinstance(item, StreamToolCalls):
                        tool_calls = item.calls

                content = "".join(content_parts)

                if not tool_calls:
                    self._conversation.add_assistant(content)
                    yield TurnCompleted(content)
                    return

                self._conversation.add_assistant(
                    content or None,
                    [self._tool_call_message(call) for call in tool_calls],
                )

                for call in tool_calls:
                    arguments, parse_error = _parse_arguments(call.arguments)
                    yield ToolCallStarted(
                        call_id=call.id,
                        name=call.name,
                        arguments=arguments,
                    )

                    if parse_error:
                        result = parse_error
                    else:
                        result = await self._tools.execute(call.name, arguments)

                    self._conversation.add_tool(call.id, result)
                    yield ToolResult(
                        call_id=call.id,
                        name=call.name,
                        result=result,
                    )

            yield AgentError(
                f"工具调用轮次超过上限（{self._max_tool_rounds} 轮），本轮已停止。"
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # 网络、鉴权、模型服务等异常统一交给前端展示
            yield AgentError(f"对话失败: {exc}")

    @staticmethod
    def _tool_call_message(call: ToolCall) -> dict[str, Any]:
        return {
            "id": call.id,
            "type": "function",
            "function": {"name": call.name, "arguments": call.arguments},
        }


def _parse_arguments(raw: str) -> tuple[dict[str, Any], str | None]:
    try:
        arguments = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        return {}, f"工具参数不是合法 JSON: {exc}"
    if not isinstance(arguments, dict):
        return {}, "工具参数必须是 JSON 对象"
    return arguments, None
