"""Xagent 启动入口。

组合根（composition root）：先加载配置并组装 core，再选择具体前端。
未来新增 web / desktop / mobile 时，只需在 FRONTENDS 中登记新的入口函数。

启动流程：
1. 当前目录没有 config.ini 时自动生成 mock 配置：
   - 有可用配置回落（项目根目录 config.ini）→ 用回落配置继续运行，
     并在 TUI 中提示已生成新配置文件；
   - 没有任何可用配置 → 弹出首次运行对话框，用户修改后重启。
2. MCP 采用异步加载：启动时只读取 .mcp.json 的服务器清单，
   进入 TUI 后后台连接并动态刷新边栏。
"""

from __future__ import annotations

import argparse
import importlib
import logging
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.config import (  # noqa: E402
    cwd_config_path,
    ensure_cwd_config,
    find_config,
    load_config,
)
from core.factory import build_agent  # noqa: E402

FrontendRunner = Callable[..., Any]

# 前端名称 -> (模块路径, 入口函数名)
FRONTENDS: dict[str, tuple[str, str]] = {
    "tui": ("tui.app", "run"),
    # 未来扩展示例：
    # "web": ("web.server", "run"),
    # "desktop": ("desktop.app", "run"),
}


def _load_frontend(name: str) -> FrontendRunner:
    module_name, function_name = FRONTENDS[name]
    module = importlib.import_module(module_name)
    return getattr(module, function_name)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="xagent",
        description="Xagent：可调用本地工具的智能 Agent",
    )
    parser.add_argument(
        "--frontend",
        choices=sorted(FRONTENDS),
        default="tui",
        help="选择前端，默认 tui",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="配置文件路径；默认依次查找 ./config.ini 与项目根目录 config.ini",
    )
    return parser.parse_args(argv)


def _setup_compact_logging(cwd: Path) -> None:
    """把 /compact 过程与 MCP 启动日志写入当前项目的 .agents/logs/compact.log。"""

    log_dir = cwd / ".agents" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_dir / "compact.log", encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )
    for name in ("xagent.compact", "xagent.mcp"):
        target = logging.getLogger(name)
        target.addHandler(handler)
        target.setLevel(logging.INFO)


def _load_mcp_servers() -> dict[str, Any]:
    """读取 .mcp.json 服务器清单（只读配置，不建立连接）。"""

    from core.mcp_client import load_mcp_config

    try:
        return load_mcp_config(None)
    except ValueError as exc:
        print(f"[mcp] 配置错误: {exc}", file=sys.stderr)
        return {}


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)

    # 1. 确保当前目录有 config.ini（没有则生成 mock 配置）
    created = ensure_cwd_config() if args.config is None else False

    # 2. 找到实际生效的配置。当前目录的示例模板不会屏蔽真实配置
    #    （find_config 会跳过未修改的 mock）；用户显式指定 --config 时不跳过
    if args.config is not None:
        config_source: Path | None = Path(args.config)
    else:
        try:
            config_source = find_config(None)
        except FileNotFoundError:
            config_source = None

    if config_source is None:
        # 没有任何可用配置（只有未修改的示例模板）：弹首次运行对话框
        from tui.first_run import run_first_run_dialog

        print(f"已生成示例配置: {cwd_config_path()}", file=sys.stderr)
        run_first_run_dialog(cwd_config_path())
        return 1

    _setup_compact_logging(Path.cwd())

    # 3. 加载配置并组装 Agent
    try:
        config = load_config(config_source)
        agent = build_agent(config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"启动失败: {exc}", file=sys.stderr)
        return 1

    config_notice = (
        f"已生成新的 config.ini（示例配置）：{cwd_config_path()}。"
        f"本次运行使用 {config_source}；"
        "如需在当前项目使用独立配置，请修改该文件后重启。"
        if created
        else None
    )

    # 3. MCP 只读取服务器清单，连接交给 TUI 异步完成
    mcp_servers = _load_mcp_servers()

    try:
        run = _load_frontend(args.frontend)
    except ImportError as exc:
        print(f"无法加载前端 {args.frontend}: {exc}", file=sys.stderr)
        return 1

    run(
        agent,
        model_name=config.llm.model,
        base_url=config.llm.base_url,
        mcp_servers=mcp_servers,
        mcp_timeout=config.mcp.timeout,
        config_notice=config_notice,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
