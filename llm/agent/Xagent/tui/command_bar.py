"""输入框下方的命令栏。

第一行：可点击的斜杠命令药丸（点击填入输入框）
第二行：快捷键提示
"""

from __future__ import annotations

from textual.widgets import Static

from .commands import COMMANDS

PILL_STYLE = "#7aa2f7 on #1f2335"


def _pills() -> str:
    return " ".join(
        f'[@click=app.fill_command("{c.name}")][{PILL_STYLE}] /{c.name} [/][/]'
        for c in COMMANDS
    )


class CommandBar(Static):
    """命令栏：markup 开启以支持可点击命令药丸。"""

    def __init__(self, **kwargs) -> None:
        super().__init__(markup=True, **kwargs)
        self._markup = ""

    @property
    def markup_text(self) -> str:
        """最近一次渲染的 markup 源串（测试用它断言 @click 药丸）。"""

        return self._markup

    def _refresh(self) -> None:
        self._markup = (
            f"{_pills()}\n"
            "[#565f89]Enter 发送 · Esc 中断生成 · Ctrl+B 侧栏 · "
            "Ctrl+N 新会话 · Ctrl+L 清屏[/]"
        )
        super().update(self._markup)

    def on_mount(self) -> None:
        self._refresh()
