from __future__ import annotations

from typing import Iterable

from autopaint.config import DrawConfig
from autopaint.drawer import (
    RasterSize,
    _build_fit_transform,
    _expand_float_segment,
    _map_to_screen_float,
)
from autopaint.types import Rect, ToolCommand

MIN_TARGET_SIZE_PX = 5
FAILSAFE_CORNER_MARGIN_PX = 8


class DrawValidationError(Exception):
    """Raised when preflight checks fail before mouse control starts."""


def validate_target_rect(rect: Rect) -> list[str]:
    warnings: list[str] = []
    if rect.width < MIN_TARGET_SIZE_PX or rect.height < MIN_TARGET_SIZE_PX:
        raise DrawValidationError(
            f"Draw area is too small ({rect.width}x{rect.height}px). "
            f"Select at least {MIN_TARGET_SIZE_PX}x{MIN_TARGET_SIZE_PX}px."
        )
    if rect.left < FAILSAFE_CORNER_MARGIN_PX and rect.top < FAILSAFE_CORNER_MARGIN_PX:
        warnings.append(
            "Draw area is near the screen's top-left corner. "
            "PyAutoGUI fail-safe may trigger accidentally during drawing."
        )
    return warnings


def validate_plan_has_draw_content(plan, draw_mode: str) -> None:
    if draw_mode == "segments" and plan.segments:
        return
    if draw_mode == "hatch" and getattr(plan, "hatch_polylines", None):
        return
    if plan.polylines:
        return
    if plan.segments:
        return
    raise DrawValidationError(
        "No drawable paths in the current plan. "
        "Adjust threshold/step settings or choose a different source."
    )


def validate_commands_non_empty(commands: list[ToolCommand]) -> None:
    if not commands:
        raise DrawValidationError(
            "Execution plan produced zero draw commands. "
            "Try contour mode, lower threshold, or reduce contour simplification."
        )


def validate_commands_screen_bounds(
    commands: list[ToolCommand],
    source_size: RasterSize,
    target_rect: Rect,
    draw_config: DrawConfig,
) -> None:
    del draw_config
    fit = _build_fit_transform(src=source_size, target=target_rect)
    last_float_pos: tuple[float, float] | None = None
    stroke_pixels: list[tuple[int, int]] = []

    def check_points(points: Iterable[tuple[int, int]]) -> None:
        for px, py in points:
            if not target_rect.contains(px, py):
                raise DrawValidationError(
                    f"Stroke path pixel ({px}, {py}) leaves the selected draw area. "
                    "Reselect a larger area."
                )

    for cmd in commands:
        if cmd.kind in {"move", "draw"}:
            if cmd.x is None or cmd.y is None:
                continue

            fx, fy = _map_to_screen_float(cmd.x, cmd.y, fit)
            sx, sy = int(round(fx)), int(round(fy))
            if not target_rect.contains(sx, sy):
                raise DrawValidationError(
                    f"Mapped point ({sx}, {sy}) is outside the selected draw area. "
                    "Reselect a larger area or use a smaller source."
                )

            if cmd.kind == "move":
                if stroke_pixels:
                    check_points(stroke_pixels)
                    stroke_pixels = []
            elif cmd.kind == "draw" and last_float_pos is not None:
                path = _expand_float_segment(last_float_pos[0], last_float_pos[1], fx, fy)
                segment = path[1:] if len(path) > 1 else path
                for px, py in segment:
                    if not stroke_pixels or stroke_pixels[-1] != (px, py):
                        stroke_pixels.append((px, py))

            last_float_pos = (fx, fy)
            continue

        if cmd.kind == "up" and stroke_pixels:
            check_points(stroke_pixels)
            stroke_pixels = []

    if stroke_pixels:
        check_points(stroke_pixels)
