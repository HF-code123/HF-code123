"""Agent 编排层。

ChatAgent 是 core 层的应用服务：它把「对话上下文 + LLM 客户端 + 工具注册表」
编排成一轮完整的问答流程，并只通过 AgentEvent 与前端通信。

上下文管理：
- 每次模型响应后检查 Token 用量（优先 API usage，缺失时估算），
  达到窗口 70% 时发出 ContextWarning 并暂停接受新提示词；
- compact() 执行 /compact 流程：归档原始上下文 → 调 LLM 生成摘要 →
  用摘要替换历史（保留 System Prompt）→ 解除输入限制。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .context_manager import (
    ARCHIVE_NOTE_TEMPLATE,
    ArchiveError,
    ContextArchiver,
    build_compaction_messages,
    estimate_messages_tokens,
    estimate_tokens,
)
from .conversation import Conversation
from .events import (
    AgentError,
    AgentEvent,
    ContextWarning,
    TextDelta,
    ToolCallStarted,
    ToolResult,
    TurnCompleted,
)
from .llm import LLMClientProtocol, StreamText, StreamToolCalls, ToolCall
from .tools.registry import ToolRegistry

DEFAULT_CONTEXT_WINDOW = 128_000
CONTEXT_WARN_RATIO = 0.7

# /compact 过程日志：由组合根配置输出位置（默认 main.py 写入 .agents/logs/compact.log）
logger = logging.getLogger("xagent.compact")


@dataclass(frozen=True)
class ContextUsageSnapshot:
    """当前会话的上下文用量快照。"""

    tokens_used: int
    context_window: int
    window_is_default: bool
    warn_ratio: float = CONTEXT_WARN_RATIO

    @property
    def ratio(self) -> float:
        if self.context_window <= 0:
            return 0.0
        return self.tokens_used / self.context_window

    @property
    def blocked(self) -> bool:
        return self.ratio >= self.warn_ratio


@dataclass(frozen=True)
class CompactionResult:
    """一次 /compact 的执行结果。"""

    archive_id: str
    archive_path: Path
    messages_before: int
    tokens_before: int
    tokens_after: int
    # 供前端展示压缩对照面板：原始消息与生成的摘要
    summary: str = ""
    original_messages: tuple[dict[str, Any], ...] = ()


class ChatAgent:
    def __init__(
        self,
        llm: LLMClientProtocol,
        tools: ToolRegistry,
        *,
        system_prompt: str = "",
        max_tool_rounds: int = 8,
        context_window: int = DEFAULT_CONTEXT_WINDOW,
        context_window_is_default: bool = True,
        context_warning_ratio: float = CONTEXT_WARN_RATIO,
        archiver: ContextArchiver | None = None,
        project_context: str = "",
        skills: Any = None,
    ) -> None:
        if max_tool_rounds <= 0:
            raise ValueError("max_tool_rounds 必须大于 0")
        if not 0 < context_warning_ratio < 1:
            raise ValueError("context_warning_ratio 必须在 0 到 1 之间")
        self._llm = llm
        self._tools = tools
        self._conversation = Conversation(system_prompt)
        self._max_tool_rounds = max_tool_rounds
        self._context_window = max(1, context_window)
        self._window_is_default = context_window_is_default
        self._warn_ratio = context_warning_ratio
        self._archiver = archiver or ContextArchiver()
        self._context_blocked = False
        self._warned = False
        self._compacting = False
        self._last_api_usage: int | None = None
        # AGENTS.md 项目约定：一次注入，全程复用（reset 后重新注入）
        self._project_context = project_context.strip()
        # 技能注册表（可选）：仅供前端展示已发现的技能清单，Agent 本身不依赖
        self._skills = skills
        if self._project_context:
            self._inject_project_context()

    @property
    def model_name(self) -> str:
        return self._llm.model_name

    @property
    def tools(self) -> ToolRegistry:
        """工具注册表（供组合根注册 MCP 等外部工具、调试使用）。"""

        return self._tools

    @property
    def messages(self) -> list[dict[str, Any]]:
        """只读快照，便于调试、持久化或未来接入其他前端。"""

        return self._conversation.messages

    @property
    def skills(self) -> Any:
        """技能注册表（可能为 None）：供前端展示已发现的技能清单。"""

        return self._skills

    @property
    def skill_infos(self) -> list[tuple[str, str]]:
        """已发现技能的 (名称, 描述) 列表；无注册表时返回空列表。"""

        registry = self._skills
        if registry is None:
            return []
        try:
            return [
                (meta.name, meta.description) for meta in registry.skills
            ]
        except Exception:
            return []

    @property
    def compacting(self) -> bool:
        return self._compacting

    def context_usage(self) -> ContextUsageSnapshot:
        """当前上下文用量：优先 API usage，缺失时估算。"""

        used = self._last_api_usage
        if used is None:
            used = estimate_messages_tokens(self._conversation.messages)
        return ContextUsageSnapshot(
            tokens_used=used,
            context_window=self._context_window,
            window_is_default=self._window_is_default,
            warn_ratio=self._warn_ratio,
        )

    def reset(self) -> None:
        """清空对话并开启新会话：重新注入 AGENTS.md 项目约定。"""

        self._conversation.clear()
        if self._project_context:
            self._inject_project_context()
        self._context_blocked = False
        self._warned = False
        self._last_api_usage = None

    def _inject_project_context(self) -> None:
        """把 AGENTS.md 内容作为一条 user 消息注入历史开头。

        采用「一次注入，全程复用」策略：消息一旦进入对话历史就保持
        原样参与后续每一轮请求，保证提示词缓存有效性；同时随会话
        一起持久化到会话事件日志（.agents/sessions/）。
        """

        self._conversation.add_user(
            "【项目约定 AGENTS.md】\n"
            "以下是本项目的 AGENTS.md 约定内容，请在整个会话中遵守：\n\n"
            f"{self._project_context}"
        )

    def replace_history(self, messages: list[dict[str, Any]]) -> None:
        """用已有消息列表恢复会话历史（忽略其中的 system 消息）。

        恢复后保留当前 System Prompt 与 AGENTS.md 注入消息，适用于
        /resume 历史会话恢复场景。
        """

        restored = [m for m in messages if m.get("role") != "system"]
        self._conversation.replace(restored)
        self._context_blocked = False
        self._warned = False
        self._last_api_usage = None

    async def stream_reply(self, user_text: str) -> AsyncIterator[AgentEvent]:
        """处理一条用户消息，期间可能多次调用工具，直到模型给出最终回答。"""

        if self._compacting:
            yield AgentError("正在压缩上下文，请等待压缩完成后再发送新消息。")
            return
        if self._context_blocked:
            usage = self.context_usage()
            yield AgentError(
                f"上下文使用量已达 {usage.ratio:.0%}"
                f"（约 {usage.tokens_used / 1000:.1f}k / "
                f"{usage.context_window / 1000:.0f}k），已暂停接受新任务。"
                "请先执行 /compact 压缩上下文。"
            )
            return

        text = user_text.strip()
        if not text:
            return

        self._conversation.add_user(text)
        try:
            for _ in range(self._max_tool_rounds):
                content_parts: list[str] = []
                tool_calls: tuple[ToolCall, ...] = ()

                async for item in self._llm.stream(
                    self._conversation.messages,
                    self._tools.definitions(),
                ):
                    if isinstance(item, StreamText):
                        content_parts.append(item.text)
                        yield TextDelta(item.text)
                    elif isinstance(item, StreamToolCalls):
                        tool_calls = item.calls

                content = "".join(content_parts)
                usage = self._refresh_usage()
                if usage.blocked:
                    self._context_blocked = True
                    if not self._warned:
                        self._warned = True
                        yield ContextWarning(
                            tokens_used=usage.tokens_used,
                            context_window=usage.context_window,
                            ratio=usage.ratio,
                            window_is_default=usage.window_is_default,
                        )

                if not tool_calls:
                    self._conversation.add_assistant(content)
                    yield TurnCompleted(content)
                    return

                self._conversation.add_assistant(
                    content or None,
                    [self._tool_call_message(call) for call in tool_calls],
                )

                for call in tool_calls:
                    arguments, parse_error = _parse_arguments(call.arguments)
                    yield ToolCallStarted(
                        call_id=call.id,
                        name=call.name,
                        arguments=arguments,
                    )

                    if parse_error:
                        result = parse_error
                    else:
                        result = await self._tools.execute(call.name, arguments)

                    self._conversation.add_tool(call.id, result)
                    yield ToolResult(
                        call_id=call.id,
                        name=call.name,
                        result=result,
                    )

            yield AgentError(
                f"工具调用轮次超过上限（{self._max_tool_rounds} 轮），本轮已停止。"
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # 网络、鉴权、模型服务等异常统一交给前端展示
            yield AgentError(f"对话失败: {exc}")

    async def compact(self) -> CompactionResult:
        """执行 /compact：归档 → 压缩 → 替换上下文。

        压缩期间（_compacting）stream_reply 会拒绝新任务；
        任何一步失败都保留原有上下文并把异常抛给前端展示。
        """

        if self._compacting:
            raise RuntimeError("上下文压缩正在进行中，请勿重复触发。")

        self._compacting = True
        started_at = datetime.now()
        logger.info(
            "压缩开始: 消息 %d 条, 窗口 %d tokens",
            len(self._conversation.messages) - 1,  # 不含 system
            self._context_window,
        )
        try:
            usage_before = self.context_usage()
            old_messages = list(self._conversation.messages)

            # 1. 原始上下文必须成功归档后才能继续
            record = self._archiver.archive(old_messages)
            logger.info(
                "归档完成: 编号 %s, 文件 %s, 共 %d 条消息",
                record.id,
                record.path,
                len(old_messages),
            )

            # 2. 调用 LLM 生成摘要（不传工具，压缩期间不执行任何 Function Calling）
            summary_parts: list[str] = []
            async for item in self._llm.stream(
                build_compaction_messages(old_messages), []
            ):
                if isinstance(item, StreamText):
                    summary_parts.append(item.text)
            summary = "".join(summary_parts).strip()
            if not summary:
                logger.warning("模型未返回压缩摘要，中止压缩并保留原有上下文")
                raise RuntimeError(
                    "模型没有返回压缩摘要，已保留原有上下文，请重试 /compact。"
                )
            logger.info("摘要生成完成: %d 字符（约 %d tokens）", len(summary), estimate_tokens(summary))

            # 3. 用摘要替换历史：保留 System Prompt，摘要作为继续工作的起点
            archive_note = ARCHIVE_NOTE_TEMPLATE.format(
                time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                id=record.id,
            )
            new_conversation = Conversation(self._conversation.system_prompt)
            new_conversation.add_user(
                "【上下文压缩摘要】以下是此前会话的工作摘要，"
                "请基于它继续当前任务：\n\n"
                f"{summary}\n\n---\n{archive_note}"
            )
            self._conversation = new_conversation

            # 4. 解除输入限制，重置警告状态
            self._context_blocked = False
            self._warned = False
            self._last_api_usage = None

            tokens_after = self.context_usage().tokens_used
            logger.info(
                "压缩完成: 归档编号 %s, %s -> %s tokens, 耗时 %.1f 秒",
                record.id,
                usage_before.tokens_used,
                tokens_after,
                (datetime.now() - started_at).total_seconds(),
            )

            return CompactionResult(
                archive_id=record.id,
                archive_path=record.path,
                messages_before=usage_before.tokens_used,
                tokens_before=usage_before.tokens_used,
                tokens_after=tokens_after,
                summary=summary,
                original_messages=tuple(old_messages),
            )
        except ArchiveError as exc:
            logger.error("上下文归档失败: %s", exc)
            raise RuntimeError(f"上下文归档失败，已保留原有上下文: {exc}") from exc
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("上下文压缩失败，已保留原有上下文")
            raise RuntimeError(f"上下文压缩失败，已保留原有上下文: {exc}") from exc
        finally:
            self._compacting = False

    def _refresh_usage(self) -> ContextUsageSnapshot:
        """模型响应结束后刷新用量：优先 API usage，缺失时估算。"""

        api_usage = getattr(self._llm, "last_usage_tokens", None)
        if isinstance(api_usage, int) and api_usage > 0:
            self._last_api_usage = api_usage
        return self.context_usage()

    @staticmethod
    def _tool_call_message(call: ToolCall) -> dict[str, Any]:
        return {
            "id": call.id,
            "type": "function",
            "function": {"name": call.name, "arguments": call.arguments},
        }


def _parse_arguments(raw: str) -> tuple[dict[str, Any], str | None]:
    try:
        arguments = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        return {}, f"工具参数不是合法 JSON: {exc}"
    if not isinstance(arguments, dict):
        return {}, "工具参数必须是 JSON 对象"
    return arguments, None
