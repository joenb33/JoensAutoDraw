from __future__ import annotations

import numpy as np
import pytest

from autopaint.config import DrawConfig
from autopaint.drawer import (
    BoundsViolation,
    RasterSize,
    _build_fit_transform,
    _compatibility_stride,
    _drag_cursor,
    _expand_float_segment,
    _polyline_to_screen_pixel_path,
    _prepare_drag_points,
    map_to_screen,
    normalize_rect,
    render_execution_preview_on_mask,
    simulate_tool_commands,
)
from autopaint.toolpath import polylines_to_commands
from autopaint.types import Point, Polyline, Rect, ToolCommand


def test_normalize_rect_orders_corners() -> None:
    rect = normalize_rect(100, 200, 10, 20)

    assert rect.left == 10
    assert rect.top == 20
    assert rect.right == 100
    assert rect.bottom == 200


def test_build_fit_transform_preserves_aspect_ratio() -> None:
    fit = _build_fit_transform(
        src=RasterSize(width=100, height=50),
        target=Rect(left=0, top=0, right=200, bottom=200),
    )

    assert fit.scale == pytest.approx(200 / 99, rel=1e-3)
    assert fit.offset_x == pytest.approx(0.0, abs=1e-3)
    assert fit.offset_y > 0


def test_build_fit_transform_handles_single_row_source() -> None:
    fit = _build_fit_transform(
        src=RasterSize(width=100, height=1),
        target=Rect(left=0, top=0, right=200, bottom=200),
    )

    assert fit.scale > 0
    x0, y0 = map_to_screen(0, 0, src=RasterSize(width=100, height=1), target=Rect(left=0, top=0, right=200, bottom=200), fit=fit)
    x1, y1 = map_to_screen(99, 0, src=RasterSize(width=100, height=1), target=Rect(left=0, top=0, right=200, bottom=200), fit=fit)
    assert x1 > x0


def test_map_to_screen_centers_source_in_target() -> None:
    target = Rect(left=0, top=0, right=300, bottom=200)
    src = RasterSize(width=100, height=100)

    top_left = map_to_screen(0, 0, src=src, target=target)
    bottom_right = map_to_screen(99, 99, src=src, target=target)

    assert top_left[0] == pytest.approx(50, abs=1)
    assert top_left[1] == pytest.approx(0, abs=1)
    assert bottom_right[0] == pytest.approx(249, abs=1)
    assert bottom_right[1] == pytest.approx(199, abs=1)


def test_expand_float_segment_emits_multiple_pixels_for_short_source_step() -> None:
    pixels = _expand_float_segment(10.0, 10.0, 10.0, 20.0, step=0.65)

    assert len(pixels) > 2
    assert pixels[0] == (10, 10)
    assert pixels[-1][1] == 20


def test_polyline_to_screen_pixel_path_avoids_dot_collapse() -> None:
    fit = _build_fit_transform(
        src=RasterSize(width=100, height=100),
        target=Rect(left=0, top=0, right=50, bottom=50),
    )
    polyline = Polyline(
        points=(
            Point(x=20, y=50),
            Point(x=80, y=50),
        )
    )

    path = _polyline_to_screen_pixel_path(polyline.points, fit)

    assert len(path) > 10


def test_assert_in_bounds_raises_outside_target() -> None:
    from autopaint.drawer import _assert_in_bounds

    rect = Rect(left=10, top=10, right=20, bottom=20)

    with pytest.raises(BoundsViolation):
        _assert_in_bounds(5, 15, rect)


def test_simulate_tool_commands_counts_draw_pixels() -> None:
    commands = polylines_to_commands(
        [
            Polyline(
                points=(
                    Point(x=0, y=0),
                    Point(x=40, y=0),
                )
            )
        ]
    )
    draw_config = DrawConfig(
        move_duration=0.01,
        step_pause_seconds=0.002,
        stroke_settle_seconds=0.003,
        countdown_seconds=0,
        compatibility_mode=True,
    )
    stats = simulate_tool_commands(
        commands=commands,
        source_size=RasterSize(width=41, height=2),
        target_rect=Rect(left=0, top=0, right=400, bottom=40),
        draw_config=draw_config,
    )

    assert stats.move_events >= 1
    assert stats.draw_pixel_events > 10
    assert stats.stroke_count == 1
    assert stats.estimated_seconds > 0


def test_drag_cursor_uses_fast_moves_in_compatibility_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"fast": 0, "moveTo": 0}

    def _fast(x: int, y: int) -> None:
        calls["fast"] += 1

    def _move_to(*args: object, **kwargs: object) -> None:
        calls["moveTo"] += 1

    monkeypatch.setattr("autopaint.drawer._move_cursor_fast", _fast)
    monkeypatch.setattr("autopaint.drawer.pyautogui.moveTo", _move_to)

    draw_config = DrawConfig(
        compatibility_mode=True,
        move_duration=0.0,
        step_pause_seconds=0.0,
        countdown_seconds=0,
    )
    _drag_cursor(10, 20, draw_config)

    assert calls["fast"] == 1
    assert calls["moveTo"] == 0


def test_compatibility_stride_keeps_dense_path_at_high_speed() -> None:
    draw_config = DrawConfig(compatibility_mode=True, move_duration=0.0)
    assert _compatibility_stride(draw_config) == 1

    path = [(x, 0) for x in range(200)]
    prepared = _prepare_drag_points(path, draw_config)
    assert len(prepared) >= 150


def test_render_execution_preview_reflects_screen_mapping() -> None:
    mask = np.zeros((40, 40), dtype=np.uint8)
    polyline = Polyline(
        points=(
            Point(x=5, y=20),
            Point(x=35, y=20),
        )
    )
    preview = render_execution_preview_on_mask(
        mask,
        [polyline],
        source_size=RasterSize(width=40, height=40),
        target_rect=Rect(left=0, top=0, right=400, bottom=400),
        draw_config=DrawConfig(compatibility_mode=True, move_duration=0.0),
        thickness=1,
    )
    assert int(np.count_nonzero(preview)) > 20


def test_contour_stroke_survives_batch_decimation() -> None:
    """Many short draw segments must produce a dense continuous drag path."""
    from autopaint.toolpath import polylines_to_commands

    polyline = Polyline(points=tuple(Point(x=i, y=0) for i in range(80)))
    commands = polylines_to_commands([polyline])
    draw_config = DrawConfig(
        compatibility_mode=True,
        move_duration=0.002,
        countdown_seconds=0,
    )

    stats = simulate_tool_commands(
        commands=commands,
        source_size=RasterSize(width=80, height=2),
        target_rect=Rect(left=0, top=0, right=800, bottom=80),
        draw_config=draw_config,
    )

    assert stats.draw_pixel_events >= 100


def test_simulate_matches_command_sequence_structure() -> None:
    commands = [
        ToolCommand(kind="move", x=0, y=0),
        ToolCommand(kind="down"),
        ToolCommand(kind="draw", x=10, y=0),
        ToolCommand(kind="up"),
    ]
    draw_config = DrawConfig(countdown_seconds=2)

    stats = simulate_tool_commands(
        commands=commands,
        source_size=RasterSize(width=11, height=2),
        target_rect=Rect(left=0, top=0, right=110, bottom=10),
        draw_config=draw_config,
    )

    assert stats.move_events == 1
    assert stats.stroke_count == 1
    assert stats.estimated_seconds == pytest.approx(
        2 + draw_config.move_duration + draw_config.stroke_settle_seconds
        + stats.draw_pixel_events * draw_config.step_pause_seconds,
        rel=1e-6,
    )
