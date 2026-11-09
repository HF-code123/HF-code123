"""TUI 右侧边栏：会话信息 + 技能清单 + 全部工具列表 + MCP 服务器状态。

信息分四段渲染：
1. 「会话」——模型 / 接口 / 目录 / 会话 id / 轮次 / 消息数 / 工具轮上限 / 状态；
2. 「技能 · N」——.agents/skills 下已发现的技能（名称 + 描述截断）；
3. 「工具 · N」——本地工具与 MCP 工具合并列出（名称 + 描述截断）；
4. 「MCP 服务器」——各服务器连接状态（沿用 set_mcp_status 兼容入口）。
"""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Static

MAX_DESC_CHARS = 90


def _truncate(text: str, limit: int = MAX_DESC_CHARS) -> str:
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


class Sidebar(VerticalScroll):
    """右侧边栏：会话信息与工具清单（内容超高时可滚动）。"""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._session: dict[str, str] = {}
        self._skills: list[tuple[str, str]] = []
        self._tools: list[tuple[str, str]] = []
        self._mcp_servers: dict[str, Any] = {}
        self._mcp_mapping: dict[str, tuple[Any, str]] = {}
        self._mcp_warnings: list[str] = []
        self._body = Static("", markup=False, id="sidebar-body")
        self._refresh_body()

    def compose(self):
        yield self._body

    @property
    def body(self) -> Static:
        """内层内容控件（测试用它断言渲染文本）。"""

        return self._body

    # ---------- 数据入口 ----------

    def set_session_info(self, **fields: str) -> None:
        self._session.update(fields)
        self._refresh_body()

    def set_skills(self, skills: list[tuple[str, str]]) -> None:
        """刷新技能清单（名称, 描述）。"""

        self._skills = list(skills)
        self._refresh_body()

    def set_tools(self, tools: list[tuple[str, str]]) -> None:
        self._tools = list(tools)
        self._refresh_body()

    def set_mcp_status(
        self,
        servers: dict[str, Any],
        mapping: dict[str, tuple[Any, str]],
        warnings: list[str],
    ) -> None:
        """连接完成后刷新 MCP 区域（保留旧 API 供测试与启动流程调用）。"""

        self._mcp_servers = servers
        self._mcp_mapping = mapping
        self._mcp_warnings = list(warnings)
        self._refresh_body()

    # ---------- 渲染 ----------

    def _refresh_body(self) -> None:
        body = Text()

        # 1. 会话信息
        body.append("会话\n", style="bold #7aa2f7")
        labels = [
            ("模型", "model"),
            ("接口", "base_url"),
            ("目录", "cwd"),
            ("会话", "session_id"),
            ("轮次", "turns"),
            ("消息", "messages"),
            ("上限", "max_rounds"),
            ("状态", "status"),
        ]
        for label, key in labels:
            value = self._session.get(key, "-")
            if key == "目录" or key == "cwd":
                value = _truncate(value, 36)
            if key == "接口" or key == "base_url":
                value = _truncate(value, 36)
            style = "#9ece6a" if key == "status" and value == "就绪" else "#a9b1d6"
            body.append(f"{label} ", style="#565f89")
            body.append(f"{value}\n", style=style)

        # 2. 技能列表
        body.append(f"\n技能 · {len(self._skills)}\n", style="bold #7aa2f7")
        if not self._skills:
            body.append("（无）\n", style="#565f89")
        for name, description in self._skills:
            body.append(f"◆ {name}\n", style="bold #bb9af7")
            if description:
                body.append(f"  {_truncate(description)}\n", style="#565f89")

        # 3. 工具列表
        body.append(f"\n工具 · {len(self._tools)}\n", style="bold #7aa2f7")
        if not self._tools:
            body.append("（无）\n", style="#565f89")
        for name, description in self._tools:
            body.append(f"• {name}\n", style="bold #a9b1d6")
            if description:
                body.append(f"  {_truncate(description)}\n", style="#565f89")

        # 4. MCP 服务器状态
        if self._mcp_servers or self._mcp_warnings:
            body.append("\nMCP 服务器\n", style="bold #7aa2f7")
            tools_by_server: dict[str, list[str]] = {
                name: [] for name in self._mcp_servers
            }
            for exposed, (server, _remote) in self._mcp_mapping.items():
                tools_by_server.setdefault(server.name, []).append(
                    exposed.split("__")[-1]
                )
            for name in self._mcp_servers:
                tools = tools_by_server.get(name, [])
                marker, color = (
                    ("●", "#9ece6a") if tools else ("○", "#f7768e")
                )
                body.append(f"{marker} {name}", style=f"bold {color}")
                body.append(f"（{len(tools)} 工具）\n", style="#565f89")
                for tool_name in tools:
                    body.append(f"  · {tool_name}\n", style="#a9b1d6")
            failed = sum(1 for w in self._mcp_warnings if "连接失败" in w)
            if failed:
                body.append(
                    f"{failed} 个服务器连接失败\n", style="#f7768e"
                )

        self._body.update(body)
