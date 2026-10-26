"""工具声明与执行注册表。

每个工具由 ToolSpec 描述，注册表负责：
1. 生成 OpenAI function calling 需要的 definitions；
2. 校验并解析模型返回的 JSON 参数；
3. 执行同步或异步 handler，并统一处理异常。
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

ToolHandler = Callable[[dict[str, Any]], Awaitable[str] | str]


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

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"工具重复注册: {spec.name}")
        self._tools[spec.name] = spec

    def definitions(self) -> list[dict[str, Any]]:
        return [spec.definition() for spec in self._tools.values()]

    async def execute_raw(self, name: str, raw_arguments: str) -> str:
        try:
            arguments = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError as exc:
            return f"工具参数不是合法 JSON: {exc}"
        if not isinstance(arguments, dict):
            return "工具参数必须是 JSON 对象"
        return await self.execute(name, arguments)

    async def execute(self, name: str, arguments: dict[str, Any]) -> str:
        spec = self._tools.get(name)
        if spec is None:
            return f"未知工具: {name}"
        try:
            if inspect.iscoroutinefunction(spec.handler):
                result = await spec.handler(arguments)
            else:
                # 文件读写、子进程等同步操作放到线程中，避免阻塞事件循环。
                result = await asyncio.to_thread(spec.handler, arguments)
        except Exception as exc:
            return f"工具 {name} 执行失败: {exc}"
        return str(result)
