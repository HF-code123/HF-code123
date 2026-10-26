"""Textual TUI 前端包。

未来新增 web / desktop / mobile 前端时，可以在此同级新增独立包，
它们都只依赖 core.contracts 与 core.events。
"""

from __future__ import annotations

from .app import XagentApp, run

__all__ = ["XagentApp", "run"]
