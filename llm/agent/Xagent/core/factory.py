"""组合根：把配置、LLM 客户端、工具注册表和 Agent 组装起来。

前端不应该自己 new 这些对象，只需要拿到 build_agent() 返回的 Agent。
这样 core 内部实现变化时，前端代码不需要改动。

组装流程中包含：
1. Skill 动态加载机制：发现 .agents/skills/，元数据注入系统提示词，
   loadSkill / createSkill 工具；
2. 上下文管理：ContextArchiver 负责 .agents/context/ 归档与检索，
   recallContext 工具 + ChatAgent 的 /compact 压缩流程。
"""

from __future__ import annotations

from pathlib import Path

from .agent import ChatAgent
from .config import AppConfig
from .context_manager import ContextArchiver
from .llm import OpenAILLMClient
from .skills import SkillRegistry, discover_skills
from .tools import ToolRegistry, build_default_registry
from .tools import recall_tools, skill_tools

AGENTS_MD_FILENAME = "AGENTS.md"


def read_agents_md(project_root: Path | None = None) -> str:
    """读取项目根目录的 AGENTS.md；不存在时返回空串。"""

    root = project_root or Path.cwd()
    path = root / AGENTS_MD_FILENAME
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def discover_skill_registry(project_root: Path | None = None) -> SkillRegistry:
    """发现并注册项目根目录下的 Skills（初始化阶段只读元数据）。"""

    registry = discover_skills(project_root)
    for warning in registry.warnings:
        print(f"[skills] 警告: {warning}")
    for error in registry.errors:
        print(f"[skills] 错误: {error}")
    return registry


def build_agent(
    config: AppConfig,
    registry: ToolRegistry | None = None,
    skills: SkillRegistry | None = None,
    archiver: ContextArchiver | None = None,
    project_root: Path | None = None,
) -> ChatAgent:
    skills = skills if skills is not None else discover_skill_registry(project_root)
    root = project_root or Path.cwd()
    archiver = archiver or ContextArchiver(root / ".agents" / "context")

    system_prompt = config.agent.system_prompt
    prompt_section = skills.render_prompt_section()
    if prompt_section:
        system_prompt = f"{system_prompt}\n\n# 可用技能\n{prompt_section}"

    tools = registry or build_default_registry(config.search)
    skill_tools.register(tools, skills)
    recall_tools.register(tools, archiver)

    return ChatAgent(
        llm=OpenAILLMClient(config.llm),
        tools=tools,
        system_prompt=system_prompt,
        max_tool_rounds=config.agent.max_tool_rounds,
        context_window=config.agent.context_window,
        context_window_is_default=config.agent.context_window_is_default,
        context_warning_ratio=config.agent.context_warning_ratio,
        archiver=archiver,
        project_context=read_agents_md(root),
        skills=skills,
    )
