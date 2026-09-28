import json
import logging
import time
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "tool_name": getattr(record, "tool_name", record.name),
            "params": getattr(record, "params", {}),
            "status": getattr(record, "status", record.levelname.lower()),
            "duration_ms": getattr(record, "duration_ms", 0),
            "message": record.getMessage(),
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def redact(value: Any) -> Any:
    sensitive = {"ownerPhone", "owner_phone", "ownerAddr", "owner_addr", "chipNo", "chip_no"}
    if isinstance(value, dict):
        return {key: "***" if key in sensitive else redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])