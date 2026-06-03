from __future__ import annotations

import pytest

from autopaint.config import DrawConfig, ProcessingConfig
from autopaint.drawer import RasterSize
from autopaint.pipeline import build_execution_commands, create_plan, preflight_draw
from autopaint.types import Point, Polyline, Rect, ToolCommand
from autopaint.validation import (
    DrawValidationError,
    validate_commands_non_empty,
    validate_commands_screen_bounds,
    validate_plan_has_draw_content,
    validate_target_rect,
)


def test_validate_target_rect_rejects_tiny_area() -> None:
    with pytest.raises(DrawValidationError, match="too small"):
        validate_target_rect(Rect(left=0, top=0, right=3, bottom=20))


def test_validate_target_rect_warns_near_failsafe_corner() -> None:
    warnings = validate_target_rect(Rect(left=0, top=0, right=200, bottom=200))

    assert warnings
    assert "top-left corner" in warnings[0]


def test_validate_plan_has_draw_content_requires_paths() -> None:
    empty_plan = type(
        "PlanStub",
        (),
        {"segments": [], "polylines": []},
    )()

    with pytest.raises(DrawValidationError, match="No drawable paths"):
        validate_plan_has_draw_content(empty_plan, draw_mode="contour")


def test_validate_commands_non_empty() -> None:
    with pytest.raises(DrawValidationError, match="zero draw commands"):
        validate_commands_non_empty([])


def test_validate_commands_screen_bounds_detects_outside_point() -> None:
    commands = [ToolCommand(kind="move", x=10000, y=0)]
    draw_config = DrawConfig()

    with pytest.raises(DrawValidationError, match="outside the selected draw area"):
        validate_commands_screen_bounds(
            commands=commands,
            source_size=RasterSize(width=11, height=11),
            target_rect=Rect(left=0, top=0, right=100, bottom=100),
            draw_config=draw_config,
        )


def test_preflight_draw_accepts_valid_plan(temp_square_image) -> None:
    plan = create_plan(
        processing=ProcessingConfig(image_path=temp_square_image),
        contour_epsilon=1.2,
    )
    draw_config = DrawConfig()

    commands, warnings = preflight_draw(
        draw_mode="contour",
        plan=plan,
        draw_config=draw_config,
        target_rect=Rect(left=0, top=0, right=400, bottom=400),
    )

    assert commands
    assert isinstance(warnings, list)
