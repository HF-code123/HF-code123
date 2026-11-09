"""LLM 客户端 429/TPM 限流自动重试测试。"""

from __future__ import annotations

import asyncio
import types

import pytest

from core.config import LLMConfig
from core.llm import OpenAILLMClient


class FakeRateLimit(Exception):
    status_code = 429

    def __init__(self) -> None:
        super().__init__(
            "Error code: 429 - {'code': 50602, "
            "'message': 'Request was rejected due to rate limiting. "
            "Details: TPM limit reached.'}"
        )


class FakeStream:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def close(self) -> None:
        pass


def make_client(fail_times: int, exc: Exception) -> tuple[OpenAILLMClient, list]:
    calls: list[dict] = []

    async def create(**kwargs):
        calls.append(kwargs)
        if len(calls) <= fail_times:
            raise exc
        return FakeStream()

    stub = types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=create)
        )
    )
    config = LLMConfig(base_url="http://localhost", api_key="k", model="m")
    client = OpenAILLMClient(config, client=stub)
    client._retry_delays = (0.0, 0.0, 0.0)  # 测试不等真实时间
    return client, calls


def collect(client: OpenAILLMClient) -> list:
    async def run():
        return [item async for item in client.stream([{"role": "user", "content": "hi"}], [])]

    return asyncio.run(run())


def test_rate_limit_retries_then_succeeds() -> None:
    client, calls = make_client(2, FakeRateLimit())

    items = collect(client)

    assert len(calls) == 3  # 失败 2 次 + 成功 1 次
    assert items == []


def test_rate_limit_exhausted_retries_raises() -> None:
    client, calls = make_client(99, FakeRateLimit())

    with pytest.raises(FakeRateLimit):
        collect(client)
    # 1 次原始 + 3 次重试；不能因 stream_options 降级再翻一倍
    assert len(calls) == 4


def test_rate_limit_does_not_trigger_stream_options_fallback() -> None:
    client, calls = make_client(99, FakeRateLimit())

    with pytest.raises(FakeRateLimit):
        collect(client)

    assert all("stream_options" in kwargs for kwargs in calls)
    assert client._stream_options_supported is True


def test_stream_options_unsupported_still_falls_back() -> None:
    calls: list[dict] = []

    async def create(**kwargs):
        calls.append(kwargs)
        if "stream_options" in kwargs:
            raise TypeError("unexpected keyword argument 'stream_options'")
        return FakeStream()

    stub = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
    )
    config = LLMConfig(base_url="http://localhost", api_key="k", model="m")
    client = OpenAILLMClient(config, client=stub)
    client._retry_delays = (0.0, 0.0, 0.0)

    assert collect(client) == []
    assert len(calls) == 2  # 先带 stream_options 失败，再降级成功
    assert "stream_options" not in calls[1]
    assert client._stream_options_supported is False


def test_non_rate_limit_error_not_retried() -> None:
    client, calls = make_client(99, ValueError(" boom "))

    with pytest.raises(ValueError):
        collect(client)
    # 非限流异常只尝试一次（stream_options 降级路径同样不重试）
    assert len(calls) <= 2


def test_tpm_message_without_status_code_is_detected() -> None:
    client, calls = make_client(1, RuntimeError("TPM limit reached, slow down"))

    items = collect(client)

    assert len(calls) == 2
    assert items == []
