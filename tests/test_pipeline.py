from __future__ import annotations

from autopaint.config import DrawConfig, ProcessingConfig
from autopaint.drawer import RasterSize
from autopaint.pipeline import build_execution_commands, create_plan, resolve_draw_polylines
from autopaint.types import Point, Polyline, Rect


def test_create_plan_from_image(temp_square_image) -> None:
    plan = create_plan(
        processing=ProcessingConfig(image_path=temp_square_image),
        contour_epsilon=1.2,
    )

    assert plan.source_kind == "image"
    assert plan.source_width == 100
    assert plan.source_height == 100
    assert plan.polyline_count >= 1
    assert plan.segment_count > 0
    assert plan.command_count > 0
    assert isinstance(plan.import_warnings, tuple)


def test_build_execution_commands_uses_segments_in_segment_mode(temp_square_image) -> None:
    plan = create_plan(
        processing=ProcessingConfig(image_path=temp_square_image),
        contour_epsilon=1.2,
    )
    draw_config = DrawConfig(optimize_contour_travel=True)
    target = Rect(left=0, top=0, right=400, bottom=400)
    source_size = RasterSize(width=plan.source_width, height=plan.source_height)

    segment_commands = build_execution_commands(
        plan=plan,
        draw_mode="segments",
        draw_config=draw_config,
        target_rect=target,
        source_size=source_size,
    )
    contour_commands = build_execution_commands(
        plan=plan,
        draw_mode="contour",
        draw_config=draw_config,
        target_rect=target,
        source_size=source_size,
    )

    assert len(segment_commands) > 0
    assert segment_commands != contour_commands


def test_build_execution_commands_uses_hatch_paths_in_hatch_mode(temp_square_image) -> None:
    plan = create_plan(
        processing=ProcessingConfig(
            image_path=temp_square_image,
            hatch_spacing=8,
            hatch_angle_degrees=45,
        ),
        contour_epsilon=1.2,
    )
    draw_config = DrawConfig(optimize_contour_travel=True)
    target = Rect(left=0, top=0, right=400, bottom=400)
    source_size = RasterSize(width=plan.source_width, height=plan.source_height)

    hatch_commands = build_execution_commands(
        plan=plan,
        draw_mode="hatch",
        draw_config=draw_config,
        target_rect=target,
        source_size=source_size,
    )
    contour_commands = build_execution_commands(
        plan=plan,
        draw_mode="contour",
        draw_config=draw_config,
        target_rect=target,
        source_size=source_size,
    )

    assert plan.hatch_count > 0
    assert len(hatch_commands) > 0
    assert hatch_commands != contour_commands


def test_create_plan_can_limit_work_to_contour_mode(temp_square_image) -> None:
    plan = create_plan(
        processing=ProcessingConfig(image_path=temp_square_image),
        contour_epsilon=1.2,
        draw_mode="contour",
    )

    assert plan.polyline_count > 0
    assert plan.segment_count == 0
    assert plan.hatch_count == 0
    assert plan.command_count > 0


def test_create_plan_can_limit_work_to_segments_mode(temp_square_image) -> None:
    plan = create_plan(
        processing=ProcessingConfig(image_path=temp_square_image),
        contour_epsilon=1.2,
        draw_mode="segments",
    )

    assert plan.segment_count > 0
    assert plan.polyline_count == 0
    assert plan.hatch_count == 0
    assert plan.command_count > 0


def test_create_plan_can_limit_work_to_hatch_mode(temp_square_image) -> None:
    plan = create_plan(
        processing=ProcessingConfig(image_path=temp_square_image, hatch_spacing=8),
        contour_epsilon=1.2,
        draw_mode="hatch",
    )

    assert plan.hatch_count > 0
    assert plan.segment_count == 0
    assert plan.polyline_count == 0
    assert plan.command_count > 0


def test_hatch_mode_falls_back_to_contours_when_no_hatch_paths() -> None:
    polyline = Polyline(points=(Point(x=0, y=0), Point(x=10, y=0)))
    plan_like = type(
        "PlanStub",
        (),
        {
            "segments": [],
            "hatch_polylines": [],
            "polylines": [polyline],
        },
    )()
    draw_config = DrawConfig(optimize_contour_travel=False)
    target = Rect(left=0, top=0, right=100, bottom=100)
    source_size = RasterSize(width=20, height=20)

    hatch_commands = build_execution_commands(
        plan=plan_like,
        draw_mode="hatch",
        draw_config=draw_config,
        target_rect=target,
        source_size=source_size,
    )
    contour_commands = build_execution_commands(
        plan=plan_like,
        draw_mode="contour",
        draw_config=draw_config,
        target_rect=target,
        source_size=source_size,
    )

    assert hatch_commands == contour_commands


def test_resolve_draw_polylines_can_reorder_for_travel() -> None:
    plan_like = type(
        "PlanStub",
        (),
        {
            "segments": [],
            "polylines": [
                Polyline(points=(Point(x=0, y=0), Point(x=10, y=0))),
                Polyline(points=(Point(x=50, y=50), Point(x=60, y=50))),
            ],
        },
    )()
    draw_config = DrawConfig(optimize_contour_travel=True)
    target = Rect(left=0, top=0, right=200, bottom=200)
    source_size = RasterSize(width=100, height=100)

    ordered = resolve_draw_polylines(
        plan=plan_like,
        draw_mode="contour",
        draw_config=draw_config,
        target_rect=target,
        source_size=source_size,
    )

    assert len(ordered) == 2
    assert ordered[0].points[0].y <= ordered[1].points[0].y
