"""Windows 中文/日文/韩文输入法兼容补丁。

Textual 8.2.8 的 win32 驱动会丢弃「VK=0 且带有控制键状态」的按键事件，
而部分 CJK 输入法正是通过这种事件提交字符（例如 Shift+? 提交全角问号）。
补丁只替换 EventMonitor.run 中的过滤条件：没有 Unicode 字符的纯修饰键才丢弃。
"""

from __future__ import annotations

import inspect
import re
import sys
from typing import Any

_PATCH_FLAG = "_xagent_ime_patch_applied"


def apply_windows_ime_patch() -> bool:
    """在 Windows 上为 Textual 打 IME 补丁，返回是否实际替换了驱动。"""

    if sys.platform != "win32":
        return False

    try:
        from textual.drivers import win32
    except ImportError:
        return False

    if getattr(win32, _PATCH_FLAG, False):
        return False

    setattr(win32, _PATCH_FLAG, True)
    if not _needs_patch(win32.EventMonitor.run):
        return False

    win32.EventMonitor = _make_patched_monitor(win32)
    return True


def _needs_patch(run: Any) -> bool:
    try:
        source = inspect.getsource(run)
    except (OSError, TypeError):
        return False
    if "_is_bare_modifier_key_event" in source:
        return False
    compact = re.sub(r"\s+", "", source)
    return (
        "if(key_event.dwControlKeyStateandkey_event.wVirtualKeyCode==0):continue"
        in compact
    )


def _is_bare_modifier_key_event(key_event: Any) -> bool:
    return (
        bool(key_event.dwControlKeyState)
        and key_event.wVirtualKeyCode == 0
        and key_event.uChar.UnicodeChar in ("", "\x00")
    )


def _make_patched_monitor(win32: Any) -> type:
    class ImeEventMonitor(win32.EventMonitor):
        def run(self) -> None:
            exit_requested = self.exit_event.is_set
            parser = win32.XTermParser(debug=win32.constants.DEBUG)

            try:
                read_count = win32.wintypes.DWORD(0)
                handle = win32.GetStdHandle(win32.STD_INPUT_HANDLE)

                max_events = 1024
                key_event_type = 0x0001
                window_buffer_size_event = 0x0004

                record_array_type = win32.INPUT_RECORD * max_events
                input_records = record_array_type()
                read_console_input = win32.KERNEL32.ReadConsoleInputW
                keys: list[str] = []
                append_key = keys.append

                while not exit_requested():
                    for event in parser.tick():
                        self.process_event(event)

                    if win32.wait_for_handles([handle], 100) is None:
                        continue

                    read_console_input(
                        handle,
                        win32.byref(input_records),
                        max_events,
                        win32.byref(read_count),
                    )
                    read_input_records = input_records[: read_count.value]

                    del keys[:]
                    new_size: tuple[int, int] | None = None

                    for input_record in read_input_records:
                        event_type = input_record.EventType

                        if event_type == key_event_type:
                            key_event = input_record.Event.KeyEvent
                            key = key_event.uChar.UnicodeChar
                            if key_event.bKeyDown:
                                if _is_bare_modifier_key_event(key_event):
                                    continue
                                append_key(key)
                        elif event_type == window_buffer_size_event:
                            size = input_record.Event.WindowBufferSizeEvent.dwSize
                            new_size = (size.X, size.Y)

                    if keys:
                        text = "".join(keys).encode(
                            "utf-16", "surrogatepass"
                        ).decode("utf-16")
                        for event in parser.feed(text):
                            self.process_event(event)
                    if new_size is not None:
                        self.on_size_change(*new_size)

            except Exception as error:
                self.app.log.error("EVENT MONITOR ERROR", error)

    return ImeEventMonitor
