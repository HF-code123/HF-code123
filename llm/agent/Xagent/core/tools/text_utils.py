"""纯文本文件判断与解码的公共逻辑。"""

from __future__ import annotations

from pathlib import Path


def read_text_file(path: str | Path) -> str:
    """读取纯文本文件，依次尝试 utf-8 / gbk 解码。"""
    raw = Path(path).read_bytes()
    if b"\x00" in raw:
        raise ValueError("不是纯文本文件")
    for encoding in ("utf-8-sig", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("不是纯文本文件")
