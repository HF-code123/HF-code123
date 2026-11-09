"""上下文压缩 / 归档 / 检索功能测试。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from core.agent import ChatAgent
from core.context_manager import (
    MAX_MATCHES,
    ARCHIVE_NOTE_TEMPLATE,
    ContextArchiver,
    build_compaction_messages,
    estimate_messages_tokens,
    estimate_tokens,
)
from core.events import AgentError, ContextWarning, TurnCompleted
from core.llm import StreamText
from core.tools import ToolRegistry
from core.tools.recall_tools import TOOL_NAME as RECALL_TOOL_NAME
from core.tools.recall_tools import register as register_recall_tool


class FakeLLM:
    """可编程假模型：依次返回每轮输出，可模拟 API usage（单个值或逐次序列）。"""

    def __init__(self, turns: list, usage=None) -> None:
        self._turns = list(turns)
        self._usage_plan = usage
        self._usage_index = 0
        self.last_usage_tokens = None
        self.calls: list[list[dict]] = []

    @property
    def model_name(self) -> str:
        return "fake-model"

    def _next_usage(self):
        if isinstance(self._usage_plan, list):
            value = (
                self._usage_plan[self._usage_index]
                if self._usage_index < len(self._usage_plan)
                else None
            )
            self._usage_index += 1
            return value
        return self._usage_plan

    async def stream(self, messages, tools):
        self.calls.append(list(messages))
        self.last_usage_tokens = self._next_usage()
        for item in self._turns.pop(0):
            yield item


def make_llm(text: str = "压缩摘要内容", usage: int | None = None) -> FakeLLM:
    from core.llm import StreamText

    return FakeLLM(turns=[[StreamText(text)]], usage=usage)


async def collect(agent: ChatAgent, text: str) -> list:
    return [event async for event in agent.stream_reply(text)]


class TestTokenEstimation:
    def test_cjk_heavy(self) -> None:
        text = "你好世界" * 10  # 40 个汉字
        assert 35 <= estimate_tokens(text) <= 45

    def test_ascii_heavy(self) -> None:
        text = "a" * 400
        assert 90 <= estimate_tokens(text) <= 110

    def test_empty(self) -> None:
        assert estimate_tokens("") == 0

    def test_messages_estimate_includes_all_messages(self) -> None:
        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "你好"},
        ]
        assert estimate_messages_tokens(messages) > estimate_tokens("你好")


class TestArchiver:
    def test_archive_writes_jsonl(self, tmp_path: Path) -> None:
        archiver = ContextArchiver(tmp_path / "context")
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "回答", "tool_calls": []},
        ]
        record = archiver.archive(messages)

        assert record.message_count == 3
        assert record.path.name.endswith(".jsonl")
        assert record.id == record.path.stem
        lines = record.path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3
        assert json.loads(lines[1])["content"] == "问题"

    def test_search_by_keyword(self, tmp_path: Path) -> None:
        archiver = ContextArchiver(tmp_path / "context")
        archiver.archive(
            [
                {"role": "user", "content": "需要配置数据库连接 postgres://x"},
                {"role": "assistant", "content": "好的，已完成设置"},
            ]
        )
        matches = archiver.search("postgres")
        assert len(matches) == 1
        assert matches[0].role == "user"
        assert "postgres://x" in matches[0].snippet

    def test_search_specific_id(self, tmp_path: Path) -> None:
        archiver = ContextArchiver(tmp_path / "context")
        first = archiver.archive([{"role": "user", "content": "关键词甲"}])
        archiver.archive([{"role": "user", "content": "关键词乙"}])

        matches = archiver.search("关键词", first.id)
        assert len(matches) == 1
        assert "甲" in matches[0].snippet

    def test_search_invalid_id_rejected(self, tmp_path: Path) -> None:
        archiver = ContextArchiver(tmp_path / "context")
        with pytest.raises(ValueError, match="不合法"):
            archiver.search("x", "../evil")

    def test_search_caps_results(self, tmp_path: Path) -> None:
        archiver = ContextArchiver(tmp_path / "context")
        messages = [
            {"role": "user", "content": f"重复关键词 第{i}条"}
            for i in range(MAX_MATCHES + 10)
        ]
        archiver.archive(messages)
        matches = archiver.search("关键词")
        assert len(matches) == MAX_MATCHES

    def test_search_snippet_bounded(self, tmp_path: Path) -> None:
        archiver = ContextArchiver(tmp_path / "context")
        long_text = "前缀" * 500 + "针脚" + "后缀" * 500
        archiver.archive([{"role": "user", "content": long_text}])
        matches = archiver.search("针脚")
        assert len(matches) == 1
        assert len(matches[0].snippet) < 400

    def test_empty_query_returns_empty(self, tmp_path: Path) -> None:
        archiver = ContextArchiver(tmp_path / "context")
        assert archiver.search("  ") == []


class TestCompactionPrompt:
    def test_build_messages_uses_system_role_for_prompt(self) -> None:
        messages = [{"role": "user", "content": "历史内容"}]
        built = build_compaction_messages(messages)
        assert built[0]["role"] == "system"
        assert "上下文管理助手" in built[0]["content"]
        assert "recallContext" in built[0]["content"]
        assert "历史内容" in built[1]["content"]

    def test_archive_note_template(self) -> None:
        note = ARCHIVE_NOTE_TEMPLATE.format(time="2026-10-22 15:30:00", id="20261022-153000")
        assert "recallContext" in note
        assert "20261022-153000" in note


def _agent(tmp_path: Path, llm: FakeLLM, **kwargs) -> ChatAgent:
    archiver = ContextArchiver(tmp_path / "context")
    return ChatAgent(
        llm, ToolRegistry(), system_prompt="系统提示词", archiver=archiver, **kwargs
    )


class TestCompactionFlow:
    def test_compact_replaces_history_keeps_system_prompt(
        self, tmp_path: Path
    ) -> None:
        llm = FakeLLM(
            turns=[
                [StreamText("先回答一")],
                [StreamText("这是压缩摘要")],
            ]
        )
        agent = _agent(tmp_path, llm)
        asyncio.run(collect(agent, "第一个问题"))

        result = asyncio.run(agent.compact())

        assert result.archive_id in [p.stem for p in (tmp_path / "context").glob("*.jsonl")]
        assert agent.messages[0]["role"] == "system"
        assert agent.messages[0]["content"] == "系统提示词"
        history = agent.messages[1:]
        assert len(history) == 1
        assert "这是压缩摘要" in history[0]["content"]
        assert "recallContext" in history[0]["content"]
        assert f"{result.archive_id}" in history[0]["content"]
        # 解除限制
        assert agent.context_usage().blocked is False

    def test_compact_failure_keeps_old_context(self, tmp_path: Path) -> None:
        class BrokenLLM(FakeLLM):
            async def stream(self, messages, tools):
                self.calls.append(list(messages))
                raise RuntimeError("模型服务不可用")
                yield  # pragma: no cover

        llm = BrokenLLM(turns=[])
        agent = _agent(tmp_path, llm)
        asyncio.run(collect(agent, "第一个问题"))
        old_messages = list(agent.messages)

        with pytest.raises(RuntimeError, match="已保留原有上下文"):
            asyncio.run(agent.compact())

        assert agent.messages == old_messages

    def test_compact_empty_summary_keeps_context(self, tmp_path: Path) -> None:
        llm = FakeLLM(
            turns=[
                [StreamText("回答")],
                [StreamText("   ")],  # 压缩阶段返回空
            ]
        )
        agent = _agent(tmp_path, llm)
        asyncio.run(collect(agent, "问题"))

        with pytest.raises(RuntimeError, match="没有返回压缩摘要"):
            asyncio.run(agent.compact())
        assert len(agent.messages) > 1

    def test_blocked_after_threshold_and_released_by_compact(
        self, tmp_path: Path
    ) -> None:
        # 窗口 1000：首次响应 usage 900 → 超过 70%；压缩调用与后续恢复为未知/低用量
        llm = FakeLLM(
            turns=[
                [StreamText("回答")],
                [StreamText("这是压缩摘要")],
                [StreamText("新任务的回答")],
            ],
            usage=[900, None, None],
        )
        agent = _agent(tmp_path, llm, context_window=1000)
        events = asyncio.run(collect(agent, "问题"))

        assert any(isinstance(e, ContextWarning) for e in events)
        assert agent.context_usage().blocked is True

        # 阈值后拒绝新任务
        blocked_events = asyncio.run(collect(agent, "再问一个"))
        assert any(isinstance(e, AgentError) and "/compact" in e.message for e in blocked_events)

        # /compact 后解除
        asyncio.run(agent.compact())
        assert agent.context_usage().blocked is False
        ok_events = asyncio.run(collect(agent, "新任务"))
        assert any(isinstance(e, TurnCompleted) for e in ok_events)

    def test_no_warning_below_threshold(self, tmp_path: Path) -> None:
        llm = FakeLLM(turns=[[StreamText("回答")]], usage=100)
        agent = _agent(tmp_path, llm, context_window=1000)
        events = asyncio.run(collect(agent, "问题"))
        assert not any(isinstance(e, ContextWarning) for e in events)

    def test_compacting_rejects_new_tasks(self, tmp_path: Path) -> None:
        llm = make_llm()
        agent = _agent(tmp_path, llm)
        agent._compacting = True
        events = asyncio.run(collect(agent, "问题"))
        assert any("正在压缩" in e.message for e in events if isinstance(e, AgentError))

    def test_api_usage_preferred_over_estimate(self, tmp_path: Path) -> None:
        llm = FakeLLM(turns=[[StreamText("回答")]], usage=1234)
        agent = _agent(tmp_path, llm, context_window=10000)
        asyncio.run(collect(agent, "问题"))
        assert agent.context_usage().tokens_used == 1234


class TestRecallTool:
    def _registry(self, tmp_path: Path) -> ToolRegistry:
        registry = ToolRegistry()
        register_recall_tool(registry, ContextArchiver(tmp_path / "context"))
        return registry

    def test_registered(self, tmp_path: Path) -> None:
        assert RECALL_TOOL_NAME in self._registry(tmp_path).names

    def test_finds_archived_content(self, tmp_path: Path) -> None:
        registry = self._registry(tmp_path)
        ContextArchiver(tmp_path / "context").archive(
            [
                {"role": "user", "content": "数据库连接串是 postgres://secret"},
                {"role": "assistant", "content": "收到"},
            ]
        )
        result = asyncio.run(
            registry.execute(RECALL_TOOL_NAME, {"text": "postgres"})
        )
        assert "postgres://secret" in result
        assert "归档" in result

    def test_search_specific_id(self, tmp_path: Path) -> None:
        registry = self._registry(tmp_path)
        archiver = ContextArchiver(tmp_path / "context")
        first = archiver.archive([{"role": "user", "content": "目标信息甲"}])
        archiver.archive([{"role": "user", "content": "目标信息乙"}])

        result = asyncio.run(
            registry.execute(RECALL_TOOL_NAME, {"text": "目标信息", "id": first.id})
        )
        assert "甲" in result
        assert "乙" not in result

    def test_invalid_id_reported(self, tmp_path: Path) -> None:
        registry = self._registry(tmp_path)
        result = asyncio.run(
            registry.execute(RECALL_TOOL_NAME, {"text": "x", "id": "../evil"})
        )
        assert "检索失败" in result

    def test_no_match_message(self, tmp_path: Path) -> None:
        registry = self._registry(tmp_path)
        ContextArchiver(tmp_path / "context").archive(
            [{"role": "user", "content": "别的内容"}]
        )
        result = asyncio.run(
            registry.execute(RECALL_TOOL_NAME, {"text": "不存在的词"})
        )
        assert "未找到" in result

    def test_empty_text_rejected(self, tmp_path: Path) -> None:
        registry = self._registry(tmp_path)
        result = asyncio.run(registry.execute(RECALL_TOOL_NAME, {"text": ""}))
        assert "不能为空" in result


class TestCompactLoggingAndResult:
    """/compact 过程日志与压缩结果对照数据。"""

    def test_result_carries_summary_and_original(self, tmp_path: Path) -> None:
        llm = FakeLLM(
            turns=[[StreamText("回答")], [StreamText("这是压缩摘要")]]
        )
        agent = _agent(tmp_path, llm)
        asyncio.run(collect(agent, "第一个问题"))

        result = asyncio.run(agent.compact())

        assert result.summary == "这是压缩摘要"
        roles = [m["role"] for m in result.original_messages]
        assert roles == ["system", "user", "assistant"]
        assert result.original_messages[1]["content"] == "第一个问题"

    def test_compact_writes_log(self, tmp_path: Path) -> None:
        import logging

        log_file = tmp_path / "compact.log"
        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        compact_logger = logging.getLogger("xagent.compact")
        compact_logger.addHandler(handler)
        compact_logger.setLevel(logging.INFO)

        llm = FakeLLM(
            turns=[[StreamText("回答")], [StreamText("这是压缩摘要")]]
        )
        agent = _agent(tmp_path, llm)
        asyncio.run(collect(agent, "第一个问题"))
        asyncio.run(agent.compact())

        for h in list(compact_logger.handlers):
            h.flush()
            compact_logger.removeHandler(h)
            h.close()

        text = log_file.read_text(encoding="utf-8")
        assert "压缩开始" in text
        assert "归档完成" in text
        assert "摘要生成完成" in text
        assert "压缩完成" in text
        assert "INFO" in text

    def test_compact_failure_logged(self, tmp_path: Path) -> None:
        import logging

        class BrokenLLM(FakeLLM):
            async def stream(self, messages, tools):
                self.calls.append(list(messages))
                raise RuntimeError("模型服务不可用")
                yield  # pragma: no cover

        records: list[logging.LogRecord] = []
        compact_logger = logging.getLogger("xagent.compact")
        handler = logging.Handler()
        handler.emit = records.append
        compact_logger.addHandler(handler)
        compact_logger.setLevel(logging.INFO)

        agent = _agent(tmp_path, BrokenLLM(turns=[]))
        with pytest.raises(RuntimeError):
            asyncio.run(agent.compact())

        compact_logger.removeHandler(handler)
        messages = [r.getMessage() for r in records]
        assert any("压缩开始" in m for m in messages)
        assert any("压缩失败" in m for m in messages)
