"""上下文压缩与历史归档管理。

采用简单、可理解的实现方案（不使用向量数据库 / Embedding）：

- Token 用量：优先使用模型 API 返回的 usage 信息；拿不到时用
  :func:`estimate_tokens` 启发式估算（CJK 字符约 1 token/字，
  其他字符约 4 字符/token）。
- 归档：压缩前把原始会话消息逐行写入
  ``.agents/context/<YYYYMMDD-HHMMSS>.jsonl``，写后回读校验，
  成功后才允许执行压缩替换。
- 检索：recallContext 工具用纯 Python 关键词匹配在归档中查找，
  返回片段 + 归档编号 + 位置，并限制结果长度。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ARCHIVE_ID_RE = re.compile(r"^\d{8}-\d{6}$")

# 检索结果上限：控制召回内容不再次撑爆上下文
MAX_MATCHES = 20
MAX_SNIPPET_CHARS = 200
MAX_RESULT_CHARS = 4000

# 上下文压缩说明追加在摘要末尾
ARCHIVE_NOTE_TEMPLATE = (
    "此前上下文已于 {time} 完成压缩，原始上下文保存在编号为 {id} 的归档文件中。"
    "如需查找历史信息，请使用 recallContext 工具。"
)

COMPACTION_PROMPT = """你是一名 AI Agent 的上下文管理助手。当前会话即将达到上下文窗口限制，需要将历史对话压缩为一份高质量的工作摘要，以便 Agent 在新的上下文中继续执行任务。

请认真阅读提供的历史上下文，提取对后续工作仍然有价值的信息，并生成一份约 5,000 Tokens 的 Markdown 摘要。

摘要应重点保留以下内容：

1. **用户目标与要求**：用户希望完成什么任务？有哪些必须遵守的约束、偏好和验收条件？
2. **已经完成的工作**：哪些任务已经完成？实现了哪些功能？获得了哪些已经验证的结果？
3. **当前工作状态**：Agent 正在处理什么任务？当前进展到哪一步？是否存在尚未解决的问题？
4. **关键技术信息**：保留用药推荐系统的关键实现要素——药品数据库表结构与字段（如药品信息表、禁忌症表、药物相互作用表、用户健康档案表）、用药规则与风险校验逻辑（如禁忌症判断、药物相互作用检查、剂量与人群适配）、接口定义（如用药查询接口、健康档案接口、AI 用药问答接口）、配置项（如模型密钥、知识库路径）、执行命令、错误信息和必要的代码片段。
5. **关键决策与原因**：记录用户和 Agent 已经确定的技术方案，以及重要的设计取舍。
6. **下一步任务**：明确接下来应该执行的操作，以及需要继续验证的事项。

请遵守以下要求：

- 优先保留对后续任务有直接影响的信息，删除寒暄、重复讨论、过时的尝试和无关内容。
- 对已经验证的事实与尚未验证的推测作出明确区分。
- 不要将计划完成的任务描述为已经完成。
- 重要的文件路径、药品名称、配置名称和命令应保留准确原文。
- 对失败的尝试，只保留有助于避免重复错误的信息。
- 如果信息存在冲突，应优先保留用户最新确认的要求，并简要说明仍然存在的冲突。
- 如果某些历史细节无法在摘要中完整保留，可简要说明它们已保存到原始上下文归档中，后续可以通过 `recallContext` 工具重新检索。
- 使用清晰的 Markdown 结构输出摘要，不要附加解释或无关内容。

**最终目标：使一个从未阅读过原始对话的 Agent，仅凭这份摘要，就能够理解当前任务、继承关键决策，并继续开展工作。**"""


def _next_archive_id(archive_id: str) -> str:
    """归档编号 +1 秒：同秒冲突时保证唯一。"""

    moment = datetime.strptime(archive_id, "%Y%m%d-%H%M%S")
    from datetime import timedelta

    return (moment + timedelta(seconds=1)).strftime("%Y%m%d-%H%M%S")


def estimate_tokens(text: str) -> int:
    """启发式 Token 估算：CJK 字符约 1 token/字，其他约 4 字符/token。"""

    if not text:
        return 0
    cjk = sum(1 for ch in text if ord(ch) > 0x2E7F)
    other = len(text) - cjk
    return cjk + (other + 3) // 4


def estimate_messages_tokens(messages: list[dict]) -> int:
    """对整段消息列表做 Token 估算（含角色、工具调用等结构信息）。"""

    total = 0
    for message in messages:
        total += estimate_tokens(json.dumps(message, ensure_ascii=False)) + 4
    return total


@dataclass(frozen=True)
class ArchiveRecord:
    """一次归档的结果。"""

    id: str
    path: Path
    message_count: int


@dataclass(frozen=True)
class ArchiveMatch:
    """一次检索命中。"""

    archive_id: str
    line_number: int  # 消息在归档文件中的行号（0 起）
    role: str
    snippet: str


class ArchiveError(RuntimeError):
    """归档失败：此时绝不能继续压缩替换，避免丢失上下文。"""


class ContextArchiver:
    """原始上下文归档与检索（纯本地 JSONL + 关键词匹配）。"""

    def __init__(self, directory: Path | None = None) -> None:
        self._directory = (
            directory if directory is not None
            else Path.cwd() / ".agents" / "context"
        )

    @property
    def directory(self) -> Path:
        return self._directory

    def archive(self, messages: list[dict]) -> ArchiveRecord:
        """把消息列表完整归档为 JSONL；写后回读校验，失败抛 ArchiveError。"""

        self._directory.mkdir(parents=True, exist_ok=True)
        archive_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self._directory / f"{archive_id}.jsonl"
        # 同一秒内多次归档时秒数递增，保证编号唯一、不覆盖旧归档
        while target.exists():
            archive_id = _next_archive_id(archive_id)
            target = self._directory / f"{archive_id}.jsonl"
        try:
            with target.open("w", encoding="utf-8") as fh:
                for message in messages:
                    fh.write(json.dumps(message, ensure_ascii=False) + "\n")
            # 回读校验：原始记录必须成功保存后才能压缩
            with target.open("r", encoding="utf-8") as fh:
                saved = [line for line in fh.read().splitlines() if line.strip()]
        except OSError as exc:
            raise ArchiveError(f"写入归档文件失败: {target}: {exc}") from exc
        if len(saved) != len(messages):
            raise ArchiveError(
                f"归档校验失败：写入 {len(messages)} 条消息，回读只有 {len(saved)} 条"
            )
        return ArchiveRecord(
            id=archive_id, path=target, message_count=len(messages)
        )

    def list_archives(self) -> list[str]:
        """列出所有归档编号（按时间升序）。"""

        if not self._directory.is_dir():
            return []
        return sorted(p.stem for p in self._directory.glob("*.jsonl"))

    def search(self, text: str, archive_id: str | None = None) -> list[ArchiveMatch]:
        """关键词检索归档；不指定 id 时检索全部归档。"""

        query = text.strip()
        if not query:
            return []

        if archive_id is not None:
            archive_id = archive_id.strip()
            if not ARCHIVE_ID_RE.match(archive_id):
                raise ValueError(
                    f"归档编号 {archive_id!r} 不合法，应为形如 20261022-153000 的编号"
                )
            files = [self._directory / f"{archive_id}.jsonl"]
        else:
            files = [
                self._directory / f"{aid}.jsonl" for aid in self.list_archives()
            ]

        matches: list[ArchiveMatch] = []
        for path in files:
            if not path.is_file():
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for line_number, line in enumerate(lines):
                if len(matches) >= MAX_MATCHES:
                    return matches
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                haystack = _message_search_text(message)
                position = haystack.find(query)
                if position == -1:
                    continue
                start = max(0, position - 60)
                end = min(len(haystack), position + len(query) + 60)
                prefix = "…" if start > 0 else ""
                suffix = "…" if end < len(haystack) else ""
                matches.append(
                    ArchiveMatch(
                        archive_id=path.stem,
                        line_number=line_number,
                        role=str(message.get("role", "?")),
                        snippet=f"{prefix}{haystack[start:end]}{suffix}",
                    )
                )
        return matches


def _message_search_text(message: dict) -> str:
    """把一条消息展平成可检索的文本（角色 + 内容 + 工具调用/结果）。"""

    parts = [f"[{message.get('role', '?')}]"]
    content = message.get("content")
    if isinstance(content, str) and content:
        parts.append(content)
    for call in message.get("tool_calls") or []:
        function = call.get("function", {}) if isinstance(call, dict) else {}
        parts.append(
            f"{function.get('name', '')}({function.get('arguments', '')})"
        )
    if message.get("tool_call_id"):
        parts.append(str(message.get("content") or ""))
    return " ".join(part for part in parts if part)


def build_compaction_messages(messages: list[dict]) -> list[dict]:
    """构造压缩专用请求：系统角色放压缩 Prompt，用户角色放原始历史。"""

    serialized = "\n".join(
        json.dumps(message, ensure_ascii=False) for message in messages
    )
    return [
        {"role": "system", "content": COMPACTION_PROMPT},
        {
            "role": "user",
            "content": (
                "以下是需要压缩的完整历史上下文（JSONL 格式，每行一条消息）：\n\n"
                f"{serialized}"
            ),
        },
    ]
