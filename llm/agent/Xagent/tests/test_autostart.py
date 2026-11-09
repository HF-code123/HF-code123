"""自动启动本地依赖服务（.mcp.json 的 autoStart 配置）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.autostart import (
    ServiceManager,
    ServiceSpec,
    is_port_open,
    load_services,
)


def write_mcp_json(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / ".mcp.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


class TestLoadServices:
    def test_parses_full_entry(self, tmp_path: Path) -> None:
        path = write_mcp_json(
            tmp_path,
            {
                "autoStart": [
                    {
                        "name": "svc",
                        "command": "python",
                        "args": ["-m", "pkg"],
                        "cwd": str(tmp_path),
                        "env": {"MCP_PORT": "8090"},
                        "port": 8090,
                    }
                ],
                "mcpServers": {},
            },
        )

        specs = load_services(path)

        assert len(specs) == 1
        spec = specs[0]
        assert spec.name == "svc"
        assert spec.args == ("-m", "pkg")
        assert spec.cwd == tmp_path
        assert spec.env == {"MCP_PORT": "8090"}
        assert spec.port == 8090

    def test_missing_section_returns_empty(self, tmp_path: Path) -> None:
        path = write_mcp_json(tmp_path, {"mcpServers": {}})
        assert load_services(path) == []

    def test_skips_entries_without_name_or_command(self, tmp_path: Path) -> None:
        path = write_mcp_json(
            tmp_path,
            {"autoStart": [{"name": "a"}, {"command": "x"}, "bad", {"name": "ok", "command": "c"}]},
        )
        assert [spec.name for spec in load_services(path)] == ["ok"]

    def test_invalid_json_returns_empty(self, tmp_path: Path) -> None:
        path = tmp_path / ".mcp.json"
        path.write_text("{not json", encoding="utf-8")
        assert load_services(path) == []

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert load_services(tmp_path / "nope.json") == []


class TestServiceManager:
    def test_skips_service_already_listening(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("core.autostart.is_port_open", lambda *a, **k: True)
        manager = ServiceManager(
            [ServiceSpec(name="svc", command="whatever", port=8090)], log_dir=tmp_path
        )

        messages = manager.ensure_running()

        assert any("已在运行" in m for m in messages)
        assert manager.warnings == []

    def test_reports_missing_command(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("core.autostart.is_port_open", lambda *a, **k: False)
        manager = ServiceManager(
            [ServiceSpec(name="svc", command="definitely-not-a-real-exe-xyz", port=8090)],
            log_dir=tmp_path,
        )

        messages = manager.ensure_running()

        assert manager.warnings
        assert "找不到命令" in manager.warnings[0]
        assert messages

    def test_stop_all_is_safe_when_nothing_spawned(self, tmp_path: Path) -> None:
        manager = ServiceManager([], log_dir=tmp_path)
        manager.stop_all()  # 不应抛异常

    def test_is_port_open_false_for_closed_port(self) -> None:
        # 端口 1 基本不可能有服务监听
        assert is_port_open(1, timeout=0.1) is False
