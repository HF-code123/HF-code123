"""文件写入工具：createFile / editFile / patchFile。"""

from __future__ import annotations

import base64
import binascii
import codecs
import re
from pathlib import Path
from typing import Any

from .registry import ToolRegistry, ToolSpec
from .text_utils import read_text_file

# patchFile 处理上限：超过后按「不是用来逐段改的文件」处理，避免把大文件读进内存
MAX_PATCH_BYTES = 8 * 1024 * 1024

# createFile 对这些扩展名做 base64 图片自动解码（模型经常拿到的是 base64 数据）
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp"}
_DATA_URI_RE = re.compile(r"\Adata:image/[a-zA-Z0-9.+-]+;base64,", re.IGNORECASE)
_BASE64_RE = re.compile(r"\A[A-Za-z0-9+/]+={0,2}\Z")


def _decode_base64_image(content: str) -> bytes | None:
    """把 base64 / data URI 文本解码成图片字节；不像 base64 时返回 None。"""

    text = content.strip()
    text = _DATA_URI_RE.sub("", text, count=1)
    compact = re.sub(r"\s+", "", text)
    if len(compact) < 24 or not _BASE64_RE.match(compact):
        return None
    try:
        return base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError):
        return None


def create_file(dir_path: str, filename: str, content: str = "") -> str:
    name = Path(filename)
    if not filename or name.name != filename:
        return f"文件名不合法: {filename}"

    p = Path(dir_path).expanduser() / filename
    if p.exists():
        return f"文件已存在，未创建: {p}"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        if name.suffix.lower() in _IMAGE_EXTS:
            decoded = _decode_base64_image(content)
            if decoded is not None:
                p.write_bytes(decoded)
                return f"已创建图片: {p}（{len(decoded)} 字节，base64 已解码）"
            p.write_text(content, encoding="utf-8")
            return (
                f"已创建文件: {p}（{len(content)} 字符）"
                "——警告：内容不是有效的 base64 图片数据，已按文本写入，"
                "文件不是有效图片；图片请传 base64（可带 data:image/...;base64, 前缀）"
            )
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


def patch_file(path: str, find: str, replace: str) -> str:
    """把文件中唯一一处 ``find`` 替换成 ``replace``（不动其余内容）。

    用于「文件很大、只需改某一段」的场景（例如在几十万字符的 HTML 模板里
    填充占位符）：相比 editFile 的整体覆盖，这里不需要模型把全文读进上下文。
    要求 ``find`` 在文件中唯一出现，既能防止误改，也能提示模型提供更长片段。
    """

    p = Path(path).expanduser()
    if not p.is_file():
        return f"文件不存在: {path}"
    if not find:
        return "参数 find 不能为空：请给出要替换的原文片段"

    try:
        raw = p.read_bytes()
    except OSError as exc:
        return f"读取失败: {exc}"
    if b"\x00" in raw:
        return f"不是纯文本文件，拒绝写入: {path}"
    if len(raw) > MAX_PATCH_BYTES:
        return f"文件过大（{len(raw)} 字节），请改用 runCli 中的脚本处理: {path}"

    has_bom = raw.startswith(codecs.BOM_UTF8)
    text = None
    for encoding in ("utf-8-sig", "gbk"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return f"不是纯文本文件，拒绝写入: {path}"

    # 统一成 \n 做匹配，写回时还原原始换行风格，避免整份文件被改行尾
    crlf = "\r\n" in text
    normalized = text.replace("\r\n", "\n")
    needle = find.replace("\r\n", "\n")
    replacement = replace.replace("\r\n", "\n")

    count = normalized.count(needle)
    if count == 0:
        preview = needle[:120].replace("\n", "⏎")
        return (
            f"文件中未找到要替换的内容，未做修改: {preview!r}。"
            "请先用 readFile 确认原文（注意缩进、引号与空格）"
        )
    if count > 1:
        return (
            f"要替换的内容在文件中出现 {count} 次，为避免误改已取消操作。"
            "请提供更长、能唯一定位的片段"
        )

    updated = normalized.replace(needle, replacement, 1)
    out = updated.replace("\n", "\r\n") if crlf else updated
    data = out.encode("utf-8")
    if has_bom:
        data = codecs.BOM_UTF8 + data
    try:
        p.write_bytes(data)
    except OSError as exc:
        return f"写入失败: {exc}"
    return (
        f"已修改: {p}（替换 1 处，{len(text)} → {len(updated)} 字符）"
    )


def _handle_create_file(arguments: dict[str, Any]) -> str:
    return create_file(
        str(arguments["dir"]),
        str(arguments["filename"]),
        str(arguments.get("content", "")),
    )


def _handle_edit_file(arguments: dict[str, Any]) -> str:
    return edit_file(str(arguments["path"]), str(arguments["content"]))


def _handle_patch_file(arguments: dict[str, Any]) -> str:
    return patch_file(
        str(arguments["path"]),
        str(arguments["find"]),
        str(arguments.get("replace", "")),
    )


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="createFile",
            description=(
                "在指定目录下用指定文件名创建新文件，可同时写入初始内容。"
                "写图片（.png/.jpg/.gif/.webp 等）时 content 传 base64 数据"
                "（可带 data:image/...;base64, 前缀，可含换行），会自动解码成二进制。"
            ),
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
    registry.register(
        ToolSpec(
            name="patchFile",
            description=(
                "局部修改纯文本文件：把文件中唯一出现的一处 find 原文替换成 "
                "replace，其余内容保持不变。适合大文件（几十万字符的 HTML/模板/"
                "代码）只改某几段，或往占位符处插入内容（find 传占位符、replace "
                "传占位符+新内容）。find 必须唯一出现，否则会取消操作并提示加长片段；"
                "替换为空字符串即删除该片段。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件的完整路径"},
                    "find": {
                        "type": "string",
                        "description": "要被替换的原文片段，必须在文件中唯一出现",
                    },
                    "replace": {
                        "type": "string",
                        "description": "替换成的新内容；传空字符串表示删除该片段",
                    },
                },
                "required": ["path", "find"],
            },
            handler=_handle_patch_file,
        )
    )
