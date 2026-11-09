"""目录与文件查询工具：getCwd / listDir / getFileType / readFile。"""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Any

from .registry import ToolRegistry, ToolSpec
from .text_utils import read_text_file

MAX_PREVIEW_CHARS = 5000


def get_cwd() -> str:
    return f"当前工作目录: {os.getcwd()}"


def list_dir(path: str) -> str:
    p = Path(path).expanduser()
    if not p.is_dir():
        return f"目录不存在: {path}"

    items = sorted(p.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
    if not items:
        return f"{p} 是空目录"

    lines: list[str] = []
    for item in items:
        if item.is_dir():
            lines.append(f"[目录] {item.name}")
        else:
            lines.append(f"[文件] {item.name}（{item.stat().st_size} 字节）")
    return f"{p} 共 {len(items)} 项:\n" + "\n".join(lines)


def get_file_type(path: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"路径不存在: {path}"
    if p.is_dir():
        return f"{path} 是目录，不是文件"

    mime, encoding = mimetypes.guess_type(p.name)
    return (
        f"文件: {p.name}\n"
        f"扩展名: {p.suffix or '(无)'}\n"
        f"MIME: {mime or '(未知)'}\n"
        f"编码: {encoding or '(未知)'}"
    )


def read_file(path: str) -> str:
    p = Path(path).expanduser()
    if not p.is_file():
        return f"文件不存在: {path}"
    try:
        text = read_text_file(p)
    except (ValueError, OSError) as exc:
        return f"读取失败: {exc}"
    if len(text) > MAX_PREVIEW_CHARS:
        text = text[:MAX_PREVIEW_CHARS] + "\n...(内容过长已截断)"
    return f"文件内容:\n{text}"


def _handle_get_cwd(arguments: dict[str, Any]) -> str:
    return get_cwd()


def _handle_list_dir(arguments: dict[str, Any]) -> str:
    return list_dir(str(arguments["path"]))


def _handle_get_file_type(arguments: dict[str, Any]) -> str:
    return get_file_type(str(arguments["path"]))


def _handle_read_file(arguments: dict[str, Any]) -> str:
    return read_file(str(arguments["path"]))


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="getCwd",
            description="获取当前工作目录（程序运行时所在目录）的绝对路径。",
            parameters={"type": "object", "properties": {}},
            handler=_handle_get_cwd,
        )
    )
    registry.register(
        ToolSpec(
            name="listDir",
            description="列出指定目录下的所有文件和子目录。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目录路径，例如 D:\\data"}
                },
                "required": ["path"],
            },
            handler=_handle_list_dir,
        )
    )
    registry.register(
        ToolSpec(
            name="getFileType",
            description="获取指定文件的文件类型，返回扩展名、MIME 类型和文本编码。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件的完整路径"}
                },
                "required": ["path"],
            },
            handler=_handle_get_file_type,
        )
    )
    registry.register(
        ToolSpec(
            name="readFile",
            description="读取指定文件的文本内容，仅支持纯文本文件（如 .txt/.py/.md/.json）。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件的完整路径"}
                },
                "required": ["path"],
            },
            handler=_handle_read_file,
        )
    )
