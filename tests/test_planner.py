from __future__ import annotations

import numpy as np

from autopaint.planner import (
    mask_to_contour_polylines,
    mask_to_segments,
    simplify_polylines,
)
from autopaint.types import Point, Polyline


def test_mask_to_segments_finds_horizontal_runs(horizontal_stroke_mask: np.ndarray) -> None:
    segments = mask_to_segments(
        mask=horizontal_stroke_mask,
        sample_step=1,
        max_line_gap=0,
    )

    assert len(segments) == 1
    assert segments[0].y == 10
    assert segments[0].x_start == 5
    assert segments[0].x_end == 34


def test_mask_to_segments_respects_sample_step(horizontal_stroke_mask: np.ndarray) -> None:
    segments = mask_to_segments(
        mask=horizontal_stroke_mask,
        sample_step=5,
        max_line_gap=0,
    )

    assert len(segments) == 1
    assert segments[0].y == 10


def test_mask_to_contour_polylines_finds_shape(square_mask: np.ndarray) -> None:
    polylines = mask_to_contour_polylines(mask=square_mask, min_points=3)

    assert len(polylines) >= 1
    assert all(len(polyline.points) >= 3 for polyline in polylines)


def test_simplify_polylines_reduces_point_count() -> None:
    polyline = Polyline(
        points=tuple(Point(x=x, y=0) for x in range(0, 100, 2)),
    )

    simplified = simplify_polylines([polyline], epsilon=2.0)

    assert len(simplified) == 1
    assert len(simplified[0].points) < len(polyline.points)
    assert simplified[0].points[0] == Point(x=0, y=0)
    assert simplified[0].points[-1] == Point(x=98, y=0)
