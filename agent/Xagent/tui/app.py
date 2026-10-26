"""Textual TUI 前端。

本模块只负责：接收用户输入、调用 ChatAgentProtocol、把 AgentEvent 渲染成界面。
它不直接访问 OpenAI、不执行工具，也不解析配置。
"""

from __future__ import annotations

from textual import work
from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Input

from core.contracts import ChatAgentProtocol
from core.events import (
    AgentError,
    TextDelta,
    ToolCallStarted,
    ToolResult,
    TurnCompleted,
)

from .compat import apply_windows_ime_patch
from .widgets import ChatView

INPUT_PLACEHOLDER = "输入消息，Enter 发送 · Ctrl+L 清空 · Ctrl+Q 退出"


class PromptInput(Input):
    def __init__(self) -> None:
        super().__init__(placeholder=INPUT_PLACEHOLDER, id="prompt")


class XagentApp(App[None]):
    CSS_PATH = "styles.tcss"
    TITLE = "Xagent"
    BINDINGS = [
        ("ctrl+l", "clear_chat", "清空对话"),
        ("ctrl+q", "quit", "退出"),
        ("escape", "focus_prompt", "输入框"),
    ]

    def __init__(
        self,
        agent: ChatAgentProtocol,
        *,
        model_name: str | None = None,
    ) -> None:
        apply_windows_ime_patch()
        super().__init__()
        self._agent = agent
        self._model_name = model_name or agent.model_name
        self._busy = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield ChatView(id="chat")
        yield PromptInput()
        yield Footer()

    async def on_mount(self) -> None:
        self.sub_title = f"{self._model_name} · Ctrl+L 清空 · Ctrl+Q 退出"
        await self.query_one(ChatView).add_notice(
            "Xagent 已启动。可以让我读取时间、判断闰年、联网搜索、抓取网页正文、执行本地命令或操作本地文件。"
        )
        self.query_one(PromptInput).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text or self._busy:
            return

        event.input.value = ""
        event.input.disabled = True
        event.input.placeholder = "Xagent 正在处理..."
        self._busy = True
        self._run_turn(text)

    @work(exclusive=True, group="agent")
    async def _run_turn(self, text: str) -> None:
        chat = self.query_one(ChatView)
        prompt = self.query_one(PromptInput)

        assistant = None
        buffer: list[str] = []
        tool_widgets = {}
        start_new_assistant = True

        async def ensure_assistant():
            nonlocal assistant, start_new_assistant
            if assistant is None or start_new_assistant:
                assistant = await chat.add_assistant()
                buffer.clear()
                start_new_assistant = False
            return assistant

        try:
            await chat.add_user(text)
            async for event in self._agent.stream_reply(text):
                if isinstance(event, TextDelta):
                    widget = await ensure_assistant()
                    buffer.append(event.text)
                    widget.update("".join(buffer))
                    chat.scroll_to_bottom()
                elif isinstance(event, ToolCallStarted):
                    tool_widgets[event.call_id] = await chat.add_tool(
                        event.name, event.arguments
                    )
                    assistant = None
                    start_new_assistant = True
                elif isinstance(event, ToolResult):
                    widget = tool_widgets.get(event.call_id)
                    if widget is not None:
                        widget.set_result(event.result)
                    else:
                        await chat.add_notice(f"工具 {event.name} 已返回结果")
                    chat.scroll_to_bottom()
                elif isinstance(event, TurnCompleted):
                    if event.content:
                        widget = await ensure_assistant()
                        widget.update(event.content)
                    elif assistant is not None:
                        widget = await ensure_assistant()
                        widget.update("_（本轮没有文本回答）_")
                elif isinstance(event, AgentError):
                    await chat.add_error(event.message)
        except Exception as exc:  # 防止前端异常导致界面永久卡在忙碌状态
            await chat.add_error(f"界面处理异常: {exc}")
        finally:
            self._busy = False
            prompt.disabled = False
            prompt.placeholder = INPUT_PLACEHOLDER
            prompt.focus()

    def action_focus_prompt(self) -> None:
        self.query_one(PromptInput).focus()

    async def action_clear_chat(self) -> None:
        if self._busy:
            self.notify("请等待当前回答结束", severity="warning")
            return
        self._agent.reset()
        chat = self.query_one(ChatView)
        await chat.clear_messages()
        await chat.add_notice("对话已清空。")
        self.query_one(PromptInput).focus()


def run(agent: ChatAgentProtocol, *, model_name: str | None = None) -> None:
    XagentApp(agent, model_name=model_name).run()
