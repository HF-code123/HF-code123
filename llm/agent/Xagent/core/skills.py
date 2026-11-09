"""Skill 动态加载机制（符合 Agent Skills 规范）。

发现与注册流程：
1. 以程序启动时的工作目录为项目根目录，寻找 ``.agents/skills/`` 目录；
2. 扫描其中的各个 Skill 子目录，每个子目录至少包含一个 ``SKILL.md``
   （允许有限深度递归，兼容下载/解压后多包一层的技能包）；
3. ``SKILL.md`` 开头通过 YAML Frontmatter 声明必需的 ``name`` 与 ``description``
   两个字段；
4. 初始化阶段只解析并保存这两个元数据字段，不读取 Skill 正文（懒加载），
   需要时再通过 :meth:`SkillRegistry.load_body` 读取完整内容。

设计要点：
- 发现失败、字段缺失、目录缺失等情况不会让程序崩溃，而是记录到
  ``errors`` / ``warnings`` 中并跳过对应 Skill；
- 不引入新的强制依赖：装有 PyYAML 时使用完整 YAML 解析，
  否则回退到内置的轻量 ``key: value`` 解析器（足以覆盖规范必需的
  name / description 单行标量字段）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# 修复常见的「key:value / key：value」（冒号后无空格或使用全角冒号）写法：
# 只在解析失败后的二次尝试中启用，行首键名后跟的第一个冒号补上空格。
_LOOSE_KEY_COLON_RE = re.compile(r"(?m)^([ \t]*[\w.-]+)[ \t]*[:：][ \t]*(?=\S)")

# createSkill 的技能名称规范：小写字母、数字和连字符组成
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

try:  # PyYAML 为可选依赖，缺失时回退到内置解析器
    import yaml  # type: ignore

    _HAS_YAML = True
except ImportError:  # pragma: no cover - 取决于运行环境是否安装 PyYAML
    yaml = None
    _HAS_YAML = False

SKILLS_DIR_NAME = ".agents"
SKILLS_SUBDIR_NAME = "skills"
SKILL_FILE_NAME = "SKILL.md"

# 递归发现的最大深度：兼容「下载/解压后多包一层」的技能包，例如
# ``.agents/skills/<name>_#SULP#_<id>/<name>/SKILL.md``。
# 深度按「从 .agents/skills 下一层算起」计：0 = 技能目录本身。
MAX_SKILL_DEPTH = 3

# 递归时跳过的目录名：命中这些名字的子树只可能是依赖/缓存，不可能放技能说明
_SKIP_DIR_NAMES = {"node_modules", "__pycache__", "site-packages", ".venv", "venv"}

# Frontmatter 必需字段
REQUIRED_FIELDS = ("name", "description")


@dataclass(frozen=True)
class SkillMetadata:
    """单个 Skill 的轻量元数据（初始化阶段唯一加载的内容）。"""

    name: str
    description: str
    directory: Path
    skill_file: Path


@dataclass(frozen=True)
class Skill:
    """完整 Skill：元数据 + 按需加载的正文。"""

    metadata: SkillMetadata
    body: str


def skills_root(project_root: Path | None = None) -> Path:
    """返回项目根目录下的 ``.agents/skills`` 目录路径。"""

    root = project_root if project_root is not None else Path.cwd()
    return root / SKILLS_DIR_NAME / SKILLS_SUBDIR_NAME


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """解析 Markdown 文件开头的 YAML Frontmatter。

    返回 ``(frontmatter 字段字典, 去掉 frontmatter 后的正文)``。
    没有 frontmatter 时返回空字典和原文。
    """

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text

    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end = index
            break
    if end is None:
        return {}, text

    block = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    return _parse_block(block), body


def _parse_block(block: str) -> dict[str, str]:
    """解析 frontmatter 文本块，逐级放宽以保证兼容常见书写习惯。

    1. PyYAML 严格解析（冒号后必须有空格的规范写法）；
    2. 解析失败或结果不是字典时，对 ``key:value`` / ``key：value``
       补空格后用 PyYAML 重试（兼容中文输入常见的紧凑写法）；
    3. 仍失败时回退到内置轻量解析器（不依赖 PyYAML 也能工作）。
    """

    if _HAS_YAML:
        for candidate in (block, _LOOSE_KEY_COLON_RE.sub(r"\1: ", block)):
            try:
                data = yaml.safe_load(candidate) or {}
            except yaml.YAMLError:
                continue
            if isinstance(data, dict):
                return {
                    str(key): value if isinstance(value, str) else str(value)
                    for key, value in data.items()
                }
    return _parse_simple_yaml(block)


def _parse_simple_yaml(block: str) -> dict[str, str]:
    """内置轻量解析器：支持 ``key: value`` 单行标量（可带引号）。

    兼容冒号后无空格、全角冒号 ``：`` 等常见中文书写习惯。
    """

    fields: dict[str, str] = {}
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        separator = ":" if ":" in stripped else ("：" if "：" in stripped else None)
        if separator is None:
            continue
        key, _, raw_value = stripped.partition(separator)
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        fields[key.strip()] = value
    return fields


class SkillRegistry:
    """Skill 注册表：负责发现、注册与按需加载正文。

    初始化阶段调用 :meth:`discover`，只读取各 SKILL.md 的 frontmatter
    （name / description），不读取正文；正文在调用 :meth:`load_body`
    时才从磁盘读取。
    """

    def __init__(self, project_root: Path | None = None) -> None:
        self._project_root = (project_root if project_root is not None else Path.cwd()).resolve()
        self._root = skills_root(self._project_root)
        self._skills: dict[str, SkillMetadata] = {}
        self.errors: list[str] = []
        self.warnings: list[str] = []

    @property
    def project_root(self) -> Path:
        return self._project_root

    @property
    def root(self) -> Path:
        return self._root

    @property
    def skills(self) -> tuple[SkillMetadata, ...]:
        """已注册 Skill 的元数据（按发现顺序）。"""

        return tuple(self._skills.values())

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._skills)

    def get(self, name: str) -> SkillMetadata | None:
        return self._skills.get(name)

    def discover(self) -> None:
        """扫描 ``.agents/skills/`` 下的 Skill 子目录并注册元数据。

        规范要求每个 Skill 是技能根的「直接子目录」且含 SKILL.md；
        实际从技能市场下载/解压的包常常多包一层目录
        （如 ``guizang-ppt-skill_#SULP#_0lea6z/guizang-ppt-skill/SKILL.md``），
        因此这里做有限深度递归：一旦某目录自己有 SKILL.md 就当作 Skill，
        不再深入它的子目录（避免把技能自带的示例当成新技能）。
        """

        self._skills.clear()
        self.errors.clear()
        self.warnings.clear()

        if not self._root.is_dir():
            return

        for entry in sorted(self._root.iterdir()):
            if not entry.is_dir():
                continue
            if not self._discover_tree(entry, depth=0):
                self.errors.append(
                    f"技能目录缺少 {SKILL_FILE_NAME}，已跳过: {entry}"
                )

    def _discover_tree(self, directory: Path, *, depth: int) -> bool:
        """在 ``directory`` 子树中寻找 SKILL.md；返回是否至少注册了一个技能。"""

        skill_file = directory / SKILL_FILE_NAME
        if skill_file.is_file():
            self._discover_one(directory)
            return True
        if depth >= MAX_SKILL_DEPTH:
            return False

        found = False
        try:
            children = sorted(directory.iterdir())
        except OSError as exc:
            self.errors.append(f"无法读取技能目录 {directory}: {exc}")
            return False

        for child in children:
            if not child.is_dir():
                continue
            if child.name.startswith(".") or child.name in _SKIP_DIR_NAMES:
                continue
            if self._discover_tree(child, depth=depth + 1):
                found = True
        return found

    def _discover_one(self, directory: Path) -> None:
        skill_file = directory / SKILL_FILE_NAME
        if not skill_file.is_file():
            self.errors.append(
                f"技能目录缺少 {SKILL_FILE_NAME}，已跳过: {directory}"
            )
            return

        fields, _ = self._read_metadata(skill_file)
        if fields is None:
            return  # 读取失败，错误信息已记录

        missing = [key for key in REQUIRED_FIELDS if not fields.get(key, "").strip()]
        if missing:
            self.errors.append(
                f"{skill_file} 的 frontmatter 缺少必需字段: {', '.join(missing)}"
            )
            return

        name = fields["name"].strip()
        if name in self._skills:
            self.warnings.append(
                f"技能名称重复，保留先注册的技能: {name}（{directory}）"
            )
            return

        self._skills[name] = SkillMetadata(
            name=name,
            description=fields["description"].strip(),
            directory=directory,
            skill_file=skill_file,
        )

    def _read_metadata(self, skill_file: Path) -> tuple[dict[str, str] | None, str]:
        """读取 SKILL.md 的 frontmatter 字段；正文在此阶段被丢弃。"""

        try:
            text = skill_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            self.errors.append(f"读取 {skill_file} 失败: {exc}")
            return None, ""

        fields, _ = parse_frontmatter(text)
        return fields, ""

    def load_body(self, name: str) -> str:
        """按需加载指定 Skill 的完整正文（不含 frontmatter）。

        这就是「动态加载」的第二阶段：初始化时只注册元数据，
        真正需要某个 Skill 的说明时才读取它的完整内容。
        """

        metadata = self._skills.get(name)
        if metadata is None:
            raise KeyError(f"未注册的技能: {name}")
        try:
            text = metadata.skill_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise OSError(f"读取技能正文失败: {metadata.skill_file}: {exc}") from exc
        _, body = parse_frontmatter(text)
        return body

    def load_full(self, name: str) -> Skill:
        """加载完整 Skill（元数据 + 正文）。"""

        metadata = self._skills[name]
        return Skill(metadata=metadata, body=self.load_body(name))

    def load_file(self, name: str, filename: str) -> str:
        """加载某个技能引用的附加文件内容（不重复加载 SKILL.md）。

        路径解析顺序（优先精确路径匹配，全部命中失败后再做递归搜索）：
        1. 绝对路径 —— 解析后必须位于该技能目录内；
        2. 相对路径 —— 依次尝试相对于技能目录、相对于项目根目录；
        3. 裸文件名（不含路径分隔符）—— 递归搜索该技能目录，
           命中唯一文件时读取，命中多个或零个时报错并列出候选。

        无论如何解析，最终读取范围都被限制在指定技能目录内部，
        避免路径解析错误导致读取其他文件。
        若最终命中的是 SKILL.md 本身，则返回去掉 frontmatter 的正文，
        与 :meth:`load_body` 行为保持一致。
        """

        metadata = self._skills.get(name)
        if metadata is None:
            raise KeyError(f"未注册的技能: {name}")

        raw = filename.strip()
        if not raw:
            raise ValueError("附加文件名不能为空")

        base = metadata.directory.resolve()
        normalized = raw.replace("\\", "/")
        parts = Path(normalized).parts
        if any(part in ("..", "") for part in parts):
            raise ValueError(f"附加文件路径不合法: {filename!r}")

        candidates: list[Path] = []
        path = Path(normalized)
        if path.is_absolute():
            resolved = path.resolve()
            if not resolved.is_relative_to(base):
                raise ValueError(
                    f"附加文件必须位于技能 {name} 的目录内: {filename!r}"
                )
            candidates.append(resolved)
        else:
            # 1) 相对于技能目录；2) 相对于项目根目录（如 .agents/skills/x/y.md）
            candidates.append(base / path)
            candidates.append(self._project_root / path)

        # 精确路径匹配
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved.is_relative_to(base) and resolved.is_file():
                return self._read_file(resolved)

        # 裸文件名：递归搜索技能目录
        if "/" not in normalized and not path.is_absolute():
            matches = sorted(
                item.resolve() for item in base.rglob(raw) if item.is_file()
            )
            if len(matches) == 1:
                return self._read_file(matches[0])
            if len(matches) > 1:
                listing = "\n".join(f"  - {m}" for m in matches)
                raise ValueError(
                    f"文件名 {raw!r} 在技能 {name} 中匹配到多个文件，"
                    f"请提供更完整的路径:\n{listing}"
                )

        raise FileNotFoundError(
            f"技能 {name} 中不存在附加文件: {filename!r}"
        )

    def _read_file(self, target: Path) -> str:
        """读取文件内容；SKILL.md 返回去掉 frontmatter 的正文。"""

        try:
            text = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise OSError(f"读取附加文件失败: {target}: {exc}") from exc
        if target.name == SKILL_FILE_NAME:
            return parse_frontmatter(text)[1]
        return text

    def create_skill(
        self,
        name: str,
        skill_context: str,
        *,
        description: str | None = None,
    ) -> SkillMetadata:
        """在 ``.agents/skills/<name>/`` 下创建新 Skill 并立即注册。

        参数 ``name`` 即子目录名，必须由小写字母、数字和连字符组成；
        ``skill_context`` 是要写入 SKILL.md 的完整内容，可以自带规范的
        YAML Frontmatter（此时要求其中 name 与参数一致、字段齐全），
        也可以只给正文——此时必须通过 ``description`` 提供描述，
        由本方法自动生成符合规范的 frontmatter。

        目标技能已存在时抛出 :class:`FileExistsError`，不覆盖原内容。
        """

        name = name.strip()
        if not SKILL_NAME_RE.match(name):
            raise ValueError(
                f"技能名称 {name!r} 不合法：只能由小写字母、数字和连字符组成"
            )
        if not skill_context.strip():
            raise ValueError("skillContext 不能为空")

        skill_file = self._root / name / SKILL_FILE_NAME
        if skill_file.exists():
            raise FileExistsError(
                f"技能已存在: {skill_file}，为避免意外覆盖原有内容，未执行创建；"
                "如需更新请先删除旧目录或换一个名称"
            )

        fields, _ = parse_frontmatter(skill_context)
        if fields:
            # skillContext 自带 frontmatter：校验必需字段与名称一致性
            missing = [
                key for key in REQUIRED_FIELDS if not fields.get(key, "").strip()
            ]
            if missing:
                raise ValueError(
                    f"skillContext 的 frontmatter 缺少必需字段: {', '.join(missing)}"
                )
            if fields["name"].strip() != name:
                raise ValueError(
                    f"skillContext frontmatter 中的 name ({fields['name'].strip()!r}) "
                    f"与参数 name ({name!r}) 不一致"
                )
            final_text = (
                skill_context if skill_context.endswith("\n") else skill_context + "\n"
            )
        else:
            # 只有正文：自动生成 frontmatter
            if not description or not description.strip():
                raise ValueError(
                    "skillContext 未包含 frontmatter，"
                    "必须通过 description 参数提供技能描述"
                )
            escaped = description.strip().replace("\\", "\\\\").replace('"', '\\"')
            final_text = (
                f"---\nname: {name}\ndescription: \"{escaped}\"\n---\n\n"
                f"{skill_context.strip()}\n"
            )

        try:
            self._root.mkdir(parents=True, exist_ok=True)
            skill_file.parent.mkdir(parents=True, exist_ok=True)
            skill_file.write_text(final_text, encoding="utf-8")
        except OSError as exc:
            raise OSError(f"写入技能文件失败: {skill_file}: {exc}") from exc

        # 回读校验，确保生成的文件可被正常的发现流程解析
        verify_fields, _ = parse_frontmatter(
            skill_file.read_text(encoding="utf-8")
        )
        missing = [key for key in REQUIRED_FIELDS if not verify_fields.get(key, "")]
        if missing:
            raise OSError(
                f"生成的 SKILL.md 校验失败，缺少字段: {', '.join(missing)}"
            )

        metadata = SkillMetadata(
            name=verify_fields["name"].strip(),
            description=verify_fields["description"].strip(),
            directory=skill_file.parent,
            skill_file=skill_file,
        )
        # 立即注册：当前会话即可用 loadSkill 加载；
        # 系统提示词中的技能列表在下次启动时更新。
        self._skills[metadata.name] = metadata
        return metadata

    def render_prompt_section(self) -> str:
        """把已注册技能列表渲染成注入 System Prompt 的片段；没有技能时返回空串。

        格式遵循 Agent Skills 规范的推荐写法：先逐条列出
        ``- name: description``，再说明如何用 loadSkill 按需加载，
        以及可以加载技能目录中引用的附加文件。
        """

        if not self._skills:
            return ""
        lines = ["You have access to the following skills:"]
        for skill in self._skills.values():
            lines.append(f"- {skill.name}: {skill.description}")
        first = next(iter(self._skills))
        lines.append(
            "IMPORTANT: a skill is NOT a tool. Never call a skill name as a "
            "tool, and never invent tool parameters for it. To use a skill, "
            "call the 'loadSkill' tool with JSON arguments, for example: "
            f'{{"name": "{first}"}}. Its full instructions then enter the '
            "conversation as the tool result; follow them with your existing "
            "tools (runCli, readFile, createFile, editFile, patchFile, "
            "listDir, renamePath, fetchWeb, getDate, ...). You can also load "
            "additional files referenced by a skill by passing filename to "
            "loadSkill. Until a skill is loaded, rely only on the names and "
            "descriptions above. When the user asks to create a new skill, "
            "use the 'createSkill' tool."
        )
        return "\n".join(lines)


def discover_skills(project_root: Path | None = None) -> SkillRegistry:
    """便捷入口：创建注册表并完成发现。"""

    registry = SkillRegistry(project_root)
    registry.discover()
    return registry
