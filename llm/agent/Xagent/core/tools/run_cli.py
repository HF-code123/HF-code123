"""本地命令行执行工具：runCli。

用途：让 Agent 调用本机已安装的任意命令行程序，例如::

    {"command": "git", "args": ["--version"]}

实现要点（教学版）：
- command 与 args 拼装成列表后交给 subprocess，``shell=False``，不拼接 shell 字符串；
- 同时回传 stdout / stderr / 退出码，模型据此判断成功与否并决定下一步；
- 固定超时 30 秒，超时会终止进程并把已产生的输出一并返回；
- 单段输出超过 6000 字时保留首尾并标明省略了多少字，避免撑爆上下文。

安全提示：本阶段为教学实验环境，未实现白名单 / 黑名单 / 审批机制，
命令以运行 xAgent 的当前用户权限执行。请勿在生产或不可信输入场景直接使用。
"""

from __future__ import annotations

import locale
import os
import re
import subprocess
from typing import Any

from .registry import ToolRegistry, ToolSpec

DEFAULT_TIMEOUT = 30
MAX_OUTPUT_CHARS = 6000
HEAD_CHARS = 4000
TAIL_CHARS = 2000

# ANSI 颜色/光标控制序列，例如 \x1b[31m、\x1b[0m
ANSI_PATTERN = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "description": (
                "要执行的程序名或可执行文件路径，例如 git、python、npm、C:\\\\Windows\\\\System32\\\\ipconfig.exe。"
                "只填程序本身，不要带参数；命令在 xAgent 的当前工作目录下执行。"
            ),
        },
        "args": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "传给程序的参数列表，逐项拆开，例如 [\"--version\"]、[\"status\", \"--short\"]。"
                "没有参数时传空数组 []。"
            ),
        },
    },
    "required": ["command", "args"],
}


def register(registry: ToolRegistry, *, runner: Any = None, timeout: int = DEFAULT_TIMEOUT) -> None:
    """注册 runCli 工具。

    runner / timeout 仅用于测试注入；不传时使用本模块的 subprocess 实现。
    """
    registry.register(
        ToolSpec(
            name="runCli",
            description=(
                "在本机执行已安装的命令行程序，并返回标准输出、标准错误和退出码。"
                "当用户要求运行命令、查看某工具版本、执行 git/npm/pip 等操作时使用。"
                "command 只填程序名，参数逐项放进 args 数组；不支持管道、重定向、通配符等 shell 语法，"
                "需要时请显式调用 cmd /c 或 bash -c。"
                "命令默认 30 秒超时，过长输出会被截断并标注。"
            ),
            parameters=PARAMETERS,
            handler=lambda arguments: run_cli(arguments, runner=runner, timeout=timeout),
        )
    )


def run_cli(
    arguments: dict[str, Any],
    *,
    runner: Any = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    command = arguments.get("command", "")
    if not isinstance(command, str):
        return "执行失败: command 必须是字符串，例如 \"git\"。"
    command = command.strip()
    if not command:
        return "执行失败: command 不能为空，请提供要执行的程序名，例如 git。"

    args, error = _parse_args(arguments.get("args"))
    if error:
        return error

    if _looks_like_command_line(command):
        return (
            "执行失败: command 只能是程序名或可执行文件路径，不能把参数写在里面；"
            f"请把参数逐项放进 args。收到 command={command!r}，"
            f"可改为 command={command.split()[0]!r}, args={command.split()[1:]!r}。"
        )

    argv = [command, *args]
    try:
        completed = (runner or _run_subprocess)(argv, timeout)
    except FileNotFoundError:
        return (
            f"执行失败: 找不到命令 {command!r}。"
            "请确认该程序已安装并在系统 PATH 中；Windows 下可改用完整路径，例如 "
            "\"C:\\\\Windows\\\\System32\\\\ipconfig.exe\"。"
        )
    except PermissionError:
        return f"执行失败: 没有权限执行 {command!r}（可能不是可执行文件，或被系统策略拦截）。"
    except subprocess.TimeoutExpired as exc:
        return _format_result(
            argv,
            exit_code=None,
            stdout=_clean(_decode(exc.stdout)),
            stderr=_clean(_decode(exc.stderr)),
            status=f"执行超时，已超过 {timeout} 秒并被终止",
            hint="提示: 命令长时间无响应，可改用更省时的参数或分步执行。",
        )
    except OSError as exc:
        return f"执行失败: 启动 {command!r} 时出错（{exc}）。"

    return _format_result(
        argv,
        exit_code=completed.returncode,
        stdout=_clean(_decode(completed.stdout)),
        stderr=_clean(_decode(completed.stderr)),
    )


# ------------------------------------------------------------------ 参数校验


def _parse_args(raw: Any) -> tuple[list[str], str]:
    """把模型给的 args 规整成字符串列表；不合法时返回错误说明。"""

    if raw is None:
        return [], ""
    if isinstance(raw, str):
        return [], (
            "执行失败: args 必须是字符串数组，不能是单个字符串；"
            f"请写成 [\"--version\"] 这样的列表。收到: {raw!r}"
        )
    if not isinstance(raw, (list, tuple)):
        return [], f"执行失败: args 必须是字符串数组，收到的是 {type(raw).__name__}。"

    items: list[str] = []
    for item in raw:
        if isinstance(item, (list, tuple, dict)):
            return [], f"执行失败: args 中存在非字符串元素 {item!r}，每个参数都必须是字符串。"
        if item is None:
            items.append("")
        elif isinstance(item, bool):
            items.append("true" if item else "false")
        else:
            items.append(str(item))
    return items, ""


def _looks_like_command_line(command: str) -> bool:
    """识别「把整条命令塞进 command」的常见误用。

    完整路径本身可能含空格（C:\\Program Files\\...），所以只要路径真实存在就放行。
    """

    if not any(ch.isspace() for ch in command):
        return False
    if os.path.isfile(command):
        return False
    return True


# ------------------------------------------------------------------ 子进程


def _run_subprocess(argv: list[str], timeout: int) -> subprocess.CompletedProcess[bytes]:
    """以参数列表 + shell=False 执行，避免任何 shell 解析与注入。"""

    return subprocess.run(
        argv,
        shell=False,
        capture_output=True,
        timeout=timeout,
        check=False,  # 退出码由工具回传给模型，不在这里抛异常
    )


def _decode(raw: bytes | str | None) -> str:
    """CLI 输出编码不统一：中文 Windows 常见 GBK，先按 UTF-8 再按本地代码页兜底。"""

    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    try:
        preferred = locale.getpreferredencoding(False)
    except Exception:  # locale 信息缺失时忽略
        preferred = ""
    encodings = ["utf-8", preferred, "gbk", "gb18030"]
    for encoding in dict.fromkeys(item for item in encodings if item):
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def _clean(text: str) -> str:
    """去掉 ANSI 颜色码、统一换行、压缩尾部空白。"""

    text = ANSI_PATTERN.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _truncate(text: str) -> str:
    """超长输出保留首尾：开头常有标题/进度，结尾常有报错与统计。"""

    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    head = text[:HEAD_CHARS].rstrip()
    tail = text[-TAIL_CHARS:].lstrip()
    omitted = len(text) - len(head) - len(tail)
    marker = (
        f"…（已截断：原始输出 {len(text)} 字，中间省略 {omitted} 字，"
        f"保留开头 {HEAD_CHARS} 字与结尾 {TAIL_CHARS} 字）…"
    )
    return f"{head}\n\n{marker}\n\n{tail}"


def _format_result(
    argv: list[str],
    *,
    exit_code: int | None,
    stdout: str,
    stderr: str,
    status: str = "",
    hint: str = "",
) -> str:
    """统一回传格式：命令、退出码、stdout、stderr，便于模型判断执行结果。"""

    lines = [
        f"命令: {subprocess.list2cmdline(argv)}",
        "退出码: 无（进程未正常结束）" if exit_code is None else f"退出码: {exit_code}",
    ]
    if status:
        lines.append(f"状态: {status}")
    lines.append("标准输出:")
    lines.append(_truncate(stdout) if stdout else "(空)")
    lines.append("标准错误:")
    lines.append(_truncate(stderr) if stderr else "(空)")

    if exit_code is not None and exit_code != 0:
        hint = hint or "提示: 退出码非 0 通常表示执行失败，请结合标准错误判断原因。"
    if hint:
        lines.append(hint)
    return "\n".join(lines)
