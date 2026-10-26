from __future__ import annotations

import asyncio
import sys
from typing import Any

import pytest

from core.tools import ToolRegistry, build_default_registry
from core.tools import run_cli as module

PY = sys.executable


class Completed:
    """模拟 subprocess.CompletedProcess。"""

    def __init__(self, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _registry(**kwargs: Any) -> ToolRegistry:
    registry = ToolRegistry()
    module.register(registry, **kwargs)
    return registry


def _run(arguments: dict[str, Any], **kwargs: Any) -> str:
    return asyncio.run(_registry(**kwargs).execute("runCli", arguments))


# ------------------------------------------------------------------ 注册与声明


def test_registered_by_default() -> None:
    assert "runCli" in build_default_registry().names


def test_definition_declares_command_and_args() -> None:
    definition = next(
        item
        for item in build_default_registry().definitions()
        if item["function"]["name"] == "runCli"
    )
    parameters = definition["function"]["parameters"]

    assert set(parameters["required"]) == {"command", "args"}
    assert parameters["properties"]["command"]["type"] == "string"
    assert parameters["properties"]["args"]["type"] == "array"
    assert parameters["properties"]["args"]["items"]["type"] == "string"
    # shell=False 的前提是参数能逐项拆开，因此必须声明为数组
    assert isinstance(parameters["properties"]["args"], dict)


# ------------------------------------------------------------------ 真实执行


def test_stdout_and_exit_code_returned() -> None:
    result = _run({"command": PY, "args": ["-c", "print('hello runCli')"]})

    assert "退出码: 0" in result
    assert "标准输出:" in result
    assert "hello runCli" in result
    assert "标准错误:\n(空)" in result
    assert "提示:" not in result  # 成功时不做多余提示


def test_stderr_and_nonzero_exit_returned() -> None:
    result = _run(
        {
            "command": PY,
            "args": ["-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"],
        }
    )

    assert "退出码: 3" in result
    assert "标准错误:" in result
    assert "boom" in result
    assert "退出码非 0" in result


def test_long_output_is_truncated_with_marker() -> None:
    result = _run({"command": PY, "args": ["-c", "print('x' * 20000)"]})

    assert "已截断" in result
    assert "中间省略" in result
    assert len(result) < 20000


def test_timeout_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(argv, **kwargs):
        raise module.subprocess.TimeoutExpired(cmd=argv, timeout=1, output=b"partial", stderr=b"warn")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    result = asyncio.run(
        _registry(timeout=1).execute("runCli", {"command": "sleep", "args": ["10"]})
    )

    assert "执行超时" in result
    assert "退出码: 无" in result
    assert "partial" in result
    assert "warn" in result


# ------------------------------------------------------------------ 参数校验


def test_blank_command_rejected() -> None:
    assert "command 不能为空" in _run({"command": "   ", "args": []})


def test_full_command_line_rejected() -> None:
    result = _run({"command": "git --version", "args": []})

    assert "只能是程序名" in result
    assert "command='git'" in result
    assert "['--version']" in result


def test_args_string_rejected() -> None:
    assert "必须是字符串数组" in _run({"command": "git", "args": "--version"})


def test_args_missing_defaults_to_empty() -> None:
    assert module._parse_args(None) == ([], "")
    assert module._parse_args([]) == ([], "")
    assert module._parse_args(["-n", 3]) == (["-n", "3"], "")


def test_non_scalar_arg_rejected() -> None:
    result = _run({"command": "git", "args": [{"bad": 1}]})

    assert "非字符串元素" in result


def test_missing_program_reported() -> None:
    result = _run({"command": "definitely-not-a-real-cli-xyz-2026", "args": []})

    assert "找不到命令" in result
    assert "PATH" in result


# ------------------------------------------------------------------ 调用细节


def test_runs_with_argument_list_and_shell_false(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return Completed(stdout=b"ok")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    result = module.run_cli(
        {"command": "git", "args": ["log", "--oneline", "a b", "-n", 3]}
    )

    argv = captured["argv"]
    assert isinstance(argv, list)
    assert argv == ["git", "log", "--oneline", "a b", "-n", "3"]
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["timeout"] == module.DEFAULT_TIMEOUT
    assert "ok" in result


def test_gbk_output_is_decoded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module.locale, "getpreferredencoding", lambda do_setlocale=True: "gbk")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda argv, **kwargs: Completed(stdout="中文输出正常".encode("gbk")),
    )
    result = module.run_cli({"command": "ipconfig", "args": []})

    assert "中文输出正常" in result


def test_ansi_color_codes_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda argv, **kwargs: Completed(stdout=b"\x1b[31merror\x1b[0m: detail\r\n"),
    )
    result = module.run_cli({"command": "pytest", "args": []})

    assert "error: detail" in result
    assert "\x1b" not in result
