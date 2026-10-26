"""日期时间与闰年工具。"""

from __future__ import annotations

import platform
import subprocess
from typing import Any

from .registry import ToolRegistry, ToolSpec


def get_date() -> str:
    """调用本地 date 命令获取当前日期时间。"""
    command = "date /t & time /t" if platform.system() == "Windows" else "date"
    try:
        completed = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"获取日期时间失败: {exc}"

    raw = completed.stdout
    for encoding in ("utf-8", "gbk"):
        try:
            return " ".join(raw.decode(encoding).split())
        except UnicodeDecodeError:
            continue
    return " ".join(raw.decode("utf-8", errors="replace").split())


def is_leap_year(year: int) -> str:
    """用 Python 判断闰年：能被 4 整除且不能被 100 整除，或者能被 400 整除。"""
    year = int(year)
    if (year % 4 == 0 and year % 100 != 0) or year % 400 == 0:
        return f"{year} 年是闰年"
    return f"{year} 年不是闰年"


def _handle_get_date(arguments: dict[str, Any]) -> str:
    return get_date()


def _handle_is_leap_year(arguments: dict[str, Any]) -> str:
    return is_leap_year(int(arguments["year"]))


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="getDate",
            description="获取当前电脑本地的日期和时间。当用户询问今天是几号、现在几点、当前时间等问题时调用。",
            parameters={"type": "object", "properties": {}},
            handler=_handle_get_date,
        )
    )
    registry.register(
        ToolSpec(
            name="isLeapYear",
            description="判断某一年是否为闰年。当用户问某年是不是闰年、某年 2 月有没有 29 天时调用。",
            parameters={
                "type": "object",
                "properties": {
                    "year": {"type": "integer", "description": "要判断的年份，例如 2024"}
                },
                "required": ["year"],
            },
            handler=_handle_is_leap_year,
        )
    )
