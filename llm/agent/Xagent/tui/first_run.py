"""首次运行对话框：自动生成 config.ini 后提示用户修改并重启。"""

from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Footer, Static


class FirstRunApp(App[None]):
    CSS_PATH = "styles.tcss"
    TITLE = "Xagent 首次运行"
    BINDINGS = [("escape", "quit", "退出")]

    def __init__(self, config_path: Path) -> None:
        super().__init__()
        self._config_path = config_path

    def compose(self) -> ComposeResult:
        with Vertical(id="first-run"):
            yield Static(
                "⚠ 已在当前目录生成新的 config.ini（当前为 mock 示例配置）\n\n"
                f"文件位置：{self._config_path}\n\n"
                "请编辑该文件，填入你的模型服务信息：\n"
                "  [llm] base_url / api_key / model（必填）\n"
                "  [search] baidu_qianfan_api_key（可选，联网搜索）\n\n"
                "修改完成后重新启动 xAgent 才能正常工作。",
                id="first-run-text",
                markup=False,
            )
            yield Button("我已了解，退出", id="first-run-close", variant="warning")
        yield Footer()

    @on(Button.Pressed, "#first-run-close")
    def _close(self) -> None:
        self.exit()


def run_first_run_dialog(config_path: Path) -> None:
    FirstRunApp(config_path).run()
