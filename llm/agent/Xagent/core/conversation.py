"""对话上下文模型。

Conversation 只负责维护 OpenAI Chat Completions 需要的消息列表，
不感知模型、网络或界面。这样 Agent 可以专注编排流程，前端也可以复用历史记录。
"""

from __future__ import annotations

from typing import Any

Message = dict[str, Any]
ToolCallMessage = dict[str, Any]


class Conversation:
    def __init__(self, system_prompt: str = "") -> None:
        self._system_prompt = system_prompt.strip()
        self._messages: list[Message] = []
        self._append_system_prompt()

    @property
    def system_prompt(self) -> str:
        """当前系统提示词，便于调试与测试。"""

        return self._system_prompt

    @property
    def messages(self) -> list[Message]:
        """返回消息列表的浅拷贝，避免调用方直接修改内部状态。"""

        return list(self._messages)

    def add_user(self, content: str) -> None:
        self._messages.append({"role": "user", "content": content})

    def add_assistant(
        self,
        content: str | None,
        tool_calls: list[ToolCallMessage] | None = None,
    ) -> None:
        message: Message = {"role": "assistant", "content": content}
        if tool_calls:
            message["tool_calls"] = list(tool_calls)
        self._messages.append(message)

    def add_tool(self, call_id: str, content: str) -> None:
        self._messages.append(
            {"role": "tool", "tool_call_id": call_id, "content": content}
        )

    def clear(self) -> None:
        self._messages.clear()
        self._append_system_prompt()

    def replace(self, messages: list[Message]) -> None:
        """用给定消息（不含 system）替换当前历史，保留 System Prompt。"""

        self._messages = [
            m for m in self._messages if m.get("role") == "system"
        ] + list(messages)

    def _append_system_prompt(self) -> None:
        if self._system_prompt:
            self._messages.append(
                {"role": "system", "content": self._system_prompt}
            )
