"""Xagent 启动入口。

组合根（composition root）：先加载配置并组装 core，再选择具体前端。
未来新增 web / desktop / mobile 时，只需在 FRONTENDS 中登记新的入口函数。
"""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.config import load_config  # noqa: E402
from core.factory import build_agent  # noqa: E402

FrontendRunner = Callable[..., Any]

# 前端名称 -> (模块路径, 入口函数名)
FRONTENDS: dict[str, tuple[str, str]] = {
    "tui": ("tui.app", "run"),
    # 未来扩展示例：
    # "web": ("web.server", "run"),
    # "desktop": ("desktop.app", "run"),
    # "mobile": ("mobile.app", "run"),
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


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        config = load_config(args.config)
        agent = build_agent(config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"启动失败: {exc}", file=sys.stderr)
        return 1

    try:
        run = _load_frontend(args.frontend)
    except ImportError as exc:
        print(f"无法加载前端 {args.frontend}: {exc}", file=sys.stderr)
        return 1

    run(agent, model_name=config.llm.model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
