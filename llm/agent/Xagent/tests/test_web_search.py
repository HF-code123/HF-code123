from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from core.config import SearchConfig
from core.tools import ToolRegistry, build_default_registry
from core.tools import web_search


def _registry(handler: httpx.MockTransport) -> ToolRegistry:
    registry = ToolRegistry()
    web_search.register(
        registry, SearchConfig(api_key="test-key", model="ernie-test", top_k=3),
        transport=handler,
    )
    return registry


def _ai_answer(requests: list[httpx.Request]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "request_id": "rid",
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "这是智能摘要。"}}
            ],
            "references": [
                {
                    "id": 1,
                    "title": "百度千帆",
                    "url": "https://cloud.baidu.com/product/qianfan",
                    "date": "2026-10-24 10:00:00",
                    "content": "千帆大模型平台",
                },
                {"id": 2, "title": "", "content": ""},
            ],
        },
    )


def test_ai_search_payload_and_output() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return _ai_answer([])

    result = asyncio.run(
        _registry(httpx.MockTransport(handler)).execute(
            "searchWeb", {"query": "百度千帆", "recency": "month"}
        )
    )

    assert captured["url"] == "https://qianfan.baidubce.com/v2/ai_search/chat/completions"
    assert captured["auth"] == "Bearer test-key"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["messages"] == [{"role": "user", "content": "百度千帆"}]
    assert body["model"] == "ernie-test"
    assert body["search_source"] == "baidu_search_v2"
    assert body["resource_type_filter"] == [{"type": "web", "top_k": 3}]
    assert body["search_recency_filter"] == "month"

    assert "这是智能摘要。" in result
    assert "参考来源" in result
    assert "百度千帆" in result
    assert "https://cloud.baidu.com/product/qianfan" in result


def test_top_k_is_clamped() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _ai_answer([])

    asyncio.run(
        _registry(httpx.MockTransport(handler)).execute(
            "searchWeb", {"query": "x", "top_k": 999}
        )
    )

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["resource_type_filter"] == [{"type": "web", "top_k": 20}]


def test_fallback_to_web_search() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(
                403, json={"code": "account_overdue", "message": "overdue"}
            )
        body = json.loads(request.content)
        assert "model" not in body  # 兜底接口不需要 model
        return httpx.Response(
            200,
            json={
                "references": [
                    {
                        "title": "备用结果",
                        "url": "https://example.com/a",
                        "date": "2026-10-20",
                        "content": "备用摘要",
                    }
                ]
            },
        )

    result = asyncio.run(
        _registry(httpx.MockTransport(handler)).execute("searchWeb", {"query": "q"})
    )

    assert paths == ["/v2/ai_search/chat/completions", "/v2/ai_search/web_search"]
    assert "智能搜索生成不可用" in result
    assert "欠费" in result
    assert "备用结果" in result
    assert "https://example.com/a" in result


def test_both_endpoints_fail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"code": "account_overdue", "message": "overdue"})

    result = asyncio.run(
        _registry(httpx.MockTransport(handler)).execute("searchWeb", {"query": "q"})
    )

    assert result.startswith("搜索失败")
    assert "智能搜索生成不可用" in result
    assert "兜底也失败" in result


def test_network_error_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    result = asyncio.run(
        _registry(httpx.MockTransport(handler)).execute("searchWeb", {"query": "q"})
    )

    assert "搜索失败" in result
    assert "网络请求失败" in result


def test_empty_result_and_invalid_arguments() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [], "references": []})

    registry = _registry(httpx.MockTransport(handler))

    empty = asyncio.run(registry.execute("searchWeb", {"query": "不存在的东西"}))
    assert "没有找到" in empty

    invalid = asyncio.run(registry.execute("searchWeb", {"query": "x", "recency": "hour"}))
    assert "recency" in invalid

    blank = asyncio.run(registry.execute("searchWeb", {"query": "   "}))
    assert "不能为空" in blank


def test_tool_registration_depends_on_key() -> None:
    assert "searchWeb" not in build_default_registry().names
    assert "searchWeb" not in build_default_registry(SearchConfig()).names
    assert "searchWeb" in build_default_registry(SearchConfig(api_key="k")).names


def test_missing_key_rejected() -> None:
    with pytest.raises(ValueError):
        web_search.register(ToolRegistry(), SearchConfig())


def test_primary_failure_is_remembered() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(
                403, json={"code": "account_overdue", "message": "overdue"}
            )
        return httpx.Response(200, json={"references": [{"title": "t", "url": "https://e.com"}]})

    registry = _registry(httpx.MockTransport(handler))
    asyncio.run(registry.execute("searchWeb", {"query": "第一次"}))
    asyncio.run(registry.execute("searchWeb", {"query": "第二次"}))

    # 第二次不再白跑必然失败的主接口
    assert paths == [
        "/v2/ai_search/chat/completions",
        "/v2/ai_search/web_search",
        "/v2/ai_search/web_search",
    ]


def test_network_error_is_not_remembered() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/chat/completions"):
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json={"references": [{"title": "t", "url": "https://e.com"}]})

    registry = _registry(httpx.MockTransport(handler))
    asyncio.run(registry.execute("searchWeb", {"query": "a"}))
    asyncio.run(registry.execute("searchWeb", {"query": "b"}))

    # 网络抖动属于可自愈错误，下次仍然重试主接口
    assert paths.count("/v2/ai_search/chat/completions") == 2


def test_day_recency_mapped_per_endpoint() -> None:
    bodies: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        bodies[request.url.path] = json.loads(request.content)
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(
                403, json={"code": "account_overdue", "message": "overdue"}
            )
        return httpx.Response(200, json={"references": [{"title": "t", "url": "https://e.com"}]})

    asyncio.run(
        _registry(httpx.MockTransport(handler)).execute(
            "searchWeb", {"query": "q", "recency": "day"}
        )
    )

    # 主接口不支持 day，降级为 week；兜底接口原样保留 day
    assert bodies["/v2/ai_search/chat/completions"]["search_recency_filter"] == "week"
    assert bodies["/v2/ai_search/web_search"]["search_recency_filter"] == "day"


def test_day_recency_mapped_per_endpoint() -> None:
    bodies: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        bodies[request.url.path] = json.loads(request.content)
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(
                403, json={"code": "account_overdue", "message": "overdue"}
            )
        return httpx.Response(200, json={"references": [{"title": "t", "url": "https://e.com"}]})

    asyncio.run(
        _registry(httpx.MockTransport(handler)).execute(
            "searchWeb", {"query": "q", "recency": "day"}
        )
    )

    # 主接口不支持 day，降级为 week；兜底接口原样保留 day
    assert bodies["/v2/ai_search/chat/completions"]["search_recency_filter"] == "week"
    assert bodies["/v2/ai_search/web_search"]["search_recency_filter"] == "day"
