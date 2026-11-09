"""recallContext 工具：检索历史上下文归档。

上下文压缩后，原始对话保存在 .agents/context/<编号>.jsonl 中。
当压缩摘要缺少某些历史细节时，LLM 可以主动调用本工具，
用关键词在归档中查找原始记录片段。
"""

from __future__ import annotations

from typing import Any

from ..context_manager import (
    MAX_RESULT_CHARS,
    ContextArchiver,
)
from .registry import ToolRegistry, ToolSpec

TOOL_NAME = "recallContext"

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "description": "要查找的关键词或文本内容（必需）",
        },
        "id": {
            "type": "string",
            "description": (
                "可选。要检索的归档编号（形如 20261022-153000，"
                "见压缩摘要末尾的归档说明）。不填时检索全部历史归档"
            ),
        },
    },
    "required": ["text"],
}


def _format_matches(matches: list) -> str:
    """把命中结果格式化为受限长度的文本，避免再次撑爆上下文。"""

    lines: list[str] = []
    used = 0
    shown = 0
    for match in matches:
        line = (
            f"{shown + 1}. 归档 {match.archive_id} · 第 {match.line_number} 条消息 · "
            f"角色 {match.role}\n   {match.snippet}"
        )
        if lines and used + len(line) > MAX_RESULT_CHARS:
            lines.append(
                f"…（结果过长，仅显示前 {shown} 条；"
                "可换更具体的关键词或指定 id 缩小范围）"
            )
            break
        lines.append(line)
        used += len(line)
        shown += 1

    total = len(matches)
    header = f"共找到 {total} 处匹配："
    if shown < total:
        header += f"（已截断，仅显示 {shown} 条）"
    return header + "\n" + "\n".join(lines)


def register(registry: ToolRegistry, archiver: ContextArchiver) -> None:
    """向工具注册表注册 recallContext。"""

    def handler(arguments: dict[str, Any]) -> str:
        text = str(arguments.get("text", "")).strip()
        if not text:
            return "参数 text 不能为空，请传入要查找的关键词"
        archive_id = str(arguments.get("id", "") or "").strip() or None
        try:
            matches = archiver.search(text, archive_id)
        except ValueError as exc:
            return f"检索失败: {exc}"
        if not matches:
            scope = f"归档 {archive_id}" if archive_id else "所有历史归档"
            return (
                f"在{scope}中未找到包含 {text!r} 的内容。"
                "可以尝试其他关键词，或去掉 id 参数扩大检索范围。"
            )
        return _format_matches(matches)

    registry.register(
        ToolSpec(
            name=TOOL_NAME,
            description=(
                "检索历史上下文归档：上下文压缩前的原始对话保存在本地归档中，"
                "当压缩摘要缺少某些历史细节（如文件路径、配置、代码、"
                "此前的工具结果）时，用关键词查找原始记录片段。"
                "返回归档编号、消息位置和内容片段。"
            ),
            parameters=PARAMETERS,
            handler=handler,
        )
    )
