from __future__ import annotations

import sys
import time

import ctypes

try:
    import keyboard
except ImportError:  # pragma: no cover - optional fallback path
    keyboard = None  # type: ignore[assignment]


class EmergencyStop(Exception):
    """Raised when a user-triggered stop is detected."""


_VK_ESCAPE = 0x1B


def esc_backend_description() -> str:
    backends: list[str] = []
    if sys.platform == "win32":
        backends.append("Win32 GetAsyncKeyState")
    if keyboard is not None:
        backends.append("keyboard")
    if not backends:
        return "none (use PyAutoGUI top-left fail-safe only)"
    return " + ".join(backends)


def _esc_pressed_win32() -> bool:
    if sys.platform != "win32":
        return False
    state = ctypes.windll.user32.GetAsyncKeyState(_VK_ESCAPE)
    return bool(state & 0x8000)


def _esc_pressed_keyboard() -> bool:
    if keyboard is None:
        return False
    return bool(keyboard.is_pressed("esc"))


def is_esc_pressed() -> bool:
    if _esc_pressed_win32():
        return True
    try:
        return _esc_pressed_keyboard()
    except Exception:
        return False


def check_emergency_stop() -> None:
    if is_esc_pressed():
        raise EmergencyStop("Emergency stop triggered by ESC key.")


def interruptible_sleep(seconds: float, step_seconds: float = 0.05) -> None:
    if seconds <= 0:
        return
    deadline = time.monotonic() + seconds
    while True:
        check_emergency_stop()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(step_seconds, remaining))


def countdown(seconds: int) -> None:
    for i in range(seconds, 0, -1):
        check_emergency_stop()
        print(f"Starting in {i}... (ESC to cancel)")
        interruptible_sleep(1.0)
