from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from autopaint.config import DrawConfig, ProcessingConfig
from autopaint.drawer import (
    RasterSize,
    _build_fit_transform,
    _order_polylines_for_travel,
    capture_target_rect,
    draw_tool_commands,
    force_release_mouse,
)
from autopaint.failsafe import countdown
from autopaint.validation import (
    DrawValidationError,
    validate_commands_non_empty,
    validate_commands_screen_bounds,
    validate_plan_has_draw_content,
    validate_target_rect,
)
from autopaint.image_processing import (
    VECTOR_SUFFIXES,
    build_binary_mask_from_prepared,
    prepare_raster_image,
)
from autopaint.types import Rect
from autopaint.planner import (
    mask_to_contour_polylines,
    mask_to_segments,
    render_polyline_preview,
    render_segment_preview,
    scaled_contour_epsilon,
    simplify_polylines,
    tune_polylines_for_draw,
)
from autopaint.vector_import import load_gcode_polylines, load_svg_polylines
from autopaint.toolpath import polylines_to_commands, segments_to_commands
from autopaint.types import ToolCommand


@dataclass(frozen=True)
class PlanResult:
    source_kind: str
    source_width: int
    source_height: int
    segment_count: int
    polyline_count: int
    command_count: int
    preview_segments: Any
    preview_polylines: Any
    mask: Any
    gray: Any
    polylines: Any
    segments: Any
    tool_commands: Any
    import_warnings: tuple[str, ...] = ()


def create_plan(processing: ProcessingConfig, contour_epsilon: float) -> PlanResult:
    suffix = processing.image_path.suffix.lower()
    if suffix in VECTOR_SUFFIXES:
        return _create_vector_plan(processing=processing)

    prepared = prepare_raster_image(
        processing.image_path,
        auto_exif_rotate=processing.auto_exif_rotate,
    )
    gray = prepared.gray
    mask = build_binary_mask_from_prepared(prepared, processing)
    effective_epsilon = scaled_contour_epsilon(
        contour_epsilon,
        width=gray.shape[1],
        height=gray.shape[0],
        auto_scale=processing.auto_scale_epsilon,
    )

    segments = mask_to_segments(
        mask=mask, sample_step=processing.sample_step, max_line_gap=processing.max_line_gap
    )
    raw_polylines = mask_to_contour_polylines(
        mask=mask,
        min_points=3,
        contour_mode=processing.contour_mode,
        min_contour_area=processing.min_contour_area,
    )
    polylines = simplify_polylines(raw_polylines, epsilon=effective_epsilon)
    polylines = tune_polylines_for_draw(
        polylines,
        min_points=processing.vector_min_polyline_points,
        sample_step_px=processing.vector_sample_step,
    )

    preview_segments = render_segment_preview(mask=mask, segments=segments)
    preview_polylines = render_polyline_preview(mask=mask, polylines=polylines)
    contour_commands = polylines_to_commands(polylines=polylines, min_polyline_points=2)

    return PlanResult(
        source_kind="image",
        source_width=gray.shape[1],
        source_height=gray.shape[0],
        segment_count=len(segments),
        polyline_count=len(polylines),
        command_count=len(contour_commands),
        preview_segments=preview_segments,
        preview_polylines=preview_polylines,
        mask=mask,
        gray=gray,
        polylines=polylines,
        segments=segments,
        tool_commands=contour_commands,
        import_warnings=prepared.warnings,
    )


def _create_vector_plan(processing: ProcessingConfig) -> PlanResult:
    suffix = processing.image_path.suffix.lower()
    if suffix == ".svg":
        polylines, width, height = load_svg_polylines(
            processing.image_path,
            sample_step_px=max(0.5, float(processing.vector_sample_step)),
            min_polyline_points=max(2, int(processing.vector_min_polyline_points)),
            jump_threshold_px=max(1.0, float(processing.vector_jump_threshold_px)),
        )
        source_kind = "svg"
    else:
        polylines, width, height = load_gcode_polylines(
            processing.image_path,
            sample_step_px=max(0.5, float(processing.vector_sample_step)),
            min_polyline_points=max(2, int(processing.vector_min_polyline_points)),
        )
        source_kind = "gcode"

    mask = np.zeros((height, width), dtype=np.uint8)
    preview_polylines = render_polyline_preview(mask=mask, polylines=polylines)
    gray = cv2.bitwise_not(preview_polylines)
    commands = polylines_to_commands(polylines=polylines, min_polyline_points=2)

    return PlanResult(
        source_kind=source_kind,
        source_width=width,
        source_height=height,
        segment_count=0,
        polyline_count=len(polylines),
        command_count=len(commands),
        preview_segments=np.zeros_like(mask),
        preview_polylines=preview_polylines,
        mask=mask,
        gray=gray,
        polylines=polylines,
        segments=[],
        tool_commands=commands,
    )


def resolve_draw_polylines(
    plan: PlanResult,
    draw_mode: str,
    draw_config: DrawConfig,
    target_rect: Rect,
    source_size: RasterSize,
) -> list:
    """Return contour polylines used for drawing (with optional travel ordering)."""
    if draw_mode == "segments" and plan.segments:
        return []

    polylines = list(plan.polylines)
    if draw_config.optimize_contour_travel and polylines:
        fit = _build_fit_transform(src=source_size, target=target_rect)
        polylines = _order_polylines_for_travel(polylines, fit)
    return polylines


def build_execution_commands(
    plan: PlanResult,
    draw_mode: str,
    draw_config: DrawConfig,
    target_rect: Rect,
    source_size: RasterSize,
) -> list[ToolCommand]:
    """Build the exact tool-command list used during mouse execution."""
    if draw_mode == "segments" and plan.segments:
        return segments_to_commands(plan.segments)

    polylines = resolve_draw_polylines(
        plan=plan,
        draw_mode=draw_mode,
        draw_config=draw_config,
        target_rect=target_rect,
        source_size=source_size,
    )
    return polylines_to_commands(polylines=polylines, min_polyline_points=2)


def preflight_draw(
    draw_mode: str,
    plan: PlanResult,
    draw_config: DrawConfig,
    target_rect: Rect,
) -> tuple[list[ToolCommand], list[str]]:
    """Validate inputs and build the exact command list before mouse control."""
    warnings = validate_target_rect(target_rect)
    validate_plan_has_draw_content(plan=plan, draw_mode=draw_mode)

    source_size = RasterSize(width=plan.source_width, height=plan.source_height)
    commands = build_execution_commands(
        plan=plan,
        draw_mode=draw_mode,
        draw_config=draw_config,
        target_rect=target_rect,
        source_size=source_size,
    )
    validate_commands_non_empty(commands)
    validate_commands_screen_bounds(
        commands=commands,
        source_size=source_size,
        target_rect=target_rect,
        draw_config=draw_config,
    )
    return commands, warnings


def execute_draw(
    draw_mode: str,
    plan: PlanResult,
    draw_config: DrawConfig,
    target_rect: Rect | None = None,
) -> None:
    target = target_rect if target_rect is not None else capture_target_rect()
    source_size = RasterSize(width=plan.source_width, height=plan.source_height)
    commands, warnings = preflight_draw(
        draw_mode=draw_mode,
        plan=plan,
        draw_config=draw_config,
        target_rect=target,
    )
    for warning in warnings:
        print(f"Warning: {warning}")

    countdown(draw_config.countdown_seconds)

    try:
        draw_tool_commands(
            commands=commands,
            source_size=source_size,
            target_rect=target,
            draw_config=draw_config,
        )
    except Exception:
        force_release_mouse(draw_config)
        raise
