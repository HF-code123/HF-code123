"""Skill 相关工具：loadSkill 按需加载 + createSkill 运行时创建。

- loadSkill：仅传 name 返回该技能 SKILL.md 全文；传 filename 读取
  技能目录内的附加文件（优先精确路径匹配，裸文件名递归唯一匹配）。
- createSkill：根据自然语言需求在当前项目的 ``.agents/skills/`` 下
  创建新技能（英文子目录 + 符合 Agent Skills 规范的 SKILL.md），
  创建后立即注册，当前会话即可用 loadSkill 加载；如需 references/、
  scripts/ 等附加目录或辅助文件，由模型继续调用现有文件工具完成。
"""

from __future__ import annotations

from typing import Any

from ..skills import SkillRegistry
from .registry import ToolRegistry, ToolSpec

TOOL_NAME = "loadSkill"
CREATE_TOOL_NAME = "createSkill"

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "要加载的技能名称，必须来自系统提示词中列出的可用技能",
        },
        "filename": {
            "type": "string",
            "description": (
                "可选。要加载的附加文件：可以是相对于技能目录的路径"
                "（如 references/api.md）、相对于项目根目录的路径，或绝对路径"
                "（后两者仍必须位于该技能目录内）；只给文件名时会在技能目录内"
                "递归搜索，命中唯一文件时才读取。不填时返回 SKILL.md 全文"
            ),
        },
    },
    "required": ["name"],
}

CREATE_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": (
                "技能名称，同时作为子目录名。只能由小写字母、数字和连字符组成，"
                "如 daily-report 或 pdf-summarizer"
            ),
        },
        "skillContext": {
            "type": "string",
            "description": (
                "要写入 SKILL.md 的完整内容。可以自带 YAML Frontmatter"
                "（name 必须与参数 name 一致，且包含 description），"
                "也可以只给 Markdown 正文"
            ),
        },
        "description": {
            "type": "string",
            "description": (
                "技能描述。当 skillContext 未包含 frontmatter 时必需，"
                "将写入自动生成的 frontmatter；已包含时可省略"
            ),
        },
    },
    "required": ["name", "skillContext"],
}


def install_unknown_tool_hint(registry: ToolRegistry, skills: SkillRegistry) -> None:
    """模型把技能名当工具名调用时（如 ``guizang-ppt-skill-ppt-skill``），
    在「未知工具」结果里直接给出纠正：改用 loadSkill 并给出正确参数。
    """

    def hint(name: str) -> str | None:
        lowered = name.lower().replace("_", "-")
        for skill in skills.skills:
            if skill.name in lowered:
                return (
                    f"「{skill.name}」是技能（skill），不是工具，不能作为工具名调用。"
                    f"请改为调用 loadSkill 工具，参数 JSON: "
                    f'{{"name": "{skill.name}"}}；'
                    "加载说明后再按说明使用普通工具完成任务。"
                )
        return None

    registry.unknown_hint = hint


def register(registry: ToolRegistry, skills: SkillRegistry) -> None:
    """向工具注册表注册 createSkill 与 loadSkill（均始终可用）。

    loadSkill 始终注册：即使启动时没有任何技能，模型也可以先用
    createSkill 创建，再在当前会话用 loadSkill 加载。
    """

    install_unknown_tool_hint(registry, skills)

    def create_handler(arguments: dict[str, Any]) -> str:
        name = str(arguments.get("name", "")).strip()
        context = str(arguments.get("skillContext", "") or "")
        description = str(arguments.get("description", "") or "").strip() or None
        if not name:
            return "参数 name 不能为空，请传入要创建的技能名称"
        if not context.strip():
            return "参数 skillContext 不能为空，请传入要写入 SKILL.md 的完整内容"
        try:
            metadata = skills.create_skill(name, context, description=description)
        except FileExistsError as exc:
            return f"创建技能失败: {exc}"
        except (ValueError, OSError) as exc:
            return f"创建技能失败: {exc}"
        return (
            f"技能创建成功: {metadata.skill_file}\n"
            f"名称: {metadata.name}\n"
            f"描述: {metadata.description}\n"
            "当前会话即可用 loadSkill 加载该技能；重启程序后它会出现在"
            "系统提示词的可用技能列表中。"
            "如需添加 references/、scripts/ 等目录或辅助文件，"
            "请继续调用 createDir / createFile 等文件工具。"
        )

    registry.register(
        ToolSpec(
            name=CREATE_TOOL_NAME,
            description=(
                "创建新的技能（Skill）：在当前项目的 .agents/skills/ 下建立"
                "以 name 命名的英文子目录，并生成符合 Agent Skills 规范的 "
                "SKILL.md（含 YAML Frontmatter 的 name/description 与正文）。"
                "目标技能已存在时会明确提示，不会覆盖。"
                "创建后立即生效，可用 loadSkill 加载；"
                "附加目录和辅助文件请继续用文件工具创建。"
            ),
            parameters=CREATE_PARAMETERS,
            handler=create_handler,
        )
    )

    def load_handler(arguments: dict[str, Any]) -> str:
        name = str(arguments.get("name", "")).strip()
        if not name:
            return "参数 name 不能为空，请传入要加载的技能名称"
        # 仅提供 name 时读取 SKILL.md 全文；提供 filename 时只读附加文件，
        # 不重复加载 SKILL.md
        filename = str(arguments.get("filename", "") or "").strip()
        try:
            if filename:
                return skills.load_file(name, filename)
            body = skills.load_body(name)
        except KeyError:
            available = ", ".join(skills.names) or "（无）"
            return f"未知技能: {name}。可用技能: {available}"
        except (OSError, ValueError) as exc:
            return f"加载技能 {name} 失败: {exc}"

        # 正文前给出技能目录：很多技能用 <SKILL_ROOT> 引用自带的模板/脚本，
        # 没有绝对路径时模型只能猜，容易把 assets/template.html 这类文件找错。
        metadata = skills.get(name)
        if metadata is None:
            return body
        return (
            f"[skill] {metadata.name}\n"
            f"[skill_dir] {metadata.directory}\n"
            "技能说明中出现 <SKILL_ROOT> 时替换为上面的目录；该目录内的文件"
            "可用 loadSkill(name, filename) 读取，或用 readFile / listDir / "
            "runCli / patchFile 直接操作。\n"
            "---------- 技能正文 ----------\n"
            f"{body}"
        )

    registry.register(
        ToolSpec(
            name=TOOL_NAME,
            description=(
                "按需加载指定技能（Skill）的内容：仅传 name 返回该技能 "
                "SKILL.md 全文；同时传 filename 可读取该技能目录内的附加文件"
                "（优先精确路径匹配，仅给文件名时在技能目录内递归唯一匹配）。"
                "技能本身不是工具，不要把技能名当作工具名调用——想用某个技能，"
                "就用本工具加载它的说明，再按说明调用普通工具完成任务。"
                "技能正文只在本次调用后作为工具结果进入对话上下文；"
                "技能名称必须来自系统提示词中列出的可用技能列表。"
            ),
            parameters=PARAMETERS,
            handler=load_handler,
        )
    )
