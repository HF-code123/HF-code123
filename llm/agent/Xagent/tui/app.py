"""Textual TUI 前端。

本模块只负责：接收用户输入、调用 ChatAgentProtocol、把 AgentEvent 渲染成界面。
它不直接访问 OpenAI、不执行工具，也不解析配置。

斜杠命令：/help /new /resume /save /context /compact /clear /quit，
以可点击药丸形式常显在聊天区顶部信息卡；输入 / 时弹出补全引导面板。

MCP 异步加载：进入界面后后台连接 .mcp.json 中的服务器，
完成后动态刷新右侧边栏。

界面布局：顶栏（标题+时钟）→ 左聊天区（信息卡/消息/输入框/状态行）
→ 右侧边栏（会话信息+工具列表）→ 底部快捷键行。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option

from core.autostart import load_services, start_services_async_safe
from core.contracts import ChatAgentProtocol
from core.events import (
    AgentError,
    ContextWarning,
    TextDelta,
    ToolCallStarted,
    ToolResult,
    TurnCompleted,
)
from core.session_store import (
    SessionSummary,
    load_session,
    list_sessions,
    save_session,
)

from .compact_panel import CompactResultPanel
from .compat import apply_windows_ime_patch
from .commands import (
    COMMAND_HINT_ID,
    COMMANDS,
    PromptInput,
    build_hint_options,
    match_commands,
    parse_command_name,
)
from .command_bar import CommandBar
from .sessions_panel import SessionsPanel
from .sidebar import Sidebar
from .widgets import ChatView

INPUT_PLACEHOLDER = "输入问题后 Enter 发送；/help 查看命令；Esc 中断生成"
COMPACT_COMMAND = "/compact"
PROJECT_CONTEXT_PREFIX = "【项目约定 AGENTS.md】"


def _format_k(tokens: int) -> str:
    return f"{tokens / 1000:.1f}k"


def _format_usage(usage) -> str:
    """把用量快照渲染成「12.3k/128k 10%」样式；默认窗口标注「默认」。"""

    marker = "（默认）" if getattr(usage, "window_is_default", True) else ""
    return (
        f"上下文 {_format_k(usage.tokens_used)} / "
        f"{_format_k(usage.context_window)}k{marker} · "
        f"已用 {usage.ratio:.0%}"
    )


class XagentApp(App[None]):
    CSS_PATH = "styles.tcss"
    TITLE = "Xagent"
    BINDINGS = [
        ("ctrl+l", "clear_chat", "清屏"),
        ("ctrl+q", "quit", "退出"),
        ("ctrl+n", "new_session", "新会话"),
        ("ctrl+b", "toggle_sidebar", "侧栏"),
        ("f1", "show_help", "帮助"),
        ("escape", "interrupt", "中断生成"),
    ]

    def __init__(
        self,
        agent: ChatAgentProtocol,
        *,
        model_name: str | None = None,
        base_url: str | None = None,
        mcp_servers: dict[str, Any] | None = None,
        mcp_timeout: float = 30.0,
        config_notice: str | None = None,
    ) -> None:
        apply_windows_ime_patch()
        super().__init__()
        self._agent = agent
        self._model_name = model_name or agent.model_name
        self._base_url = base_url or "-"
        self._busy = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_timeout = mcp_timeout
        self._config_notice = config_notice
        self._hint_matches: list = []
        # 状态行计数器
        self._session_id = uuid.uuid4().hex[:8]
        self._turn_count = 0
        self._tool_call_count = 0
        self._turn_started_at: float | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            with Vertical(id="content"):
                yield ChatView(id="chat")
                yield OptionList(id=COMMAND_HINT_ID, classes="hidden")
                yield PromptInput(id="prompt")
                yield CommandBar(id="command-bar")
                yield Static(id="status-line", markup=True)
            yield Sidebar(id="sidebar")
        yield Footer()

    async def on_mount(self) -> None:
        self._refresh_sidebar()
        self._update_status()
        chat = self.query_one(ChatView)
        await chat.add_notice(
            "Xagent 已启动。可以让我读取时间、联网搜索、抓取网页、执行命令、"
            "操作文件或加载技能；斜杠命令见输入框下方命令栏（可点击填入）。"
        )
        if self._config_notice:
            await chat.add_notice(self._config_notice)
        self.query_one(PromptInput).focus()
        self._load_mcp_async()

    # ---------- 界面刷新 ----------

    def _tool_infos(self) -> list[tuple[str, str]]:
        try:
            return list(self._agent.tools.infos())
        except Exception:
            return []

    def _skill_infos(self) -> list[tuple[str, str]]:
        try:
            return list(self._agent.skill_infos)
        except Exception:
            return []

    def _max_rounds(self) -> str:
        rounds = getattr(self._agent, "_max_tool_rounds", None)
        return f"{rounds} 轮模型调用/轮" if rounds else "-"

    def _refresh_sidebar(self) -> None:
        sidebar = self.query_one(Sidebar)
        messages = getattr(self._agent, "messages", [])
        sidebar.set_session_info(
            model=self._model_name,
            base_url=self._base_url,
            cwd=str(Path.cwd()),
            session_id=self._session_id,
            turns=str(self._turn_count),
            messages=str(max(len(messages) - 1, 0)),
            max_rounds=self._max_rounds(),
            status="运行中" if self._busy else "就绪",
        )
        sidebar.set_skills(self._skill_infos())
        sidebar.set_tools(self._tool_infos())

    def _update_status(self) -> None:
        """刷新输入框下方的状态行。"""

        line = self.query_one("#status-line", Static)
        if self._busy and self._turn_started_at is not None:
            elapsed = time.monotonic() - self._turn_started_at
            line.update(
                f"[#e0af68]●[/] 运行中 · {self._turn_count} 轮 ｜ "
                f"{self._model_name} ｜ 工具 {self._tool_call_count} 次 ｜ "
                f"{elapsed:.1f}s ｜ Esc 中断"
            )
        else:
            line.update(
                f"[#9ece6a]●[/] 就绪 · {self._turn_count} 轮 ｜ "
                f"{self._model_name} ｜ 工具 {self._tool_call_count} 次"
            )

    # ---------- MCP 异步加载 ----------

    @work(exclusive=True, group="mcp")
    async def _load_mcp_async(self) -> None:
        """后台连接 MCP 服务器，完成后动态刷新边栏；不阻塞界面。"""

        from core.mcp_client import register_mcp_tools

        if not self._mcp_servers:
            self.query_one(Sidebar).set_mcp_status({}, {}, [])
            return

        # 先按 .mcp.json 的 autoStart 拉起本地依赖服务（端口已在监听则跳过），
        # 放到线程里做，避免等待端口就绪时阻塞界面
        if load_services():
            try:
                await asyncio.to_thread(
                    start_services_async_safe, Path.cwd() / ".agents" / "logs"
                )
            except Exception:
                pass  # 自动启动失败不应影响 MCP 连接尝试

        try:
            manager = await register_mcp_tools(
                self._agent.tools, timeout=self._mcp_timeout
            )
        except Exception as exc:  # 整体失败也只影响边栏显示
            self.query_one(Sidebar).set_mcp_status(
                self._mcp_servers, {}, [f"连接失败: {exc}"]
            )
            await self.query_one(ChatView).add_error(f"MCP 加载失败: {exc}")
            return
        sidebar = self.query_one(Sidebar)
        sidebar.set_mcp_status(
            self._mcp_servers,
            manager.mapping if manager else {},
            manager.warnings if manager else [],
        )
        self._refresh_sidebar()
        chat = self.query_one(ChatView)
        if manager and manager.mapping:
            await chat.add_notice(
                f"✅ 已连接 {len(manager.mapping)} 个 MCP 工具"
                f"（{len(self._mcp_servers)} 个服务器）"
            )
        for warning in manager.warnings if manager else []:
            await chat.add_notice(f"[mcp] {warning}")

    # ---------- 上下文用量显示 ----------

    def _refresh_context_status(self) -> None:
        """在副标题栏醒目展示当前上下文用量。"""

        usage = getattr(self._agent, "context_usage", None)
        if usage is None:
            return
        try:
            status = _format_usage(usage())
        except Exception:
            return
        self.sub_title = f"{self._model_name} · {status}"

    # ---------- 命令补全面板 ----------

    def on_input_changed(self, event: Input.Changed) -> None:
        self._update_command_hint(event.value)

    def _update_command_hint(self, value: str) -> None:
        hint = self.query_one(f"#{COMMAND_HINT_ID}", OptionList)
        if not value.startswith("/") or self._busy:
            hint.add_class("hidden")
            self._hint_matches = []
            return
        matches = match_commands(value[1:])
        self._hint_matches = matches
        hint.clear_options()
        for option in build_hint_options(matches):
            hint.add_option(option)
        if matches:
            hint.highlighted = 0
            hint.remove_class("hidden")
        else:
            hint.add_class("hidden")

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        """点击补全面板中的命令：填入输入框（不自动执行，便于补参数）。"""

        if event.option_id is None:
            return
        prompt = self.query_one(PromptInput)
        prompt.value = f"/{event.option_id} "
        prompt.cursor_position = len(prompt.value)
        self._hide_command_hint()
        prompt.focus()

    def _hide_command_hint(self) -> None:
        hint = self.query_one(f"#{COMMAND_HINT_ID}", OptionList)
        hint.add_class("hidden")
        self._hint_matches = []

    # ---------- 输入与普通对话轮 ----------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        self._hide_command_hint()
        if not text or self._busy:
            return

        event.input.value = ""
        if text.startswith("/"):
            self._dispatch_command(text)
            return

        event.input.disabled = True
        event.input.placeholder = "Xagent 正在处理..."
        self._busy = True
        self._turn_started_at = time.monotonic()
        self._update_status()
        self._refresh_sidebar()
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
                    self._tool_call_count += 1
                    self._update_status()
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
                elif isinstance(event, ContextWarning):
                    await chat.add_error(
                        f"⚠ 上下文用量已达 {event.ratio:.0%}"
                        f"（{event.tokens_used / 1000:.1f}k / "
                        f"{event.context_window / 1000:.0f}k），"
                        "已暂停接受新任务。请输入 /compact 压缩上下文。"
                    )
                elif isinstance(event, AgentError):
                    await chat.add_error(event.message)
        except asyncio.CancelledError:
            await chat.add_notice("⏹ 已中断本轮生成。")
            raise
        except Exception as exc:  # 防止前端异常导致界面永久卡在忙碌状态
            await chat.add_error(f"界面处理异常: {exc}")
        finally:
            self._turn_count += 1
            self._busy = False
            self._turn_started_at = None
            prompt.disabled = False
            prompt.placeholder = INPUT_PLACEHOLDER
            prompt.focus()
            self._update_status()
            self._refresh_sidebar()
            self._refresh_context_status()

    # ---------- 斜杠命令 ----------

    def action_fill_command(self, name: str) -> None:
        """命令栏的命令药丸被点击：把命令填入输入框。"""

        if self._busy:
            self.notify("请等待当前回答结束", severity="warning")
            return
        prompt = self.query_one(PromptInput)
        prompt.value = f"/{name} "
        prompt.cursor_position = len(prompt.value)
        prompt.focus()

    def _dispatch_command(self, text: str) -> None:
        self._execute_command(parse_command_name(text))

    def _execute_command(self, name: str) -> None:
        if name == "compact":
            self._run_compaction()
        elif name == "help":
            self.action_show_help()
        elif name == "new":
            self.run_worker(self._command_new())
        elif name == "resume":
            self.run_worker(self._command_resume())
        elif name == "save":
            self.run_worker(self._command_save())
        elif name == "context":
            usage = self._agent.context_usage()
            self.run_worker(
                self._notice(
                    f"当前 {_format_usage(usage)}\n"
                    "输入 /compact 可压缩上下文。"
                )
            )
        elif name == "clear":
            self.run_worker(self._command_clear())
        elif name == "quit":
            self.exit()
        else:
            self.run_worker(
                self._notice(f"未知命令 /{name}，输入 / 查看可用命令。")
            )

    async def _notice(self, message: str) -> None:
        await self.query_one(ChatView).add_notice(message)

    async def _command_new(self) -> None:
        """/new：自动保存当前会话历史，然后开启新会话。"""

        chat = self.query_one(ChatView)
        messages = self._agent.messages
        if self._has_real_history(messages):
            try:
                summary = save_session(messages)
                await chat.add_notice(
                    f"当前会话已保存：{summary.path}\n标题：{summary.title}"
                )
            except OSError as exc:
                await chat.add_error(f"会话保存失败：{exc}")
        self._agent.reset()
        self._session_id = uuid.uuid4().hex[:8]
        self._turn_count = 0
        self._tool_call_count = 0
        await chat.clear_messages()
        await chat.add_notice("已开启新会话（项目约定 AGENTS.md 已重新注入）。")
        self._refresh_sidebar()
        self._update_status()
        self._refresh_context_status()

    def _has_real_history(self, messages: list[dict]) -> bool:
        """除 system 与 AGENTS.md 注入消息外，是否还有真实对话内容。"""

        for message in messages:
            if message.get("role") == "system":
                continue
            if (
                message.get("role") == "user"
                and str(message.get("content", "")).startswith(
                    PROJECT_CONTEXT_PREFIX
                )
            ):
                continue
            return True
        return False

    async def _command_save(self) -> None:
        try:
            summary = save_session(self._agent.messages)
            await self.query_one(ChatView).add_notice(
                f"会话已保存：{summary.path}\n标题：{summary.title}"
                f"（{summary.message_count} 条消息）"
            )
        except OSError as exc:
            await self.query_one(ChatView).add_error(f"会话保存失败：{exc}")

    async def _command_resume(self) -> None:
        """/resume：弹出历史会话列表，回车恢复、Esc 取消。"""

        sessions = list_sessions()
        if not sessions:
            await self.query_one(ChatView).add_notice(
                "还没有历史会话（.agents/sessions/ 为空），"
                "用 /save 或 /new 保存当前会话后再试。"
            )
            return
        selected = await self.push_screen_wait(SessionsPanel(sessions))
        if selected is None:
            await self.query_one(ChatView).add_notice("已取消恢复会话。")
            return
        messages = load_session(selected.path)
        self._agent.replace_history(messages)
        self._session_id = selected.session_id[:8]
        chat = self.query_one(ChatView)
        await chat.clear_messages()
        await self._render_history(chat, messages)
        await chat.add_notice(
            f"已恢复会话 {selected.session_id}：{selected.title}"
        )
        self._refresh_sidebar()
        self._refresh_context_status()
        self.query_one(PromptInput).focus()

    async def _render_history(self, chat: ChatView, messages: list[dict]) -> None:
        """把恢复的消息重新渲染到聊天区域（含工具调用与结果）。"""

        tool_meta: dict[str, tuple[str, dict]] = {}
        for message in messages:
            for call in message.get("tool_calls") or []:
                function = call.get("function", {})
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                tool_meta[call.get("id", "")] = (
                    function.get("name", "?"),
                    arguments,
                )

        for message in messages:
            role = message.get("role")
            if role == "system":
                continue
            if role == "user":
                content = str(message.get("content") or "")
                if content.startswith(PROJECT_CONTEXT_PREFIX):
                    await chat.add_notice("（已注入 AGENTS.md 项目约定）")
                else:
                    await chat.add_user(content)
            elif role == "assistant":
                content = message.get("content")
                widget = await chat.add_assistant()
                widget.update(content or "_（工具调用轮次）_")
            elif role == "tool":
                name, arguments = tool_meta.get(
                    message.get("tool_call_id", ""), ("未知工具", {})
                )
                widget = await chat.add_tool(name, arguments)
                widget.set_result(str(message.get("content") or ""))

    async def _command_clear(self) -> None:
        chat = self.query_one(ChatView)
        await chat.clear_messages()
        await chat.add_notice("对话显示已清空（上下文保留，/new 才会重置会话）。")

    # ---------- /compact ----------

    @work(exclusive=True, group="agent")
    async def _run_compaction(self) -> None:
        """执行 /compact：压缩期间不允许其他输入。"""

        chat = self.query_one(ChatView)
        prompt = self.query_one(PromptInput)
        self._busy = True
        self._turn_started_at = time.monotonic()
        prompt.disabled = True
        prompt.placeholder = "正在压缩上下文，请稍候..."
        self._update_status()
        self._refresh_sidebar()
        try:
            await chat.add_notice("开始压缩上下文：先归档原始对话，再生成摘要...")
            result = await self._agent.compact()
            await chat.add_notice(
                "✅ 上下文压缩完成。\n"
                f"归档编号: {result.archive_id}（{result.archive_path}）\n"
                f"压缩前 {_format_k(result.tokens_before)} → "
                f"压缩后 {_format_k(result.tokens_after)}。\n"
                "已解除输入限制，可以继续对话；"
                "历史细节可用 recallContext 工具检索。"
            )
            self.push_screen(CompactResultPanel(result))
        except Exception as exc:
            await chat.add_error(
                f"❌ {exc}\n原有上下文已保留，可重新输入 /compact 重试。"
            )
        finally:
            self._turn_count += 1
            self._busy = False
            self._turn_started_at = None
            prompt.disabled = False
            prompt.placeholder = INPUT_PLACEHOLDER
            prompt.focus()
            self._update_status()
            self._refresh_sidebar()
            self._refresh_context_status()

    # ---------- 动作 ----------

    def action_focus_prompt(self) -> None:
        self.query_one(PromptInput).focus()

    async def action_clear_chat(self) -> None:
        if self._busy:
            self.notify("请等待当前回答结束", severity="warning")
            return
        self._agent.reset()
        self._turn_count = 0
        self._tool_call_count = 0
        chat = self.query_one(ChatView)
        await chat.clear_messages()
        await chat.add_notice("对话已清空。")
        self._update_status()
        self._refresh_sidebar()
        self._refresh_context_status()
        self.query_one(PromptInput).focus()

    def action_toggle_sidebar(self) -> None:
        sidebar = self.query_one(Sidebar)
        sidebar.display = not sidebar.display

    def action_show_help(self) -> None:
        listing = "\n".join(f"/{c.name}  —  {c.description}" for c in COMMANDS)
        self.run_worker(
            self._notice(
                f"可用命令：\n{listing}\n\n"
                "提示：输入框下方命令栏中的命令可以直接点击填入。"
            )
        )

    def action_new_session(self) -> None:
        if self._busy:
            self.notify("请等待当前回答结束", severity="warning")
            return
        self.run_worker(self._command_new())

    def action_interrupt(self) -> None:
        """Esc：忙碌时中断当前生成，空闲时回到输入框。"""

        if not self._busy:
            self.action_focus_prompt()
            return
        try:
            self.workers.cancel_group(self, "agent")
        except Exception:
            pass


def run(
    agent: ChatAgentProtocol,
    *,
    model_name: str | None = None,
    base_url: str | None = None,
    mcp_servers: dict[str, Any] | None = None,
    mcp_timeout: float = 30.0,
    config_notice: str | None = None,
) -> None:
    XagentApp(
        agent,
        model_name=model_name,
        base_url=base_url,
        mcp_servers=mcp_servers,
        mcp_timeout=mcp_timeout,
        config_notice=config_notice,
    ).run()
