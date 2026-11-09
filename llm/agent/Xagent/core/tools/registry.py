"""工具声明与执行注册表。

每个工具由 ToolSpec 描述，注册表负责：
1. 生成 OpenAI function calling 需要的 definitions；
2. 校验并解析模型返回的 JSON 参数（对小模型的常见 JSON 笔误做自动修复）；
3. 执行同步或异步 handler，并统一处理异常。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

ToolHandler = Callable[[dict[str, Any]], Awaitable[str] | str]

_FENCE_START_RE = re.compile(r"^```[a-zA-Z]*\s*")
_FENCE_END_RE = re.compile(r"\s*```\s*$")
# 相邻两个字符串之间缺逗号（如 {"a": "x" "b": "y"}）：闭合引号 + 任意空白（含零个）+ 新引号。
# 零空白（"x""y"）也必然是笔误：合法 JSON 里字符串闭合引号后紧跟开引号不存在
# （后跟的只可能是 , } ] :），所以这里放宽到 * 是安全的。
# 注意 + 必须用占有式语义防回溯爆炸——Python 无占有量词，改用先分块再匹配：
# 对超长 base64 内容，[^"\\] 逐字符匹配代价可接受（线性），无需特殊处理。
_MISSING_COMMA_RE = re.compile(r'("(?:[^"\\]|\\.)*")[ \t\r\n]*(?=")')


def _json_candidates(raw: str) -> list[str]:
    """生成候选解析文本：原文、去代码围栏版、截取首尾大括号版。"""

    stripped = (raw or "").strip()
    fenced = _FENCE_START_RE.sub("", _FENCE_END_RE.sub("", stripped)).strip()
    candidates: list[str] = []
    for base in dict.fromkeys((stripped, fenced)):
        if not base:
            continue
        candidates.append(base)
        start, end = base.find("{"), base.rfind("}")
        if start != -1 and end > start:
            trimmed = base[start : end + 1]
            if trimmed not in candidates:
                candidates.append(trimmed)
    return candidates


def parse_tool_arguments(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    """解析工具参数 JSON；失败时对 LLM 常见笔误做修复重试。

    覆盖三类高频问题：
    1. 参数外面套了 ```json 围栏或夹带说明文字；
    2. 字符串值里带未转义的真实换行/制表符（strict=False 容忍）；
    3. 相邻字段之间漏了逗号（{"a": "x" "b": "y"}）。
    返回 ``(参数字典, None)`` 或 ``(None, 错误说明)``。
    """

    last_error = "参数为空"
    for candidate in _json_candidates(raw):
        variants = dict.fromkeys(
            (candidate, _MISSING_COMMA_RE.sub(r"\1, ", candidate))
        )
        for text in variants:
            for options in ({}, {"strict": False}):
                try:
                    data = json.loads(text, **options)
                except json.JSONDecodeError as exc:
                    last_error = (
                        f"{exc.msg}（line {exc.lineno} column {exc.colno}）"
                    )
                    continue
                if isinstance(data, dict):
                    return data, None
                last_error = "参数必须是 JSON 对象"
    return None, (
        f"工具参数不是合法 JSON: {last_error}。"
        "已尝试自动修复（去围栏/补逗号/容忍换行）仍失败，"
        "请把参数写成一行合法 JSON 后重新调用"
    )


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler

    def definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        # 模型调用了不存在的工具时的补救提示（如把技能名当工具名）。
        # 返回 None 表示没有针对性建议；抛异常按无提示处理。
        self.unknown_hint: Callable[[str], str | None] | None = None

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"工具重复注册: {spec.name}")
        self._tools[spec.name] = spec

    def definitions(self) -> list[dict[str, Any]]:
        return [spec.definition() for spec in self._tools.values()]

    def infos(self) -> list[tuple[str, str]]:
        """全部工具的 (名称, 描述)，供界面工具清单展示。"""

        return [(spec.name, spec.description) for spec in self._tools.values()]

    async def execute_raw(self, name: str, raw_arguments: str) -> str:
        arguments, error = parse_tool_arguments(raw_arguments)
        if error:
            return error
        return await self.execute(name, arguments or {})

    async def execute(self, name: str, arguments: dict[str, Any]) -> str:
        spec = self._tools.get(name)
        if spec is None:
            message = (
                f"未知工具: {name}。可用工具: {', '.join(self._tools) or '（无）'}"
            )
            if self.unknown_hint is not None:
                try:
                    hint = self.unknown_hint(name)
                except Exception:
                    hint = None
                if hint:
                    message = f"{message}\n{hint}"
            return message
        # 必填参数缺失时明确告知缺什么，而不是让 handler 抛 KeyError
        properties = (spec.parameters or {}).get("properties", {})
        missing = [
            key
            for key in (spec.parameters or {}).get("required", ())
            if arguments.get(key) in (None, "")
        ]
        if missing:
            detail = "; ".join(
                f"{key}（{properties.get(key, {}).get('description', '必填')}）"
                for key in missing
            )
            # 缺失与空值是同一类问题：都明确告知「参数不能为空」并给出期望格式
            return (
                f"工具 {name} 缺少必需参数: {detail}。"
                f"这些参数不能为空，请补全后重新调用。"
            )
        try:
            if inspect.iscoroutinefunction(spec.handler):
                result = await spec.handler(arguments)
            else:
                # 文件读写、子进程等同步操作放到线程中，避免阻塞事件循环。
                result = await asyncio.to_thread(spec.handler, arguments)
        except Exception as exc:
            return f"工具 {name} 执行失败: {exc}"
        return str(result)
