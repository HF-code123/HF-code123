"""文件写入工具：createFile / editFile。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .registry import ToolRegistry, ToolSpec
from .text_utils import read_text_file


def create_file(dir_path: str, filename: str, content: str = "") -> str:
    name = Path(filename)
    if not filename or name.name != filename:
        return f"文件名不合法: {filename}"

    p = Path(dir_path).expanduser() / filename
    if p.exists():
        return f"文件已存在，未创建: {p}"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    except OSError as exc:
        return f"创建失败: {exc}"
    return f"已创建文件: {p}（{len(content)} 字符）"


def edit_file(path: str, content: str) -> str:
    p = Path(path).expanduser()
    if not p.is_file():
        return f"文件不存在: {path}"
    try:
        read_text_file(p)
    except ValueError:
        return f"不是纯文本文件，拒绝写入: {path}"
    except OSError as exc:
        return f"读取失败: {exc}"
    try:
        p.write_text(content, encoding="utf-8")
    except OSError as exc:
        return f"写入失败: {exc}"
    return f"已写入: {p}（原有内容已被覆盖，{len(content)} 字符）"


def _handle_create_file(arguments: dict[str, Any]) -> str:
    return create_file(
        str(arguments["dir"]),
        str(arguments["filename"]),
        str(arguments.get("content", "")),
    )


def _handle_edit_file(arguments: dict[str, Any]) -> str:
    return edit_file(str(arguments["path"]), str(arguments["content"]))


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="createFile",
            description="在指定目录下用指定文件名创建新文件，可同时写入初始内容。",
            parameters={
                "type": "object",
                "properties": {
                    "dir": {"type": "string", "description": "目标目录路径"},
                    "filename": {"type": "string", "description": "文件名，含扩展名，如 note.txt"},
                    "content": {"type": "string", "description": "文件初始内容，可为空"},
                },
                "required": ["dir", "filename"],
            },
            handler=_handle_create_file,
        )
    )
    registry.register(
        ToolSpec(
            name="editFile",
            description="编辑指定纯文本文件的内容。注意：会用新内容整体覆盖原有内容。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件的完整路径"},
                    "content": {"type": "string", "description": "写入的新内容（覆盖全部原有内容）"},
                },
                "required": ["path", "content"],
            },
            handler=_handle_edit_file,
        )
    )
