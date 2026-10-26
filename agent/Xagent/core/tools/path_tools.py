"""目录与路径管理工具：createDir / deleteDir / deleteFile / renamePath。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .registry import ToolRegistry, ToolSpec


def create_dir(path: str) -> str:
    p = Path(path).expanduser()
    if p.is_dir():
        return f"目录已存在: {p}"
    if p.exists():
        return f"路径已存在且不是目录: {p}"
    try:
        p.mkdir(parents=True)
    except OSError as exc:
        return f"创建目录失败: {exc}"
    return f"已创建目录: {p}"


def delete_dir(path: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"目录不存在: {path}"
    if not p.is_dir():
        return f"不是目录，拒绝删除: {path}"
    if p.resolve() == Path(p.anchor):
        return f"拒绝删除根目录: {p}"
    if p.resolve() == Path.cwd().resolve():
        return f"拒绝删除当前工作目录: {p}"
    try:
        shutil.rmtree(p)
    except OSError as exc:
        return f"删除目录失败: {exc}"
    return f"已删除目录及其全部内容: {p}"


def delete_file(path: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"文件不存在: {path}"
    if not p.is_file():
        return f"不是文件，拒绝删除: {path}"
    try:
        p.unlink()
    except OSError as exc:
        return f"删除文件失败: {exc}"
    return f"已删除文件: {p}"


def rename_path(old: str, new: str) -> str:
    o = Path(old).expanduser()
    n = Path(new).expanduser()
    if not o.exists():
        return f"源路径不存在: {old}"
    if n.exists():
        return f"目标已存在，未重命名: {new}"
    try:
        n.parent.mkdir(parents=True, exist_ok=True)
        o.rename(n)
    except OSError as exc:
        return f"重命名失败: {exc}"
    return f"已重命名: {o} -> {n}"


def _handle_create_dir(arguments: dict[str, Any]) -> str:
    return create_dir(str(arguments["path"]))


def _handle_delete_dir(arguments: dict[str, Any]) -> str:
    return delete_dir(str(arguments["path"]))


def _handle_delete_file(arguments: dict[str, Any]) -> str:
    return delete_file(str(arguments["path"]))


def _handle_rename_path(arguments: dict[str, Any]) -> str:
    return rename_path(str(arguments["old"]), str(arguments["new"]))


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="createDir",
            description="创建新目录，父目录不存在时会一并创建。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要创建的目录路径"}
                },
                "required": ["path"],
            },
            handler=_handle_create_dir,
        )
    )
    registry.register(
        ToolSpec(
            name="deleteDir",
            description="删除指定目录及其内部所有内容（目录可以非空）。危险操作。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要删除的目录路径"}
                },
                "required": ["path"],
            },
            handler=_handle_delete_dir,
        )
    )
    registry.register(
        ToolSpec(
            name="deleteFile",
            description="删除指定文件。危险操作。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要删除的文件完整路径"}
                },
                "required": ["path"],
            },
            handler=_handle_delete_file,
        )
    )
    registry.register(
        ToolSpec(
            name="renamePath",
            description="重命名文件或目录，也可以借此把文件/目录移动到另一个路径。",
            parameters={
                "type": "object",
                "properties": {
                    "old": {"type": "string", "description": "原路径"},
                    "new": {"type": "string", "description": "新路径（含新名字）"},
                },
                "required": ["old", "new"],
            },
            handler=_handle_rename_path,
        )
    )
