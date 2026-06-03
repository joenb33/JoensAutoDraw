from __future__ import annotations

import pytest

from autopaint.failsafe import EmergencyStop, check_emergency_stop, esc_backend_description


def test_esc_backend_description_is_non_empty_on_windows() -> None:
    description = esc_backend_description()

    assert description
    assert description != "none (use PyAutoGUI top-left fail-safe only)"


def test_check_emergency_stop_can_be_monkeypatched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("autopaint.failsafe.is_esc_pressed", lambda: True)

    with pytest.raises(EmergencyStop):
        check_emergency_stop()
