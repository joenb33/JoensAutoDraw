from __future__ import annotations

import sys

import pytest

from autopaint import main as mainmod
from autopaint.types import Rect


def test_target_rect_parser_accepts_comma_separated_rect() -> None:
    rect = mainmod._target_rect("10,20,300,400")

    assert rect == Rect(left=10, top=20, right=300, bottom=400)


def test_target_rect_parser_rejects_invalid_size() -> None:
    with pytest.raises(Exception, match="positive size"):
        mainmod._target_rect("10,20,10,400")


def test_main_dry_run_uses_default_target_without_mouse_prompt(
    temp_square_image, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = {}

    def fake_execute_draw(*, draw_mode, plan, draw_config, target_rect=None) -> None:
        captured["draw_mode"] = draw_mode
        captured["dry_run"] = draw_config.dry_run
        captured["target_rect"] = target_rect

    monkeypatch.setattr(mainmod, "execute_draw", fake_execute_draw)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "autopaint.main",
            "--image",
            str(temp_square_image),
            "--dry-run",
            "--countdown",
            "0",
        ],
    )

    assert mainmod.main() == 0
    assert captured["draw_mode"] == "contour"
    assert captured["dry_run"] is True
    assert captured["target_rect"] == Rect(left=10, top=10, right=109, bottom=109)
