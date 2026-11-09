"""Skill 动态加载机制测试：发现、注册、元数据解析与按需加载正文。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.factory import build_agent
from core.skills import (
    SkillRegistry,
    discover_skills,
    parse_frontmatter,
    skills_root,
)
from core.tools import ToolRegistry
from core.tools.skill_tools import TOOL_NAME, register as register_skill_tool


def run_tool(registry: ToolRegistry, name: str, arguments: dict) -> str:
    """同步执行注册表中的工具（测试辅助）。"""

    return asyncio.run(registry.execute(name, arguments))


def make_skill(
    root: Path,
    name: str,
    *,
    skill_name: str = "",
    description: str = "一个用于测试的技能",
    body: str = "这是技能正文。",
    frontmatter: str | None = None,
    write_skill_file: bool = True,
) -> Path:
    """在临时目录中构造一个 Skill 子目录，返回目录路径。"""

    directory = root / ".agents" / "skills" / name
    directory.mkdir(parents=True)
    if write_skill_file:
        if frontmatter is None:
            frontmatter = (
                f"---\nname: {skill_name or name}\n"
                f"description: {description}\n---\n"
            )
        (directory / "SKILL.md").write_text(
            frontmatter + body, encoding="utf-8"
        )
    return directory


class TestParseFrontmatter:
    def test_basic_fields(self) -> None:
        fields, body = parse_frontmatter(
            "---\nname: demo\ndescription: 演示技能\n---\n正文内容\n"
        )
        assert fields["name"] == "demo"
        assert fields["description"] == "演示技能"
        assert body == "正文内容"

    def test_quoted_values(self) -> None:
        fields, _ = parse_frontmatter(
            '---\nname: "demo"\ndescription: \'含: 冒号的描述\'\n---\n'
        )
        assert fields["name"] == "demo"
        assert fields["description"] == "含: 冒号的描述"

    def test_no_frontmatter(self) -> None:
        fields, body = parse_frontmatter("普通 Markdown\n内容\n")
        assert fields == {}
        assert "普通 Markdown" in body

    def test_unclosed_frontmatter(self) -> None:
        fields, body = parse_frontmatter("---\nname: demo\n没有结束标记\n")
        assert fields == {}
        assert "没有结束标记" in body


class TestDiscovery:
    def test_skills_root_uses_cwd_by_default(self) -> None:
        assert skills_root() == Path.cwd() / ".agents" / "skills"
        assert skills_root(Path("/tmp/proj")) == Path("/tmp/proj/.agents/skills")

    def test_missing_skills_dir_is_empty(self, tmp_path: Path) -> None:
        registry = discover_skills(tmp_path)
        assert registry.skills == ()
        assert registry.errors == []

    def test_discovers_metadata_only(self, tmp_path: Path) -> None:
        make_skill(tmp_path, "greeter", description="打招呼技能")
        registry = SkillRegistry(tmp_path)
        registry.discover()

        assert registry.names == ("greeter",)
        skill = registry.get("greeter")
        assert skill is not None
        assert skill.description == "打招呼技能"
        assert skill.skill_file.name == "SKILL.md"
        assert registry.errors == []

    def test_skips_directory_without_skill_md(self, tmp_path: Path) -> None:
        make_skill(tmp_path, "good")
        (tmp_path / ".agents" / "skills" / "empty").mkdir()

        registry = SkillRegistry(tmp_path)
        registry.discover()

        assert registry.names == ("good",)
        assert any("empty" in message for message in registry.errors)

    def test_skips_missing_required_fields(self, tmp_path: Path) -> None:
        make_skill(tmp_path, "good")
        make_skill(
            tmp_path,
            "no-name",
            frontmatter="---\ndescription: 只有描述\n---\n",
        )
        make_skill(
            tmp_path,
            "no-desc",
            frontmatter="---\nname: no-desc\n---\n",
        )

        registry = SkillRegistry(tmp_path)
        registry.discover()

        assert registry.names == ("good",)
        assert len(registry.errors) == 2

    def test_ignores_plain_files(self, tmp_path: Path) -> None:
        make_skill(tmp_path, "good")
        (tmp_path / ".agents" / "skills" / "README.md").write_text("说明")

        registry = SkillRegistry(tmp_path)
        registry.discover()

        assert registry.names == ("good",)
        assert registry.errors == []

    def test_duplicate_name_keeps_first(self, tmp_path: Path) -> None:
        make_skill(tmp_path, "first", skill_name="dup", description="第一个")
        make_skill(tmp_path, "second", skill_name="dup", description="第二个")

        registry = SkillRegistry(tmp_path)
        registry.discover()

        assert registry.names == ("dup",)
        assert registry.get("dup") is not None
        assert registry.get("dup").description == "第一个"
        assert registry.warnings


class TestNestedDiscovery:
    """从技能市场下载/解压的技能包常多包一层目录，也要能被发现。"""

    def test_wrapped_package_is_discovered(self, tmp_path: Path) -> None:
        wrapper = tmp_path / ".agents" / "skills" / "demo_#SULP#_abc123"
        skill_dir = wrapper / "demo"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: demo\ndescription: 包了一层目录\n---\n正文\n",
            encoding="utf-8",
        )

        registry = discover_skills(tmp_path)

        assert registry.names == ("demo",)
        assert registry.get("demo").directory == skill_dir
        assert registry.errors == []

    def test_does_not_descend_into_a_skill(self, tmp_path: Path) -> None:
        # 技能自带的示例目录里即使有 SKILL.md，也不再当作独立技能注册
        directory = make_skill(tmp_path, "demo")
        example = directory / "examples" / "inner"
        example.mkdir(parents=True)
        (example / "SKILL.md").write_text(
            "---\nname: inner\ndescription: 示例技能\n---\n", encoding="utf-8"
        )

        registry = discover_skills(tmp_path)

        assert registry.names == ("demo",)

    def test_depth_limit_skips_too_deep(self, tmp_path: Path) -> None:
        deep = tmp_path / ".agents" / "skills"
        for part in ("a", "b", "c", "d", "e"):
            deep = deep / part
        deep.mkdir(parents=True)
        (deep / "SKILL.md").write_text(
            "---\nname: too-deep\ndescription: 太深\n---\n", encoding="utf-8"
        )

        registry = discover_skills(tmp_path)

        assert registry.names == ()
        assert any("a" in message for message in registry.errors)

    def test_skips_node_modules(self, tmp_path: Path) -> None:
        package = tmp_path / ".agents" / "skills" / "pkg" / "node_modules" / "dep"
        package.mkdir(parents=True)
        (package / "SKILL.md").write_text(
            "---\nname: dep\ndescription: 依赖里的\n---\n", encoding="utf-8"
        )

        registry = discover_skills(tmp_path)

        assert registry.names == ()


class TestLazyLoading:
    def test_body_not_read_at_discovery(self, tmp_path: Path) -> None:
        make_skill(tmp_path, "demo", body="正文内容")
        registry = SkillRegistry(tmp_path)
        registry.discover()

        # 注册阶段只保留元数据；正文只能通过 load_body 拿到
        assert registry.get("demo") is not None
        assert registry.load_body("demo") == "正文内容"

    def test_load_full(self, tmp_path: Path) -> None:
        make_skill(tmp_path, "demo", body="完整说明")
        registry = discover_skills(tmp_path)

        skill = registry.load_full("demo")
        assert skill.metadata.name == "demo"
        assert skill.body == "完整说明"

    def test_load_unknown_body_raises(self, tmp_path: Path) -> None:
        registry = discover_skills(tmp_path)
        with pytest.raises(KeyError):
            registry.load_body("ghost")

    def test_prompt_section_empty_without_skills(self, tmp_path: Path) -> None:
        registry = discover_skills(tmp_path)
        assert registry.render_prompt_section() == ""

    def test_prompt_section_lists_skills(self, tmp_path: Path) -> None:
        make_skill(tmp_path, "demo", description="演示技能")
        registry = discover_skills(tmp_path)

        section = registry.render_prompt_section()
        assert "- demo: 演示技能" in section
        assert "loadSkill" in section
        # 明确「技能不是工具」，并给出具体的调用示例，防止小模型把技能名当工具名
        assert "NOT a tool" in section
        assert '{"name": "demo"}' in section


class TestLoadSkillTool:
    def test_always_registered_even_without_skills(self, tmp_path: Path) -> None:
        # loadSkill 始终注册：配合 createSkill 形成创建→加载闭环
        registry = ToolRegistry()
        register_skill_tool(registry, discover_skills(tmp_path))
        assert TOOL_NAME in registry.names

    def test_registered_with_skills(self, tmp_path: Path) -> None:
        make_skill(tmp_path, "demo", body="技能正文")
        registry = ToolRegistry()
        register_skill_tool(registry, discover_skills(tmp_path))
        assert TOOL_NAME in registry.names

    def test_loads_body(self, tmp_path: Path) -> None:
        make_skill(tmp_path, "demo", body="技能正文")
        registry = ToolRegistry()
        register_skill_tool(registry, discover_skills(tmp_path))

        result = run_tool(registry, TOOL_NAME, {"name": "demo"})
        assert "技能正文" in result

    def test_result_carries_skill_dir(self, tmp_path: Path) -> None:
        """正文前给出技能绝对路径，技能里的 <SKILL_ROOT> 才有处可替换。"""

        directory = make_skill(tmp_path, "demo", body="见 <SKILL_ROOT>/assets/a.html")
        registry = ToolRegistry()
        register_skill_tool(registry, discover_skills(tmp_path))

        result = run_tool(registry, TOOL_NAME, {"name": "demo"})
        assert "[skill_dir]" in result
        assert str(directory) in result
        assert result.rstrip().endswith("见 <SKILL_ROOT>/assets/a.html")

    def test_filename_load_has_no_header(self, tmp_path: Path) -> None:
        directory = make_skill(tmp_path, "demo")
        (directory / "ref.md").write_text("附加内容", encoding="utf-8")
        registry = ToolRegistry()
        register_skill_tool(registry, discover_skills(tmp_path))

        result = run_tool(registry, TOOL_NAME, {"name": "demo", "filename": "ref.md"})
        assert result == "附加内容"

    def test_unknown_name_reports_available(self, tmp_path: Path) -> None:
        make_skill(tmp_path, "demo", description="演示技能")
        registry = ToolRegistry()
        register_skill_tool(registry, discover_skills(tmp_path))

        result = run_tool(registry, TOOL_NAME, {"name": "ghost"})
        assert "未知技能" in result
        assert "demo" in result

    def test_skill_name_called_as_tool_gets_corrective_hint(
        self, tmp_path: Path
    ) -> None:
        """模型把技能名当工具名调用时（含拼错的变体），结果直接教它改用 loadSkill。"""

        make_skill(tmp_path, "guizang-ppt-skill", description="网页 PPT 技能")
        registry = ToolRegistry()
        register_skill_tool(registry, discover_skills(tmp_path))

        result = run_tool(
            registry, "guizang-ppt-skill-ppt-skill", {"style": "x", "pages": "10"}
        )
        assert "未知工具" in result
        assert "不是工具" in result
        assert 'loadSkill' in result
        assert '{"name": "guizang-ppt-skill"}' in result
        assert "可用工具" in result

    def test_unknown_tool_without_skill_match_lists_tools(self, tmp_path: Path) -> None:
        make_skill(tmp_path, "demo")
        registry = ToolRegistry()
        register_skill_tool(registry, discover_skills(tmp_path))

        result = run_tool(registry, "nope", {})
        assert "未知工具" in result
        assert "loadSkill" in result  # 可用工具列表里能找到正确工具名

    def test_empty_name_rejected(self, tmp_path: Path) -> None:
        make_skill(tmp_path, "demo")
        registry = ToolRegistry()
        register_skill_tool(registry, discover_skills(tmp_path))

        result = run_tool(registry, TOOL_NAME, {})
        assert "不能为空" in result


class TestFactoryIntegration:
    def test_build_agent_injects_skills_and_tool(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        make_skill(tmp_path, "demo", description="演示技能")
        monkeypatch.chdir(tmp_path)

        from core.config import AppConfig, LLMConfig

        config = AppConfig(
            llm=LLMConfig(base_url="http://localhost", api_key="k", model="m")
        )
        agent = build_agent(config, registry=ToolRegistry())

        prompt = agent._conversation.system_prompt
        assert "# 可用技能" in prompt
        assert "- demo: 演示技能" in prompt

    def test_build_agent_without_skills_keeps_prompt_clean(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)

        from core.config import AppConfig, LLMConfig

        config = AppConfig(
            llm=LLMConfig(base_url="http://localhost", api_key="k", model="m")
        )
        agent = build_agent(config, registry=ToolRegistry())

        assert "# 可用技能" not in agent._conversation.system_prompt


class TestLoadAdditionalFiles:
    def make_skill_with_ref(self, tmp_path: Path) -> Path:
        directory = make_skill(tmp_path, "demo", body="见 references/api.md")
        ref_dir = directory / "references"
        ref_dir.mkdir()
        (ref_dir / "api.md").write_text("API 附加说明", encoding="utf-8")
        return directory

    def test_load_file_relative_to_skill_dir(self, tmp_path: Path) -> None:
        self.make_skill_with_ref(tmp_path)
        registry = discover_skills(tmp_path)
        assert registry.load_file("demo", "references/api.md") == "API 附加说明"

    def test_load_file_project_relative(self, tmp_path: Path) -> None:
        self.make_skill_with_ref(tmp_path)
        registry = discover_skills(tmp_path)
        project_rel = ".agents/skills/demo/references/api.md"
        assert registry.load_file("demo", project_rel) == "API 附加说明"

    def test_load_file_absolute_path_inside_skill_dir(self, tmp_path: Path) -> None:
        directory = self.make_skill_with_ref(tmp_path)
        registry = discover_skills(tmp_path)
        absolute = directory / "references" / "api.md"
        assert registry.load_file("demo", str(absolute)) == "API 附加说明"

    def test_load_file_absolute_path_outside_rejected(self, tmp_path: Path) -> None:
        self.make_skill_with_ref(tmp_path)
        outside = tmp_path / "secret.txt"
        outside.write_text("机密")
        registry = discover_skills(tmp_path)
        with pytest.raises(ValueError):
            registry.load_file("demo", str(outside))

    def test_load_file_bare_name_unique_match(self, tmp_path: Path) -> None:
        self.make_skill_with_ref(tmp_path)
        registry = discover_skills(tmp_path)
        assert registry.load_file("demo", "api.md") == "API 附加说明"

    def test_load_file_bare_name_ambiguous(self, tmp_path: Path) -> None:
        directory = self.make_skill_with_ref(tmp_path)
        (directory / "references" / "v2").mkdir()
        (directory / "references" / "v2" / "api.md").write_text("另一份")
        registry = discover_skills(tmp_path)
        with pytest.raises(ValueError, match="多个文件"):
            registry.load_file("demo", "api.md")

    def test_load_file_bare_name_no_match(self, tmp_path: Path) -> None:
        self.make_skill_with_ref(tmp_path)
        registry = discover_skills(tmp_path)
        with pytest.raises(FileNotFoundError):
            registry.load_file("demo", "ghost.md")

    def test_load_file_missing_relative_path(self, tmp_path: Path) -> None:
        make_skill(tmp_path, "demo")
        registry = discover_skills(tmp_path)
        with pytest.raises(FileNotFoundError):
            registry.load_file("demo", "references/api.md")

    def test_load_file_rejects_traversal(self, tmp_path: Path) -> None:
        make_skill(tmp_path, "demo")
        (tmp_path / "secret.txt").write_text("机密")
        registry = discover_skills(tmp_path)
        for bad in ("../secret.txt", "..\\secret.txt", "a/../../secret.txt", ""):
            with pytest.raises(ValueError):
                registry.load_file("demo", bad)

    def test_load_file_unknown_skill(self, tmp_path: Path) -> None:
        registry = discover_skills(tmp_path)
        with pytest.raises(KeyError):
            registry.load_file("ghost", "a.md")

    def test_load_file_does_not_return_skill_md(self, tmp_path: Path) -> None:
        # 提供 filename 时只读附加文件，不重复返回 SKILL.md
        self.make_skill_with_ref(tmp_path)
        registry = discover_skills(tmp_path)
        result = registry.load_file("demo", "api.md")
        assert "API 附加说明" in result
        assert "见 references" not in result

    def test_tool_loads_file(self, tmp_path: Path) -> None:
        self.make_skill_with_ref(tmp_path)
        registry = ToolRegistry()
        register_skill_tool(registry, discover_skills(tmp_path))
        result = run_tool(registry, TOOL_NAME, {"name": "demo", "filename": "references/api.md"})
        assert "API 附加说明" in result

    def test_tool_bare_name_fallback(self, tmp_path: Path) -> None:
        self.make_skill_with_ref(tmp_path)
        registry = ToolRegistry()
        register_skill_tool(registry, discover_skills(tmp_path))
        result = run_tool(registry, TOOL_NAME, {"name": "demo", "filename": "api.md"})
        assert "API 附加说明" in result

    def test_tool_rejects_outside_file(self, tmp_path: Path) -> None:
        self.make_skill_with_ref(tmp_path)
        outside = tmp_path / "secret.txt"
        outside.write_text("机密")
        registry = ToolRegistry()
        register_skill_tool(registry, discover_skills(tmp_path))
        result = run_tool(registry, TOOL_NAME, {"name": "demo", "filename": str(outside)})
        assert "失败" in result


class TestContextIsolation:
    """SKILL 正文只在 LLM 调用 loadSkill 后进入对话上下文，
    且 Agent 既有工作流程（事件序列、消息结构）保持不变。"""

    def make_skill_agent(self, tmp_path: Path, turns: list) -> tuple:
        from core.agent import ChatAgent
        from core.llm import StreamItem
        from test_agent import FakeLLM

        make_skill(tmp_path, "demo", body="SKILL正文标记XYZ")
        skills = discover_skills(tmp_path)
        registry = ToolRegistry()
        from core.tools.registry import ToolSpec

        registry.register(
            ToolSpec(
                name="echo",
                description="回显文本",
                parameters={"type": "object", "properties": {}},
                handler=lambda arguments: "echo:ok",
            )
        )
        register_skill_tool(registry, skills)
        agent = ChatAgent(
            FakeLLM(turns),
            registry,
            system_prompt=skills.render_prompt_section(),
        )
        return agent, skills

    async def _collect(self, agent, text: str) -> list:
        return [event async for event in agent.stream_reply(text)]

    def test_system_prompt_contains_metadata_only(self, tmp_path: Path) -> None:
        agent, skills = self.make_skill_agent(tmp_path, [])
        prompt = agent.messages[0]["content"]

        assert "- demo: 一个用于测试的技能" in prompt
        assert "SKILL正文标记XYZ" not in prompt

    def test_body_enters_context_only_via_loadskill(
        self, tmp_path: Path
    ) -> None:
        from core.llm import StreamText, StreamToolCalls
        from core.llm import ToolCall

        turns = [
            [StreamToolCalls((ToolCall("call-1", "loadSkill", '{"name": "demo"}'),))],
            [StreamText("已按技能说明执行")],
        ]
        agent, _ = self.make_skill_agent(tmp_path, turns)

        events = asyncio.run(self._collect(agent, "用 demo 技能"))

        result = next(e for e in events if type(e).__name__ == "ToolResult")
        assert "SKILL正文标记XYZ" in result.result

        roles = [m["role"] for m in agent.messages]
        assert roles == ["system", "user", "assistant", "tool", "assistant"]
        # 正文只出现在 tool 消息里，system 与 assistant 消息中都没有
        system_msg = agent.messages[0]["content"]
        assert "SKILL正文标记XYZ" not in system_msg

    def test_loaded_skill_can_drive_existing_tools(
        self, tmp_path: Path
    ) -> None:
        """加载技能后，LLM 按说明继续调用已有工具（以 echo 模拟 runCli/fileOps），
        事件序列与普通多轮工具调用完全一致。"""

        from core.llm import StreamText, StreamToolCalls
        from core.llm import ToolCall

        turns = [
            [StreamToolCalls((ToolCall("c1", "loadSkill", '{"name": "demo"}'),))],
            [StreamToolCalls((ToolCall("c2", "echo", "{}"),))],
            [StreamText("协同完成")],
        ]
        agent, _ = self.make_skill_agent(tmp_path, turns)

        events = asyncio.run(self._collect(agent, "干活"))

        assert [type(e).__name__ for e in events] == [
            "ToolCallStarted",
            "ToolResult",
            "ToolCallStarted",
            "ToolResult",
            "TextDelta",
            "TurnCompleted",
        ]
        assert [e.name for e in events if type(e).__name__ == "ToolCallStarted"] == [
            "loadSkill",
            "echo",
        ]


class TestLooseFrontmatter:
    """兼容中文书写习惯：冒号后无空格、全角冒号、带引号版本号。"""

    def test_no_space_after_colon(self) -> None:
        fields, _ = parse_frontmatter(
            '---\nversion:"1.0.0"\nname:校内通知\ndescription:"起草通知"\n---\n正文\n'
        )
        assert fields["name"] == "校内通知"
        assert fields["description"] == "起草通知"
        assert fields["version"] == "1.0.0"

    def test_fullwidth_colon(self) -> None:
        fields, _ = parse_frontmatter(
            '---\nname：全角冒号\ndescription：描述文本\n---\n'
        )
        assert fields["name"] == "全角冒号"
        assert fields["description"] == "描述文本"

    def test_real_world_notice_skill(self, tmp_path: Path) -> None:
        make_skill(
            tmp_path,
            "notice",
            frontmatter=(
                '---\nversion:"1.0.0"\nname:撰写通知\n'
                'description:"撰写公司内部通知"\n---\n'
            ),
            body="正文",
        )
        registry = SkillRegistry(tmp_path)
        registry.discover()
        assert registry.names == ("撰写通知",)
        assert registry.errors == []
