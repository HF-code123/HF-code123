"""斜杠命令 / 配置化参数 / 首次运行 / MCP 边栏 / AGENTS.md 注入 测试。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from core.agent import ChatAgent
from core.config import (
    DEFAULT_CONTEXT_WINDOW,
    AppConfig,
    LLMConfig,
    ensure_cwd_config,
)
from core.events import AgentEvent, TextDelta, TurnCompleted
from core.factory import build_agent, read_agents_md
from core.llm import StreamText
from core.session_store import (
    list_sessions,
    load_session,
    load_summary,
    save_session,
    sessions_root,
)
from core.tools import ToolRegistry
from tui.sidebar import Sidebar

from test_context import FakeLLM, collect  # noqa: E402


# ---------- 1. 配置化参数 ----------


class TestConfigParams:
    def _write(self, tmp_path: Path, content: str) -> AppConfig:
        path = tmp_path / "config.ini"
        path.write_text(content, encoding="utf-8")
        return AppConfig.from_ini(path)

    def test_custom_values(self, tmp_path: Path) -> None:
        config = self._write(
            tmp_path,
            "[llm]\nbase_url = u\napi_key = k\nmodel = m\n\n"
            "[agent]\nmax_tool_rounds = 3\ncontext_window = 8192\n"
            "context_warning_ratio = 0.5\n\n[mcp]\ntimeout = 12.5\n",
        )
        assert config.agent.context_window == 8192
        assert config.agent.context_window_is_default is False
        assert config.agent.context_warning_ratio == 0.5
        assert config.mcp.timeout == 12.5

    def test_defaults_when_absent(self, tmp_path: Path) -> None:
        config = self._write(
            tmp_path, "[llm]\nbase_url = u\napi_key = k\nmodel = m\n"
        )
        assert config.agent.context_window == DEFAULT_CONTEXT_WINDOW
        assert config.agent.context_window_is_default is True
        assert config.agent.context_warning_ratio == pytest.approx(0.7)
        assert config.mcp.timeout == pytest.approx(30.0)

    def test_invalid_ratio_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="context_warning_ratio"):
            self._write(
                tmp_path,
                "[llm]\nbase_url = u\napi_key = k\nmodel = m\n\n"
                "[agent]\ncontext_warning_ratio = 1.5\n",
            )

    def test_ensure_cwd_config_creates_mock(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        assert ensure_cwd_config() is True
        path = tmp_path / "config.ini"
        assert path.is_file()
        text = path.read_text(encoding="utf-8")
        assert "请替换为你的API密钥" in text
        # 第二次调用不再覆盖
        path.write_text("modified", encoding="utf-8")
        assert ensure_cwd_config() is False
        assert path.read_text(encoding="utf-8") == "modified"


# ---------- 2. 会话持久化 ----------


class TestSessionStore:
    def test_save_list_load_roundtrip(self, tmp_path: Path) -> None:
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "帮我写一份报告"},
            {"role": "assistant", "content": "好的"},
        ]
        summary = save_session(messages, project_root=tmp_path)

        assert summary.title == "帮我写一份报告"
        assert summary.message_count == 3
        assert list_sessions(project_root=tmp_path)[0].session_id == (
            summary.session_id
        )
        loaded = load_session(summary.path)
        assert loaded == messages

    def test_title_truncated(self, tmp_path: Path) -> None:
        long_text = "很长" * 60
        summary = save_session(
            [{"role": "user", "content": long_text}], project_root=tmp_path
        )
        assert len(summary.title) <= 41
        assert summary.title.endswith("…")

    def test_empty_session_title(self, tmp_path: Path) -> None:
        summary = save_session([], project_root=tmp_path)
        assert summary.title == "（空会话）"

    def test_corrupt_meta_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.jsonl"
        path.write_text("{not json}\n", encoding="utf-8")
        with pytest.raises(OSError):
            load_summary(path)
        assert list_sessions(project_root=tmp_path) == []

    def test_list_sorted_newest_first(self, tmp_path: Path) -> None:
        save_session([{"role": "user", "content": "甲"}],
                     project_root=tmp_path, session_id="20260101-000000")
        save_session([{"role": "user", "content": "乙"}],
                     project_root=tmp_path, session_id="20260201-000000")
        ids = [s.session_id for s in list_sessions(project_root=tmp_path)]
        assert ids == ["20260201-000000", "20260101-000000"]


# ---------- 3. AGENTS.md 注入 ----------


class TestAgentsMdInjection:
    def _agent(self, tmp_path: Path, **kwargs) -> ChatAgent:
        return ChatAgent(
            FakeLLM(turns=[]), ToolRegistry(), **kwargs
        )

    def test_injected_on_init(self, tmp_path: Path) -> None:
        agent = self._agent(tmp_path, system_prompt="sys", project_context="约定内容")
        roles = [m["role"] for m in agent.messages]
        contents = [m.get("content", "") for m in agent.messages]
        assert roles == ["system", "user"]
        assert "【项目约定 AGENTS.md】" in contents[1]
        assert "约定内容" in contents[1]

    def test_reinjected_after_reset(self, tmp_path: Path) -> None:
        agent = self._agent(tmp_path, project_context="约定内容")
        agent._conversation.add_user("第一条")
        agent.reset()
        contents = [m.get("content", "") for m in agent.messages]
        # 无 system prompt 时：reset 后只剩重新注入的 AGENTS.md 消息
        assert len(contents) == 1
        assert "【项目约定 AGENTS.md】" in contents[0]

    def test_no_injection_without_context(self, tmp_path: Path) -> None:
        agent = self._agent(tmp_path, system_prompt="sys")
        assert [m["role"] for m in agent.messages] == ["system"]

    def test_read_agents_md(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        assert read_agents_md() == ""
        (tmp_path / "AGENTS.md").write_text("# 项目约定\n使用中文", encoding="utf-8")
        assert "使用中文" in read_agents_md()

    def test_factory_injects_agents_md(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "AGENTS.md").write_text("工厂注入测试", encoding="utf-8")
        config = AppConfig(
            llm=LLMConfig(base_url="u", api_key="k", model="m")
        )
        agent = build_agent(config)
        assert any(
            "工厂注入测试" in str(m.get("content", "")) for m in agent.messages
        )


# ---------- 4. replace_history / 自定义告警阈值 ----------


class TestHistoryRestore:
    def test_replace_history_keeps_system(self, tmp_path: Path) -> None:
        agent = ChatAgent(
            FakeLLM(turns=[]), ToolRegistry(), system_prompt="sys"
        )
        agent.replace_history(
            [
                {"role": "system", "content": "旧的system应被忽略"},
                {"role": "user", "content": "历史问题"},
                {"role": "assistant", "content": "历史回答"},
            ]
        )
        roles = [m["role"] for m in agent.messages]
        contents = [m.get("content") for m in agent.messages]
        assert roles == ["system", "user", "assistant"]
        assert contents[0] == "sys"
        assert contents[1] == "历史问题"

    def test_custom_warn_ratio_blocks(self, tmp_path: Path) -> None:
        # usage=600 经一次响应后写入用量（API usage 优先），窗口 1000、阈值 0.5
        agent = ChatAgent(
            FakeLLM(turns=[[StreamText("回答")]], usage=600),
            ToolRegistry(),
            context_window=1000,
            context_warning_ratio=0.5,
        )
        asyncio.run(collect(agent, "问题"))
        assert agent.context_usage().blocked is True

        agent2 = ChatAgent(
            FakeLLM(turns=[[StreamText("回答")]], usage=400),
            ToolRegistry(),
            context_window=1000,
            context_warning_ratio=0.5,
        )
        asyncio.run(collect(agent2, "问题"))
        assert agent2.context_usage().blocked is False


# ---------- 5. TUI：边栏 / 补全面板 / 会话命令 ----------


class SessionFakeAgent:
    """带消息历史的最小 FakeAgent，供会话命令测试。"""

    def __init__(self) -> None:
        self.history: list[dict] = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "初始问题"},
            {"role": "assistant", "content": "初始回答"},
        ]

    @property
    def model_name(self) -> str:
        return "fake-model"

    @property
    def messages(self) -> list[dict]:
        return list(self.history)

    def reset(self) -> None:
        self.history = self.history[:1]

    def replace_history(self, messages: list[dict]) -> None:
        self.history = [m for m in messages if m.get("role") != "system"]

    def context_usage(self):
        from core.agent import ContextUsageSnapshot

        return ContextUsageSnapshot(tokens_used=100, context_window=128000,
                                    window_is_default=True)

    async def stream_reply(self, user_text: str) -> AsyncIterator[AgentEvent]:
        self.history.append({"role": "user", "content": user_text})
        self.history.append({"role": "assistant", "content": "回复"})
        yield TextDelta("回复")
        yield TurnCompleted("回复")


def _snapshot(chat) -> list[str]:
    return [type(child).__name__ for child in chat.children]


class TestTuiNewFeatures:
    def test_sidebar_lists_skills(self) -> None:
        """右侧边栏展示已发现的技能清单（名称 + 描述）。"""

        async def run_test() -> None:
            from tui.app import XagentApp

            class SkillAgent(SessionFakeAgent):
                @property
                def skill_infos(self) -> list[tuple[str, str]]:
                    return [
                        ("healthfit", "个人全维度健康管理系统"),
                        ("health-report", "生成结构化健康摘要"),
                    ]

            app = XagentApp(SkillAgent())
            async with app.run_test(size=(110, 32)) as pilot:
                await pilot.pause()
                rendered = str(app.query_one(Sidebar).body.render())
                assert "技能 · 2" in rendered
                assert "healthfit" in rendered
                assert "个人全维度健康管理系统" in rendered
                assert "health-report" in rendered

        asyncio.run(run_test())

    def test_sidebar_renders_mcp_status(self) -> None:
        from core.mcp_client import MCPServerConfig

        async def run_test() -> None:
            from tui.app import XagentApp

            app = XagentApp(SessionFakeAgent())
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                sidebar = app.query_one(Sidebar)
                server = MCPServerConfig(name="pet", url="http://x/mcp")
                sidebar.set_mcp_status(
                    {"pet": server},
                    {"mcp__pet__list_pets": (server, "list_pets")},
                    [],
                )
                await pilot.pause()
                rendered = str(sidebar.body.render())
                assert "pet" in rendered
                assert "list_pets" in rendered
                assert "●" in rendered

        asyncio.run(run_test())

    def test_command_hint_filters_and_completes(self) -> None:
        from textual.widgets import OptionList

        from tui.app import XagentApp

        async def run_test() -> None:
            app = XagentApp(SessionFakeAgent())
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                prompt = app.query_one("#prompt")
                hint = app.query_one("#command-hint", OptionList)

                prompt.value = "/"
                await pilot.pause()
                assert not hint.has_class("hidden")
                assert hint.option_count == 8

                prompt.value = "/sa"
                await pilot.pause()
                assert hint.option_count == 1

                await pilot.press("tab")
                assert prompt.value == "/save "

                prompt.value = "/nothing"
                await pilot.pause()
                assert hint.has_class("hidden")

        asyncio.run(run_test())

    def test_command_bar_lists_every_command(self) -> None:
        """输入框下方命令栏常显全部命令药丸，且每个命令都可点击。"""

        from tui.app import XagentApp
        from tui.command_bar import CommandBar
        from tui.commands import COMMANDS

        async def run_test() -> None:
            app = XagentApp(SessionFakeAgent())
            async with app.run_test(size=(110, 32)) as pilot:
                await pilot.pause()
                bar = app.query_one(CommandBar)
                markup = bar.markup_text
                for command in COMMANDS:
                    assert f"/{command.name}" in markup
                    assert f'@click=app.fill_command("{command.name}")' in markup
                assert bar.region.width > 0 and bar.region.height > 0

        asyncio.run(run_test())

    def test_command_bar_click_fills_prompt(self) -> None:
        """点击命令栏中的命令药丸：填入输入框，回车后执行。"""

        from tui.app import XagentApp
        from tui.command_bar import CommandBar

        async def run_test() -> None:
            app = XagentApp(SessionFakeAgent())
            async with app.run_test(size=(110, 32)) as pilot:
                await pilot.pause()
                bar = app.query_one(CommandBar)
                prompt = app.query_one("#prompt")

                # 药丸在命令栏第一行（0=药丸，1=快捷键提示）
                # 上边框(1) 使内容行从 y+1 开始；左内边距(1) 使 x 偏移 +1
                line = str(bar.render()).split("\n")[0]
                col = line.index("/context") + 5  # 内边距+落在药丸内部
                await pilot.click(CommandBar, offset=(col + 1, 1))
                await pilot.pause()

                assert prompt.value == "/context "
                assert prompt.has_focus

                # 回车执行
                await pilot.press("enter")
                await pilot.pause(0.3)
                chat = app.query_one("#chat")
                assert "上下文" in str(chat.children[-1].render())

        asyncio.run(run_test())

    def test_header_click_ignored_while_busy(self) -> None:
        from tui.app import XagentApp

        async def run_test() -> None:
            app = XagentApp(SessionFakeAgent())
            async with app.run_test(size=(110, 32)) as pilot:
                await pilot.pause()
                app._busy = True
                app.action_fill_command("new")
                await pilot.pause()
                assert app.query_one("#prompt").value == ""
                app._busy = False

        asyncio.run(run_test())

    def test_help_command(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        from tui.app import XagentApp

        async def run_test() -> None:
            app = XagentApp(SessionFakeAgent())
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app.query_one("#prompt").value = "/help"
                await pilot.press("enter")
                await pilot.pause(0.3)
                chat = app.query_one("#chat")
                assert any(
                    "/resume" in str(child.render()) for child in chat.children
                )

        asyncio.run(run_test())

    def test_new_saves_and_resets(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        from tui.app import XagentApp

        async def run_test() -> None:
            agent = SessionFakeAgent()
            app = XagentApp(agent)
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app.query_one("#prompt").value = "/new"
                await pilot.press("enter")
                await pilot.pause(0.3)

                sessions = list_sessions(project_root=tmp_path)
                assert len(sessions) == 1
                assert sessions[0].title == "初始问题"
                assert load_session(sessions[0].path)[1]["content"] == "初始问题"
                # 上下文被重置（只剩 system）
                assert agent.history == [{"role": "system", "content": "sys"}]
                # AGENTS.md 不存在时，空会话不产生第二个归档
                app.query_one("#prompt").value = "/new"
                await pilot.press("enter")
                await pilot.pause(0.3)
                assert len(list_sessions(project_root=tmp_path)) == 1

        asyncio.run(run_test())

    def test_resume_restores_session(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        from tui.app import XagentApp

        async def run_test() -> None:
            saved = save_session(
                [
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "历史问题甲"},
                    {"role": "assistant", "content": "历史回答乙"},
                ],
                project_root=tmp_path,
            )
            agent = SessionFakeAgent()
            agent.history = agent.history[:1]  # 模拟已 reset 的空会话
            app = XagentApp(agent)
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app.query_one("#prompt").value = "/resume"
                await pilot.press("enter")
                await pilot.pause(0.3)

                # 面板已弹出且获得焦点，回车选择第一项
                from textual.widgets import OptionList

                option_list = app.screen.query_one("#sessions-list", OptionList)
                assert option_list.has_focus
                await pilot.press("enter")
                await pilot.pause(0.3)

                assert agent.history[0]["content"] == "历史问题甲"
                chat = app.query_one("#chat")
                rendered = " ".join(
                    str(child.render()) for child in chat.children
                )
                # AssistantMessage 现在是容器（标签+内层 Markdown），深入取正文
                inner_sources = []
                for child in chat.children:
                    for sub in getattr(child, "children", []):
                        source = getattr(sub, "source", "")
                        if source:
                            inner_sources.append(source)
                assert "历史问题甲" in rendered
                assert "历史回答乙" in rendered + " ".join(inner_sources)
                _ = saved

        asyncio.run(run_test())

    def test_resume_cancel_with_escape(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        from tui.app import XagentApp

        async def run_test() -> None:
            save_session(
                [{"role": "user", "content": "旧会话"}], project_root=tmp_path
            )
            agent = SessionFakeAgent()
            agent.history = agent.history[:1]
            app = XagentApp(agent)
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app.query_one("#prompt").value = "/resume"
                await pilot.press("enter")
                await pilot.pause(0.3)
                await pilot.press("escape")
                await pilot.pause(0.3)
                assert agent.history == [{"role": "system", "content": "sys"}]

        asyncio.run(run_test())

    def test_save_command(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        from tui.app import XagentApp

        async def run_test() -> None:
            agent = SessionFakeAgent()
            app = XagentApp(agent)
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app.query_one("#prompt").value = "/save"
                await pilot.press("enter")
                await pilot.pause(0.3)
                assert len(list_sessions(project_root=tmp_path)) == 1
                assert sessions_root(tmp_path).is_dir()

        asyncio.run(run_test())
