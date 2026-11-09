"""本地依赖服务的自动启动。

有些 MCP Server 需要先跑起来一个本地 HTTP 服务（例如宠物医院的
Python MCP 适配器，以及它依赖的 Go REST API）。每次手动开两个窗口
很麻烦，所以允许在 ``.mcp.json`` 里声明这些服务：

    {
      "autoStart": [
        {"name": "pethospital-api", "command": "D:\\\\llms\\\\windows\\\\pethospital.exe",
         "cwd": "D:\\\\llms\\\\windows", "port": 8080},
        {"name": "pethospital-mcp", "command": "...\\\\.venv\\\\Scripts\\\\python.exe",
         "args": ["-m", "pet_hospital_mcp"], "cwd": "...",
         "env": {"MCP_PORT": "8090"}, "port": 8090}
      ],
      "mcpServers": { ... }
    }

xAgent 启动时按顺序检查：端口已经在监听就跳过（用户自己开过了），
否则拉起子进程并等待端口就绪；进程输出写入 .agents/logs/service-<name>.log，
退出 xAgent 时自动结束由本程序拉起的进程。
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("xagent.autostart")

DEFAULT_READY_TIMEOUT = 20.0
POLL_INTERVAL = 0.3


@dataclass(frozen=True)
class ServiceSpec:
    """一个需要随 xAgent 一起启动的本地服务。"""

    name: str
    command: str
    args: tuple[str, ...] = ()
    cwd: Path | None = None
    env: dict[str, str] = field(default_factory=dict)
    port: int | None = None
    ready_timeout: float = DEFAULT_READY_TIMEOUT


def load_services(config_path: str | Path | None = None) -> list[ServiceSpec]:
    """从 .mcp.json 读取顶层 autoStart 列表；没有配置时返回空列表。"""

    if config_path is not None:
        path: Path | None = Path(config_path).expanduser()
    else:
        candidates = [Path.cwd() / ".mcp.json", Path(__file__).resolve().parents[1] / ".mcp.json"]
        path = next((item for item in candidates if item.is_file()), None)
    if path is None or not path.is_file():
        return []

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("读取 autoStart 配置失败: %s", exc)
        return []

    entries = raw.get("autoStart")
    if not isinstance(entries, list):
        return []

    specs: list[ServiceSpec] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        command = str(entry.get("command") or "").strip()
        if not name or not command:
            continue
        port_raw = entry.get("port")
        port = int(port_raw) if isinstance(port_raw, (int, str)) and str(port_raw).strip().isdigit() else None
        cwd_raw = str(entry.get("cwd") or "").strip()
        specs.append(
            ServiceSpec(
                name=name,
                command=command,
                args=tuple(str(a) for a in (entry.get("args") or [])),
                cwd=Path(cwd_raw) if cwd_raw else None,
                env={str(k): str(v) for k, v in (entry.get("env") or {}).items()},
                port=port,
                ready_timeout=float(entry.get("ready_timeout") or DEFAULT_READY_TIMEOUT),
            )
        )
    return specs


def is_port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    """端口是否可连接（用于判断服务是否已在运行）。"""

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class ServiceManager:
    """负责启动/停止随 xAgent 运行的本地服务。"""

    def __init__(self, specs: list[ServiceSpec], log_dir: Path | None = None) -> None:
        self._specs = list(specs)
        self._log_dir = log_dir or (Path.cwd() / ".agents" / "logs")
        self._spawned: list[tuple[ServiceSpec, subprocess.Popen]] = []
        self.warnings: list[str] = []

    def ensure_running(self) -> list[str]:
        """确保所有服务可用；返回人话级别的进展/告警消息。"""

        messages: list[str] = []
        for spec in self._specs:
            if spec.port is not None and is_port_open(spec.port):
                messages.append(f"{spec.name} 已在运行（端口 {spec.port}），跳过启动")
                continue

            executable = Path(spec.command)
            if not executable.exists() and not _which(spec.command):
                message = f"{spec.name} 启动失败：找不到命令 {spec.command}"
                self.warnings.append(message)
                messages.append(message)
                continue

            try:
                process = self._spawn(spec)
            except OSError as exc:
                message = f"{spec.name} 启动失败：{exc}"
                self.warnings.append(message)
                messages.append(message)
                continue

            self._spawned.append((spec, process))
            ready, detail = self._wait_ready(spec, process)
            if ready:
                messages.append(f"{spec.name} 已启动" + (f"（端口 {spec.port}）" if spec.port else ""))
            else:
                message = f"{spec.name} 启动后未就绪：{detail}"
                self.warnings.append(message)
                messages.append(message)
        return messages

    def _spawn(self, spec: ServiceSpec) -> subprocess.Popen:
        self._log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self._log_dir / f"service-{spec.name}.log"
        log_file = open(log_path, "a", encoding="utf-8", errors="replace")
        env = dict(os.environ)
        env.update(spec.env)
        creationflags = 0
        if os.name == "nt":  # 不弹黑窗口
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        logger.info("启动服务 %s: %s %s", spec.name, spec.command, " ".join(spec.args))
        return subprocess.Popen(
            [spec.command, *spec.args],
            cwd=str(spec.cwd) if spec.cwd else None,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )

    def _wait_ready(self, spec: ServiceSpec, process: subprocess.Popen) -> tuple[bool, str]:
        if spec.port is None:
            # 没声明端口时无法探测，只确认进程没立刻退出
            time.sleep(0.5)
            if process.poll() is None:
                return True, ""
            return False, _log_tail(self._log_dir / f"service-{spec.name}.log")

        deadline = time.monotonic() + spec.ready_timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return False, f"进程已退出（code {process.returncode}）：" + _log_tail(
                    self._log_dir / f"service-{spec.name}.log"
                )
            if is_port_open(spec.port):
                return True, ""
            time.sleep(POLL_INTERVAL)
        return False, f"等待 {spec.ready_timeout:.0f} 秒后端口 {spec.port} 仍不可用"

    def stop_all(self) -> None:
        """结束由本程序拉起的服务进程（用户自己启动的不动）。"""

        for spec, process in self._spawned:
            if process.poll() is not None:
                continue
            try:
                process.terminate()
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass
            logger.info("已停止服务 %s", spec.name)
        self._spawned.clear()


def _which(command: str) -> str | None:
    """在 PATH 中查找可执行文件（如 npx、python）。"""

    from shutil import which

    return which(command)


def _log_tail(path: Path, lines: int = 3) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except OSError:
        return ""
    return " / ".join(content[-lines:]) if content else ""


def start_services_async_safe(log_dir: Path | None = None) -> ServiceManager:
    """读取配置并启动服务；同时注册退出时自动清理。"""

    manager = ServiceManager(load_services(), log_dir=log_dir)
    manager.ensure_running()
    atexit.register(manager.stop_all)
    return manager
