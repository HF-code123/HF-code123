"""会话持久化：把对话历史保存为 JSONL 事件日志，支持列表与恢复。

存储位置：``.agents/sessions/<YYYYMMDD-HHMMSS>.jsonl``
文件格式（每行一个 JSON 对象）：
- 第 1 行：{"type": "meta", "id", "created_at", "title", "message_count"}
- 其余行：{"type": "message", "index": n, "message": {...原始消息...}}

AGENTS.md 注入消息、工具调用参数与工具结果都随消息一起完整保存，
/restore 恢复后即可继续使用（历史细节也可通过 recallContext 检索
压缩归档，两者互不影响）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

TITLE_MAX_CHARS = 40


@dataclass(frozen=True)
class SessionSummary:
    """历史会话列表中展示的一条摘要。"""

    session_id: str
    path: Path
    title: str
    created_at: str
    message_count: int


def sessions_root(project_root: Path | None = None) -> Path:
    root = project_root or Path.cwd()
    return root / ".agents" / "sessions"


def _make_title(messages: list[dict]) -> str:
    """取第一条用户消息作为会话标题，截断到 40 字符。"""

    for message in messages:
        if message.get("role") == "user":
            content = str(message.get("content") or "").strip()
            if content:
                first_line = content.splitlines()[0]
                if len(first_line) > TITLE_MAX_CHARS:
                    first_line = first_line[:TITLE_MAX_CHARS] + "…"
                return first_line
    return "（空会话）"


def save_session(
    messages: list[dict],
    project_root: Path | None = None,
    session_id: str | None = None,
) -> SessionSummary:
    """把完整消息列表保存为 JSONL；写后回读校验条数。"""

    directory = sessions_root(project_root)
    directory.mkdir(parents=True, exist_ok=True)
    sid = session_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"{sid}.jsonl"

    lines = [
        json.dumps(
            {
                "type": "meta",
                "id": sid,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "title": _make_title(messages),
                "message_count": len(messages),
            },
            ensure_ascii=False,
        )
    ]
    for index, message in enumerate(messages):
        lines.append(
            json.dumps(
                {"type": "message", "index": index, "message": message},
                ensure_ascii=False,
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 写后回读校验：损坏立即报错，避免 /resume 恢复出残缺会话
    restored = load_session(path)
    if len(restored) != len(messages):
        raise OSError(f"会话保存校验失败: {path}")
    return load_summary(path)


def list_sessions(project_root: Path | None = None) -> list[SessionSummary]:
    """列出所有历史会话，按保存时间倒序（最新在前）。"""

    directory = sessions_root(project_root)
    if not directory.is_dir():
        return []
    summaries = [
        summary
        for path in directory.glob("*.jsonl")
        if (summary := _try_load_summary(path)) is not None
    ]
    summaries.sort(key=lambda s: s.session_id, reverse=True)
    return summaries


def load_session(path: str | Path) -> list[dict]:
    """读取一个会话文件，返回完整消息列表。"""

    lines = Path(path).read_text(encoding="utf-8").splitlines()
    messages: list[dict] = []
    for line in lines:
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("type") == "message":
            messages.append(record["message"])
    return messages


def load_summary(path: str | Path) -> SessionSummary:
    """读取会话元信息（文件损坏时抛出异常）。"""

    summary = _try_load_summary(Path(path))
    if summary is None:
        raise OSError(f"会话文件损坏或缺少元信息: {path}")
    return summary


def _try_load_summary(path: Path) -> SessionSummary | None:
    try:
        first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        if first.get("type") != "meta":
            return None
        return SessionSummary(
            session_id=str(first.get("id", path.stem)),
            path=path,
            title=str(first.get("title", "（未知）")),
            created_at=str(first.get("created_at", "")),
            message_count=int(first.get("message_count", 0)),
        )
    except (OSError, json.JSONDecodeError, IndexError, ValueError):
        return None
