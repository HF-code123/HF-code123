from __future__ import annotations

import sys

import pytest

from tui.compat import (
    _is_bare_modifier_key_event,
    _needs_patch,
    apply_windows_ime_patch,
)


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="仅 Windows 驱动需要")


def _key_event(virtual_key: int, control_state: int, character: str):
    from textual.drivers import win32

    event = win32.KEY_EVENT_RECORD()
    event.wVirtualKeyCode = virtual_key
    event.dwControlKeyState = control_state
    event.uChar.UnicodeChar = character
    return event


def test_shift_ime_character_is_not_treated_as_bare_modifier() -> None:
    event = _key_event(virtual_key=0, control_state=0x0010, character="\uff1f")

    assert _is_bare_modifier_key_event(event) is False


def test_null_modifier_event_is_dropped() -> None:
    event = _key_event(virtual_key=0, control_state=0x0010, character="\x00")

    assert _is_bare_modifier_key_event(event) is True


def test_patch_is_applied_once() -> None:
    from textual.drivers import win32

    if getattr(win32, "_xagent_ime_patch_applied", False):
        assert apply_windows_ime_patch() is False
        return

    buggy = _needs_patch(win32.EventMonitor.run)
    assert apply_windows_ime_patch() is buggy
    if buggy:
        assert win32.EventMonitor.__name__ == "ImeEventMonitor"
    assert apply_windows_ime_patch() is False
