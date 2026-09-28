import json
from dataclasses import dataclass
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError


@dataclass(frozen=True, slots=True)
class AppError(Exception):
    code: str
    message: str
    details: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "details": self.details or {}}}


def raise_tool_error(error: AppError) -> None:
    raise ToolError(json.dumps(error.as_dict(), ensure_ascii=False, separators=(",", ":")))