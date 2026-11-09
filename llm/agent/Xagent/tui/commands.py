"""斜杠命令注册表与输入补全面板。

输入 / 时在输入框上方弹出命令引导面板：继续输入过滤命令，
↑/↓ 选择、Tab 或点击补全，Enter 始终执行输入框中的内容。
"""

from __future__ import annotations

from dataclasses import dataclass

from textual import events
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option


@dataclass(frozen=True)
class Command:
    """一个斜杠命令的名称与说明。"""

    name: str
    description: str


COMMANDS: list[Command] = [
    Command("help", "显示所有斜杠命令"),
    Command("new", "新建会话（自动保存当前会话历史）"),
    Command("resume", "恢复历史会话（列表中选择）"),
    Command("save", "保存当前会话到 .agents/sessions/"),
    Command("context", "查看当前上下文用量"),
    Command("compact", "压缩上下文（归档 + 生成摘要）"),
    Command("clear", "清空当前对话显示"),
    Command("quit", "退出 xAgent"),
]

COMMAND_HINT_ID = "command-hint"


def match_commands(prefix: str) -> list[Command]:
    """按前缀过滤命令（大小写不敏感）。"""

    prefix = prefix.lower()
    return [c for c in COMMANDS if c.name.startswith(prefix)]


def parse_command_name(text: str) -> str:
    """从输入文本解析命令名；不是命令时返回空串。"""

    if not text.startswith("/"):
        return ""
    parts = text[1:].split()
    return parts[0].lower() if parts else ""


class PromptInput(Input):
    """带命令补全引导的输入框。"""

    async def _on_key(self, event: events.Key) -> None:
        """补全面板可见时拦截 ↑/↓/Tab，实现键盘选择与补全。"""

        hint = self.app.query_one(f"#{COMMAND_HINT_ID}", OptionList)
        if hint.has_class("hidden"):
            await super()._on_key(event)
            return

        if event.key == "down" and hint.option_count:
            hint.action_cursor_down()
            event.stop()
            event.prevent_default()
            return
        if event.key == "up" and hint.option_count:
            hint.action_cursor_up()
            event.stop()
            event.prevent_default()
            return
        if event.key == "tab":
            index = hint.highlighted
            if index is not None and self.app._hint_matches:
                command = self.app._hint_matches[index]
                self.value = f"/{command.name} "
                self.cursor_position = len(self.value)
            event.stop()
            event.prevent_default()
            return
        await super()._on_key(event)


def build_hint_options(matches: list[Command]) -> list[Option]:
    return [Option(f"/{c.name}  —  {c.description}", id=c.name) for c in matches]
