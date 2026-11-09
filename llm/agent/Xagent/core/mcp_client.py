"""MCP Client：让 Xagent 通过 Function Calling 调用外部 MCP Server 的工具。

工作流程（与课程要求对应）：
1. 程序启动时读取项目根目录的 ``.mcp.json`` 配置文件，获得各 MCP Server 的连接信息；
2. 使用 MCP Python SDK 的 Streamable HTTP 传输建立客户端连接，
   调用 ``list_tools()`` 获取工具的名称、描述和参数 Schema，
   并注册进 Xagent 已有的工具集合中，使 LLM 能像调用本地 Function Calling 工具一样
   调用这些远程工具；
3. 为避免不同 MCP Server 之间以及与本地工具之间出现工具名称冲突，
   暴露给 LLM 的工具名统一加服务器名前缀（``mcp__<server>__<tool>``），
   并在内部维护「暴露名 -> (服务器配置, 原始工具名)」的映射关系；
4. LLM 发起工具调用时，根据映射找到对应的 MCP Server，以 function call 的方式
   转发工具名称和参数（``call_tool``），并把返回的文本内容、结构化数据或错误信息
   转成字符串回传给 LLM。

单个 MCP Server 连接失败只输出警告并跳过，不影响其他工具的正常运行。
"""

from __future__ import annotations

import json
import logging
import re
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlparse

from mcp import ClientSession, StdioServerParameters
from mcp.client.session_group import StreamableHttpParameters  # noqa: F401  # 文档用途
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import httpx2, streamable_http_client

from .tools.registry import ToolRegistry

MCP_CONFIG_FILENAME = ".mcp.json"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

logger = logging.getLogger("xagent.mcp")

# 暴露给 LLM 的工具名前缀，形如 mcp__<server>__<tool>
NAME_PREFIX = "mcp"
NAME_SEPARATOR = "__"

DEFAULT_TIMEOUT = 30.0


@dataclass(frozen=True)
class MCPServerConfig:
    """一个 MCP Server 的连接信息（来自 .mcp.json）。

    支持两种传输方式：
    - Streamable HTTP：配置 url（可选 headers）；
    - stdio 本地进程：配置 command / args / env（如 npx 启动的本地 MCP Server）。
    """

    name: str
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)

    @property
    def is_stdio(self) -> bool:
        return bool(self.command.strip())


def load_mcp_config(config_path: str | Path | None = None) -> dict[str, MCPServerConfig]:
    """读取 .mcp.json，返回 服务器名 -> 连接配置 的映射。

    查找顺序：显式路径 > 当前工作目录 > 项目根目录。
    文件不存在时返回空映射（不启用 MCP 工具），格式错误时抛出 ValueError。
    """

    if config_path is not None:
        candidates = [Path(config_path).expanduser()]
    else:
        candidates = [Path.cwd() / MCP_CONFIG_FILENAME, PROJECT_ROOT / MCP_CONFIG_FILENAME]

    path = next((c for c in candidates if c.is_file()), None)
    if path is None:
        return {}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"MCP 配置文件不是合法 JSON: {path} ({exc})") from exc

    servers_raw = raw.get("mcpServers")
    if not isinstance(servers_raw, dict):
        raise ValueError(f"MCP 配置文件缺少 mcpServers 字段: {path}")

    servers: dict[str, MCPServerConfig] = {}
    for name, entry in servers_raw.items():
        if not isinstance(entry, dict):
            raise ValueError(f"MCP 配置项 {name} 必须是对象: {path}")
        url = str(entry.get("url", "")).strip()
        command = str(entry.get("command", "")).strip()
        if not url and not command:
            raise ValueError(f"MCP 配置项 {name} 缺少 url 或 command: {path}")
        headers = {str(k): str(v) for k, v in (entry.get("headers") or {}).items()}
        args = [str(a) for a in (entry.get("args") or [])]
        env = {str(k): str(v) for k, v in (entry.get("env") or {}).items()}
        servers[str(name)] = MCPServerConfig(
            name=str(name), url=url, headers=headers, command=command, args=args, env=env
        )
    return servers


def qualified_tool_name(server_name: str, tool_name: str) -> str:
    """生成暴露给 LLM 的工具名：mcp__<server>__<tool>，并保证字符合法。"""

    def _sanitize(text: str) -> str:
        cleaned = re.sub(r"[^0-9A-Za-z_-]", "_", text)
        return cleaned or "unnamed"

    return f"{NAME_PREFIX}{NAME_SEPARATOR}{_sanitize(server_name)}{NAME_SEPARATOR}{_sanitize(tool_name)}"


def result_to_text(result: Any) -> str:
    """把 MCP CallToolResult 转成回传给 LLM 的字符串。

    优先拼接文本内容块；没有文本但有结构化数据时序列化 structuredContent；
    isError 为真时返回带 [MCP 工具错误] 前缀的错误文本。
    """

    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
        else:  # 图片 / 资源引用等非文本内容块，原样序列化供模型参考
            parts.append(block.model_dump_json())

    if not parts:
        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            parts.append(json.dumps(structured, ensure_ascii=False))

    text = "\n".join(parts).strip()
    if getattr(result, "isError", False):
        return f"[MCP 工具错误] {text or '服务器未返回错误详情'}"
    return text or "（工具执行成功，无返回内容）"


LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0.0.0.0"})


def _make_http_client(server: MCPServerConfig, timeout: float) -> "httpx2.AsyncClient":
    """为 MCP Server 创建 httpx 客户端。

    关键点：**连接本机 MCP Server 时必须绕过系统代理**。
    若环境里存在 HTTP_PROXY / HTTPS_PROXY（企业代理、科学上网工具等），
    httpx 默认会把 127.0.0.1 的请求也交给代理转发，代理再连本机端口往往
    直接失败，表现为 502 或 ``unhandled errors in a TaskGroup``，
    让人误以为 MCP Server 没启动。因此本地地址使用 trust_env=False。
    """

    host = (urlparse(server.url).hostname or "").lower()
    is_local = host in LOCAL_HOSTS or host.startswith("127.")
    return httpx2.AsyncClient(
        headers=server.headers or None,
        timeout=httpx2.Timeout(timeout),
        follow_redirects=True,
        trust_env=not is_local,
    )


class MCPManager:
    """管理所有已配置的 MCP Server 与工具名映射。"""

    def __init__(self, servers: dict[str, MCPServerConfig], timeout: float = DEFAULT_TIMEOUT) -> None:
        self.servers = dict(servers)
        self.timeout = timeout
        # 暴露给 LLM 的工具名 -> (服务器配置, 服务器上的原始工具名)
        self.mapping: dict[str, tuple[MCPServerConfig, str]] = {}
        self.warnings: list[str] = []

    @asynccontextmanager
    async def open_session(self, server: MCPServerConfig) -> AsyncIterator[ClientSession]:
        """按配置建立连接（Streamable HTTP 或 stdio 本地进程）并完成初始化握手。"""

        if server.is_stdio:
            env = dict(server.env) if server.env else None
            params = StdioServerParameters(command=server.command, args=server.args, env=env)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
            return

        http_client = _make_http_client(server, self.timeout)
        async with streamable_http_client(server.url, http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    async def register_into(self, registry: ToolRegistry) -> list[str]:
        """连接每个 MCP Server，发现工具并注册进 Xagent 的工具注册表。

        返回警告列表；单个服务器失败不影响其他服务器。
        """

        self.warnings = []
        for name, server in self.servers.items():
            try:
                async with self.open_session(server) as session:
                    tools_result = await session.list_tools()
                    remote_tools = list(tools_result.tools)
            except Exception as exc:
                reason = _describe_connection_error(exc)
                warning = (
                    f"MCP Server {name!r} ({server.url or server.command}) "
                    f"连接失败，已跳过: {reason}"
                )
                self.warnings.append(warning)
                logger.warning("%s", warning, exc_info=exc)
                continue

            if not remote_tools:
                self.warnings.append(f"MCP Server {name!r} 未提供任何工具。")
                continue

            for tool in remote_tools:
                exposed = qualified_tool_name(name, tool.name)
                if exposed in self.mapping:
                    self.warnings.append(f"工具 {exposed} 重复，已跳过。")
                    continue
                schema = getattr(tool, "input_schema", None) or getattr(
                    tool, "inputSchema", None
                )
                self.mapping[exposed] = (server, tool.name)
                registry.register(
                    ToolSpecFor(
                        name=exposed,
                        description=f"[MCP:{name}] {tool.description or tool.name}",
                        parameters=schema or {"type": "object", "properties": {}},
                        handler=self._make_handler(exposed),
                    )
                )
        return self.warnings

    def _make_handler(self, exposed_name: str):
        async def handler(arguments: dict[str, Any]) -> str:
            return await self.execute(exposed_name, arguments)

        return handler

    async def execute(self, exposed_name: str, arguments: dict[str, Any]) -> str:
        """根据映射把一次 function call 转发到对应的 MCP Server。"""

        entry = self.mapping.get(exposed_name)
        if entry is None:
            return f"未知 MCP 工具: {exposed_name}"
        server, remote_name = entry
        async with self.open_session(server) as session:
            result = await session.call_tool(remote_name, arguments or {})
        return result_to_text(result)


def ToolSpecFor(name: str, description: str, parameters: dict[str, Any], handler):
    """延迟导入的小工厂，避免模块顶层循环依赖。"""

    from .tools.registry import ToolSpec

    return ToolSpec(name=name, description=description, parameters=parameters, handler=handler)


async def register_mcp_tools(
    registry: ToolRegistry,
    config_path: str | Path | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> MCPManager:
    """加载 .mcp.json 并把所有可用 MCP 工具注册进 registry。"""

    servers = load_mcp_config(config_path)
    manager = MCPManager(servers, timeout=timeout)
    if servers:
        await manager.register_into(registry)
    return manager


def _describe_connection_error(exc: BaseException) -> str:
    """把连接异常翻译成用户可读的一句话。

    MCP SDK 内部用 anyio TaskGroup 并发通信，失败时抛出的
    ``ExceptionGroup`` 会把真实原因包在子异常里（表现为
    ``unhandled errors in a TaskGroup (1 sub-exception)``），
    这里递归解包并按常见场景给出可操作的提示。
    """

    # 解包 ExceptionGroup（含 BaseExceptionGroup），取第一个子异常
    subs = getattr(exc, "exceptions", None)
    if subs:
        return _describe_connection_error(subs[0])

    text = f"{type(exc).__name__}: {exc}"
    lowered = str(exc).lower()

    if isinstance(exc, ConnectionError) or "connect" in lowered:
        return f"{text}（无法建立连接：服务未启动、端口不对或地址不可达）"
    if "timed out" in lowered or "timeout" in lowered:
        return f"{text}（连接/初始化超时，可尝试增大 [mcp] timeout）"
    if "401" in lowered or "unauthorized" in lowered or "forbidden" in lowered:
        return f"{text}（鉴权失败，请检查 headers 中的密钥）"
    if "ssl" in lowered:
        return f"{text}（SSL/TLS 错误）"
    if "winerror 2" in lowered or "no such file" in lowered or "not found" in lowered:
        return f"{text}（命令不存在，请检查 command 是否可执行）"
    return text


def print_warnings(manager: MCPManager | None, stream: Any = None) -> None:
    """把 MCP 注册过程中的警告输出到 stderr（连接失败等非致命问题）。"""

    if manager is None:
        return
    target = stream or sys.stderr
    for warning in manager.warnings:
        print(f"[mcp] 警告: {warning}", file=target)
