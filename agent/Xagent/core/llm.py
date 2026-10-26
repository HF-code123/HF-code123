"""LLM 客户端适配层。

本模块是唯一直接依赖 openai SDK 的地方。它把 OpenAI 的流式 chunk
转换为与业务无关的 StreamText / StreamToolCalls，供 Agent 消费。
未来更换模型服务时，只需实现 LLMClientProtocol。

已知坑：部分模型（如 Qwen2.5 系列走第三方兼容端点）会把工具调用
以 <tool_call>{...}</tool_call> 的形式混在正文文本里流出来，
而不是走原生 tool_calls 字段。TextToolCallFilter 负责：
1. 把这类块从正文中剔除，避免界面显示原始 JSON；
2. 把块内容解析成 ToolCall，让工具照常执行。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from openai import AsyncOpenAI

from .config import LLMConfig

OPEN_TAG = "<tool_call>"
CLOSE_TAG = "</tool_call>"


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class StreamText:
    text: str


@dataclass(frozen=True)
class StreamToolCalls:
    calls: tuple[ToolCall, ...]


StreamItem = StreamText | StreamToolCalls


class TextToolCallFilter:
    """流式文本过滤器：剔除 <tool_call> 块并解析其中的工具调用。

    用法：逐段 feed() 模型正文，只把返回值交给下游展示；
    流结束后调用 finalize() 拿残余文本和解析出的 ToolCall 列表。
    标签跨 chunk 分裂的情况由内部缓存处理。
    """

    def __init__(self) -> None:
        self._buf = ""
        self._in_block = False
        self._block_content = ""
        self._calls: list[ToolCall] = []

    def feed(self, text: str) -> str:
        """输入一段正文，返回可安全展示的正文。"""
        self._buf += text
        out: list[str] = []
        while True:
            if self._in_block:
                end = self._buf.find(CLOSE_TAG)
                if end == -1:
                    # 保留可能是闭合标签前缀的尾巴，其余全部吞掉
                    keep = self._longest_tag_prefix_suffix(self._buf, CLOSE_TAG)
                    self._block_content += self._buf[: len(self._buf) - keep]
                    self._buf = self._buf[len(self._buf) - keep :]
                    break
                self._block_content += self._buf[:end]
                self._parse_block()
                self._in_block = False
                self._buf = self._buf[end + len(CLOSE_TAG) :]
                continue

            idx = self._buf.find("<")
            if idx == -1:
                # 没有标签嫌疑，但还要处理 '}' 残留模式：'}' + 空白 + </tool_call>
                return self._flush_or_hold_brace(out)
            out.append(self._buf[:idx])
            self._buf = self._buf[idx:]
            if self._buf.startswith(OPEN_TAG):
                self._in_block = True
                self._block_content = ""
                self._buf = self._buf[len(OPEN_TAG) :]
                continue
            if self._buf.startswith(CLOSE_TAG):
                # 服务端剥离不干净的孤立闭合标签，直接吞掉
                self._buf = self._buf[len(CLOSE_TAG) :]
                continue
            if OPEN_TAG.startswith(self._buf) or CLOSE_TAG.startswith(self._buf):
                # 可能是未到齐的标签，等下一个 chunk 再判断
                break
            # 普通字符 '<'，放行一个后继续扫描
            out.append(self._buf[0])
            self._buf = self._buf[1:]
        return "".join(out)

    def _flush_or_hold_brace(self, out: list[str]) -> str:
        """处理 '}' + 空白 + '</tool_call>' 的服务端残留。

        SiliconFlow 有时只把闭合标签留在 content 里（JSON 主体已转成
        原生 tool_calls），于是正文会多出一段 '}\\n'。遇到 '}' 时先扣下，
        确认后面跟的不是残留模式再放行；流结束时 finalize 会兜底放行。
        """
        while True:
            if not self._buf:
                break
            if self._buf[0] != "}":
                out.append(self._buf[0])
                self._buf = self._buf[1:]
                continue
            rest = self._buf[1:].lstrip()
            if rest.startswith(CLOSE_TAG):
                # 确认是残留：吞掉 '}'、空白和闭合标签
                consumed = len(self._buf) - len(rest)
                self._buf = self._buf[consumed + len(CLOSE_TAG) :]
                continue
            if rest == "" or CLOSE_TAG.startswith(rest):
                # 还无法判断，等下一个 chunk
                break
            out.append(self._buf[0])
            self._buf = self._buf[1:]
        return "".join(out)

    def finalize(self) -> tuple[str, list[ToolCall]]:
        """流结束：返回未展示完的残余文本和解析出的工具调用。"""
        tail = ""
        if self._in_block:
            # 块没闭合（模型截断），尝试解析已收集的内容
            self._parse_block()
        else:
            # 兜底放行：包括被扣下的 '}'（正常文本应以原文呈现）
            while self._buf and self._buf[0] == "}":
                tail += self._buf[0]
                self._buf = self._buf[1:]
                rest = self._buf.lstrip()
                if rest.startswith(CLOSE_TAG):
                    self._buf = rest[len(CLOSE_TAG) :]
                    break
            tail += self._buf
        self._buf = ""
        return tail, list(self._calls)

    def _parse_block(self) -> None:
        raw = self._block_content.strip()
        self._block_content = ""
        if not raw:
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return  # 不是合法 JSON，直接丢弃，不当正文也不当工具
        if not isinstance(data, dict) or not data.get("name"):
            return
        arguments = data.get("arguments", {})
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments, ensure_ascii=False)
        self._calls.append(
            ToolCall(
                id=f"textcall_{len(self._calls) + 1}",
                name=str(data["name"]),
                arguments=arguments or "{}",
            )
        )

    @staticmethod
    def _longest_tag_prefix_suffix(text: str, tag: str) -> int:
        """text 的最长后缀中，有多少字符恰好是 tag 的前缀。"""
        for size in range(min(len(text), len(tag) - 1), 0, -1):
            if tag.startswith(text[-size:]):
                return size
        return 0


class LLMClientProtocol(Protocol):
    @property
    def model_name(self) -> str: ...

    def stream(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
    ) -> AsyncIterator[StreamItem]: ...


class OpenAILLMClient:
    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        self._client = AsyncOpenAI(
            base_url=config.base_url,
            api_key=config.api_key,
            timeout=config.timeout,
        )

    @property
    def model_name(self) -> str:
        return self._config.model

    async def stream(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
    ) -> AsyncIterator[StreamItem]:
        kwargs: dict[str, Any] = {
            "model": self._config.model,
            "messages": list(messages),
            "stream": True,
        }
        if tools:
            kwargs["tools"] = list(tools)

        stream = await self._client.chat.completions.create(**kwargs)
        buffers: dict[int, dict[str, str]] = {}
        text_filter = TextToolCallFilter()
        try:
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                content = getattr(delta, "content", None)
                if content:
                    visible = text_filter.feed(content)
                    if visible:
                        yield StreamText(visible)

                for tool_call in getattr(delta, "tool_calls", None) or []:
                    slot = buffers.setdefault(
                        tool_call.index,
                        {"id": "", "name": "", "arguments": ""},
                    )
                    if tool_call.id:
                        slot["id"] = tool_call.id
                    function = getattr(tool_call, "function", None)
                    if function is None:
                        continue
                    if function.name:
                        slot["name"] += function.name
                    if function.arguments:
                        slot["arguments"] += function.arguments
        finally:
            close = getattr(stream, "close", None)
            if close is not None:
                await close()

        tail, text_calls = text_filter.finalize()
        if tail:
            yield StreamText(tail)

        calls = tuple(
            ToolCall(
                id=slot["id"] or f"call_{index}",
                name=slot["name"],
                arguments=slot["arguments"] or "{}",
            )
            for index, slot in sorted(buffers.items())
            if slot["name"]
        )
        if not calls and text_calls:
            # 模型没走原生 tool_calls，用从正文里解析出来的兜底
            calls = tuple(text_calls)
        if calls:
            yield StreamToolCalls(calls)
