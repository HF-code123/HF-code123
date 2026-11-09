"""createSkill 工具测试：运行时创建技能、规范校验与防覆盖。"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.skills import SKILL_NAME_RE, discover_skills
from core.tools import ToolRegistry
from core.tools.skill_tools import CREATE_TOOL_NAME, TOOL_NAME
from core.tools.skill_tools import register as register_skill_tools


class TestSkillNameRules:
    def test_valid_names(self) -> None:
        for name in ("a", "daily-report", "pdf-summarizer-2", "a1-b2"):
            assert SKILL_NAME_RE.match(name)

    def test_invalid_names(self) -> None:
        for name in (
            "Daily-Report",  # 大写
            "daily report",  # 空格
            "daily_report",  # 下划线
            "-daily",  # 连字符开头
            "daily-",  # 连字符结尾
            "中文技能",  # 非小写英文
            "",  # 空串
        ):
            assert not SKILL_NAME_RE.match(name)


class TestCreateSkill:
    def test_creates_directory_and_file(self, tmp_path: Path) -> None:
        registry = discover_skills(tmp_path)
        metadata = registry.create_skill(
            "daily-report", "这是技能正文。", description="生成日报"
        )

        skill_file = tmp_path / ".agents" / "skills" / "daily-report" / "SKILL.md"
        assert skill_file.is_file()
        assert metadata.name == "daily-report"
        assert metadata.description == "生成日报"
        # 立即注册：无需重新 discover
        assert "daily-report" in registry.names
        assert registry.load_body("daily-report") == "这是技能正文。"

    def test_generated_frontmatter_is_compliant(self, tmp_path: Path) -> None:
        registry = discover_skills(tmp_path)
        registry.create_skill("demo", "正文内容", description="描述: 含冒号")

        text = (
            (tmp_path / ".agents" / "skills" / "demo" / "SKILL.md")
            .read_text(encoding="utf-8")
        )
        assert text.startswith("---\n")
        assert 'name: demo' in text
        assert 'description:' in text

    def test_skill_context_with_frontmatter_kept_as_is(
        self, tmp_path: Path
    ) -> None:
        registry = discover_skills(tmp_path)
        context = (
            "---\nname: with-fm\ndescription: 自带frontmatter\n---\n\n正文A\n"
        )
        registry.create_skill("with-fm", context)

        text = (
            (tmp_path / ".agents" / "skills" / "with-fm" / "SKILL.md")
            .read_text(encoding="utf-8")
        )
        assert text == context
        assert registry.load_body("with-fm") == "正文A"

    def test_frontmatter_name_mismatch_rejected(self, tmp_path: Path) -> None:
        registry = discover_skills(tmp_path)
        context = "---\nname: other\ndescription: 描述\n---\n正文\n"
        with pytest.raises(ValueError, match="不一致"):
            registry.create_skill("with-fm", context)

    def test_frontmatter_missing_description_rejected(
        self, tmp_path: Path
    ) -> None:
        registry = discover_skills(tmp_path)
        context = "---\nname: with-fm\n---\n正文\n"
        with pytest.raises(ValueError, match="description"):
            registry.create_skill("with-fm", context)

    def test_body_without_description_rejected(self, tmp_path: Path) -> None:
        registry = discover_skills(tmp_path)
        with pytest.raises(ValueError, match="description"):
            registry.create_skill("demo", "只有正文")

    def test_invalid_name_rejected(self, tmp_path: Path) -> None:
        registry = discover_skills(tmp_path)
        for bad in ("Daily", "daily report", "daily_report", "中文", "-x"):
            with pytest.raises(ValueError, match="不合法"):
                registry.create_skill(bad, "正文", description="描述")

    def test_existing_skill_not_overwritten(self, tmp_path: Path) -> None:
        registry = discover_skills(tmp_path)
        registry.create_skill("demo", "旧正文", description="旧描述")
        old_text = (
            (tmp_path / ".agents" / "skills" / "demo" / "SKILL.md")
            .read_text(encoding="utf-8")
        )

        with pytest.raises(FileExistsError, match="覆盖"):
            registry.create_skill("demo", "新正文", description="新描述")

        # 文件内容保持不变
        assert (
            tmp_path / ".agents" / "skills" / "demo" / "SKILL.md"
        ).read_text(encoding="utf-8") == old_text

    def test_created_skill_survives_rediscover(self, tmp_path: Path) -> None:
        registry = discover_skills(tmp_path)
        registry.create_skill("demo", "持久正文", description="持久技能")

        fresh = discover_skills(tmp_path)
        assert fresh.names == ("demo",)
        assert fresh.load_body("demo") == "持久正文"


class TestCreateSkillTool:
    def _registry(self, tmp_path: Path) -> tuple[ToolRegistry, object]:
        skills = discover_skills(tmp_path)
        registry = ToolRegistry()
        register_skill_tools(registry, skills)
        return registry, skills

    def test_create_skill_always_registered(self, tmp_path: Path) -> None:
        registry, _ = self._registry(tmp_path)
        # 两个 Skill 工具都始终注册，形成创建→加载闭环
        assert CREATE_TOOL_NAME in registry.names
        assert TOOL_NAME in registry.names

    def test_create_and_load_in_same_session(self, tmp_path: Path) -> None:
        import asyncio

        registry, skills = self._registry(tmp_path)
        result = asyncio.run(
            registry.execute(
                CREATE_TOOL_NAME,
                {
                    "name": "daily-report",
                    "skillContext": "正文",
                    "description": "生成日报",
                },
            )
        )
        assert "创建成功" in result

        # 创建后 loadSkill 立即可用（无需重启）
        assert TOOL_NAME in registry.names
        loaded = asyncio.run(
            registry.execute(TOOL_NAME, {"name": "daily-report"})
        )
        assert "正文" in loaded

    def test_duplicate_returns_clear_message(self, tmp_path: Path) -> None:
        import asyncio

        registry, _ = self._registry(tmp_path)
        for _ in range(2):
            pass
        asyncio.run(
            registry.execute(
                CREATE_TOOL_NAME,
                {"name": "demo", "skillContext": "正文", "description": "描述"},
            )
        )
        result = asyncio.run(
            registry.execute(
                CREATE_TOOL_NAME,
                {"name": "demo", "skillContext": "新正文", "description": "新"},
            )
        )
        assert "创建技能失败" in result
        assert "覆盖" in result

    def test_invalid_name_reports_error(self, tmp_path: Path) -> None:
        import asyncio

        registry, _ = self._registry(tmp_path)
        result = asyncio.run(
            registry.execute(
                CREATE_TOOL_NAME,
                {"name": "Bad Name", "skillContext": "正文", "description": "描述"},
            )
        )
        assert "创建技能失败" in result
        assert "不合法" in result

    def test_missing_description_reports_error(self, tmp_path: Path) -> None:
        import asyncio

        registry, _ = self._registry(tmp_path)
        result = asyncio.run(
            registry.execute(
                CREATE_TOOL_NAME,
                {"name": "demo", "skillContext": "只有正文"},
            )
        )
        assert "创建技能失败" in result
        assert "description" in result

    def test_empty_arguments_rejected(self, tmp_path: Path) -> None:
        import asyncio

        registry, _ = self._registry(tmp_path)
        result = asyncio.run(registry.execute(CREATE_TOOL_NAME, {}))
        assert "不能为空" in result
