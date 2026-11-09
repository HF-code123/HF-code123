"""历史会话选择面板：↑/↓ 选择、Enter 恢复、Esc 取消。"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList
from textual.widgets.option_list import Option

from core.session_store import SessionSummary


class SessionsPanel(ModalScreen["SessionSummary | None"]):
    """列出 .agents/sessions/ 下的历史会话，回车恢复、Esc 取消。"""

    BINDINGS = [("escape", "cancel", "取消")]

    def __init__(self, sessions: list[SessionSummary]) -> None:
        super().__init__()
        self._sessions = sessions

    def compose(self) -> ComposeResult:
        with Vertical(id="sessions-panel"):
            yield Label(
                f"选择要恢复的会话（共 {len(self._sessions)} 个，Enter 恢复 / Esc 取消）",
                id="sessions-title",
            )
            option_list = OptionList(id="sessions-list")
            for session in self._sessions:
                option_list.add_option(
                    Option(
                        f"{session.created_at}  {session.title}"
                        f"（{session.message_count} 条消息）",
                        id=session.session_id,
                    )
                )
            yield option_list

    def on_mount(self) -> None:
        option_list = self.query_one("#sessions-list", OptionList)
        if option_list.option_count:
            option_list.highlighted = 0
        option_list.focus()

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        selected = next(
            (s for s in self._sessions if s.session_id == event.option_id), None
        )
        self.dismiss(selected)

    def action_cancel(self) -> None:
        self.dismiss(None)
