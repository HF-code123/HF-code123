"""MCP Client 单元测试：配置解析、名称映射、工具注册与调用转发。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.mcp_client import (
    MCPServerConfig,
    MCPManager,
    load_mcp_config,
    qualified_tool_name,
    result_to_text,
)
from core.tools.registry import ToolRegistry


# ---------- 配置解析 ----------


def test_load_mcp_config_reads_servers(tmp_path: Path) -> None:
    path = tmp_path / ".mcp.json"
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "pethospital": {"url": "http://127.0.0.1:8090/mcp"},
                    "auth-server": {
                        "url": "https://example.com/mcp",
                        "headers": {"Authorization": "Bearer token"},
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    servers = load_mcp_config(path)

    assert set(servers) == {"pethospital", "auth-server"}
    assert servers["pethospital"].url == "http://127.0.0.1:8090/mcp"
    assert servers["auth-server"].headers == {"Authorization": "Bearer token"}


def test_load_mcp_config_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_mcp_config(tmp_path / "nope.json") == {}


def test_load_mcp_config_invalid(tmp_path: Path) -> None:
    path = tmp_path / ".mcp.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_mcp_config(path)

    path.write_text(json.dumps({"mcpServers": {"a": {}}}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_mcp_config(path)


# ---------- 名称映射 ----------


def test_qualified_tool_name_sanitizes() -> None:
    assert qualified_tool_name("pet hospital", "list_pets") == "mcp__pet_hospital__list_pets"
    assert qualified_tool_name("a", "b") == "mcp__a__b"


# ---------- 结果转换 ----------


class FakeBlock:
    def __init__(self, text: str | None = None) -> None:
        self.text = text

    def model_dump_json(self) -> str:
        return '{"image": true}'


class FakeResult:
    def __init__(self, content, is_error=False, structured=None) -> None:
        self.content = content
        self.isError = is_error
        self.structuredContent = structured


def test_result_to_text_variants() -> None:
    assert result_to_text(FakeResult([FakeBlock("hello")])) == "hello"
    assert result_to_text(FakeResult([FakeBlock("a"), FakeBlock("b")])) == "a\nb"
    assert result_to_text(FakeResult([], structured={"total": 3})) == '{"total": 3}'
    out = result_to_text(FakeResult([FakeBlock("boom")], is_error=True))
    assert out.startswith("[MCP 工具错误]")
    out = result_to_text(FakeResult([FakeBlock()]))
    assert out == '{"image": true}'


# ---------- 注册与调用转发（使用假 session） ----------


class FakeToolsResult:
    def __init__(self, tools) -> None:
        self.tools = tools


class FakeTool:
    def __init__(self, name: str, description: str, schema: dict) -> None:
        self.name = name
        self.description = description
        self.inputSchema = schema


class FakeSession:
    def __init__(self, tools, call_log) -> None:
        self._tools = tools
        self._call_log = call_log

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def initialize(self) -> None:
        return None

    async def list_tools(self):
        return FakeToolsResult(self._tools)

    async def call_tool(self, name, arguments):
        self._call_log.append((name, arguments))

        class R:
            content = []
            isError = False
            structuredContent = {"echo": arguments}

        return R()


@pytest.fixture
def manager_with_fake(monkeypatch):
    call_log: list = []
    server = MCPServerConfig(name="pethospital", url="http://127.0.0.1:8090/mcp")
    manager = MCPManager({"pethospital": server})

    def fake_open_session(self, cfg):
        return FakeSession(
            [
                FakeTool(
                    "list_pets",
                    "查询宠物档案列表",
                    {"type": "object", "properties": {"page": {"type": "integer"}}},
                )
            ],
            call_log,
        )

    monkeypatch.setattr(MCPManager, "open_session", fake_open_session)
    return manager, call_log


@pytest.mark.asyncio
async def test_register_and_execute(manager_with_fake) -> None:
    manager, call_log = manager_with_fake
    registry = ToolRegistry()

    warnings = await manager.register_into(registry)

    assert warnings == []
    exposed = qualified_tool_name("pethospital", "list_pets")
    assert exposed in registry.names
    assert exposed in manager.mapping
    # definition 遵循 OpenAI function calling 格式
    definition = registry.definitions()[0]
    assert definition["function"]["name"] == exposed
    assert definition["function"]["parameters"]["type"] == "object"

    result = await registry.execute(exposed, {"page": 2})
    assert call_log == [("list_pets", {"page": 2})]
    assert "echo" in result

    # 未知工具
    result = await registry.execute("mcp__nope__x", {})
    assert "未知" in result


@pytest.mark.asyncio
async def test_register_skips_unreachable_server(monkeypatch) -> None:
    server = MCPServerConfig(name="down", url="http://127.0.0.1:1/mcp")
    manager = MCPManager({"down": server, "ok": MCPServerConfig(name="ok", url="http://ok/mcp")})

    def fake_open_session(self, cfg):
        if cfg.name == "down":
            raise ConnectionError("refused")
        return FakeSession([FakeTool("t", "d", {"type": "object"})], [])

    monkeypatch.setattr(MCPManager, "open_session", fake_open_session)
    registry = ToolRegistry()

    warnings = await manager.register_into(registry)

    assert len(warnings) == 1 and "down" in warnings[0]
    assert registry.names == (qualified_tool_name("ok", "t"),)


# ---------- 连接错误可读化 ----------

from core.mcp_client import _describe_connection_error  # noqa: E402


class TestDescribeConnectionError:
    def test_unpacks_exception_group(self) -> None:
        inner = ConnectionError("[Errno 10061] Connect call failed")
        group: BaseExceptionGroup = BaseExceptionGroup(
            "unhandled errors in a TaskGroup (1 sub-exception)", [inner]
        )

        message = _describe_connection_error(group)

        assert "TaskGroup" not in message
        assert "服务未启动" in message
        assert "10061" in message

    def test_timeout_hint(self) -> None:
        message = _describe_connection_error(TimeoutError("Request timed out"))
        assert "timeout" in message.lower()

    def test_http_401_hint(self) -> None:
        message = _describe_connection_error(RuntimeError("HTTP 401 Unauthorized"))
        assert "鉴权" in message

    def test_plain_error_passthrough(self) -> None:
        message = _describe_connection_error(RuntimeError("weird failure"))
        assert "weird failure" in message


class TestLocalProxyBypass:
    """本机 MCP Server 必须绕过系统代理（HTTP_PROXY 存在时尤其重要）。"""

    def test_local_host_disables_trust_env(self) -> None:
        from core.mcp_client import MCPServerConfig, _make_http_client

        for url in ("http://127.0.0.1:8090/mcp", "http://localhost:8090/mcp", "http://127.0.1.1:1/mcp"):
            client = _make_http_client(MCPServerConfig(name="s", url=url), 5.0)
            assert client.trust_env is False, url

    def test_remote_host_keeps_trust_env(self) -> None:
        from core.mcp_client import MCPServerConfig, _make_http_client

        client = _make_http_client(
            MCPServerConfig(name="s", url="https://mcp.example.com/mcp"), 5.0
        )
        assert client.trust_env is True


class TestLocalProxyBypass:
    """本机 MCP Server 必须绕过系统代理（HTTP_PROXY 存在时尤其重要）。"""

    def test_local_host_disables_trust_env(self) -> None:
        from core.mcp_client import MCPServerConfig, _make_http_client

        for url in ("http://127.0.0.1:8090/mcp", "http://localhost:8090/mcp", "http://127.0.1.1:1/mcp"):
            client = _make_http_client(MCPServerConfig(name="s", url=url), 5.0)
            assert client.trust_env is False, url

    def test_remote_host_keeps_trust_env(self) -> None:
        from core.mcp_client import MCPServerConfig, _make_http_client

        client = _make_http_client(
            MCPServerConfig(name="s", url="https://mcp.example.com/mcp"), 5.0
        )
        assert client.trust_env is True
