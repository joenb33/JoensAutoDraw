from __future__ import annotations

import cv2
import numpy as np

from autopaint.planner import (
    mask_to_contour_polylines,
    mask_to_segments,
    resample_polylines,
    simplify_polylines,
    tune_polylines_for_draw,
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
        points=(
            Point(x=0, y=0),
            Point(x=25, y=0),
            Point(x=25, y=25),
            Point(x=0, y=25),
            Point(x=0, y=0),
        ),
    )

    simplified = simplify_polylines([polyline], epsilon=2.0)

    assert len(simplified) == 1
    assert len(simplified[0].points) < len(polyline.points)


def test_mask_to_contour_polylines_external_ignores_hole(square_mask: np.ndarray) -> None:
    mask = square_mask.copy()
    cv2.circle(mask, (50, 50), 10, 0, -1)

    external = mask_to_contour_polylines(mask, contour_mode="external", min_contour_area=0)
    all_contours = mask_to_contour_polylines(mask, contour_mode="all", min_contour_area=0)

    assert len(external) <= len(all_contours)
    assert len(external) >= 1


def test_mask_to_contour_polylines_min_area_filters_speckle() -> None:
    mask = np.zeros((60, 60), dtype=np.uint8)
    cv2.rectangle(mask, (10, 10), (50, 50), 255, -1)
    mask[5, 5] = 255

    filtered = mask_to_contour_polylines(mask, contour_mode="all", min_contour_area=50)
    unfiltered = mask_to_contour_polylines(mask, contour_mode="all", min_contour_area=0)

    assert len(filtered) <= len(unfiltered)


def test_mask_to_contour_polylines_largest_keeps_one(square_mask: np.ndarray) -> None:
    mask = square_mask.copy()
    cv2.rectangle(mask, (2, 2), (12, 12), 255, -1)

    largest = mask_to_contour_polylines(mask, contour_mode="largest", min_contour_area=0)

    assert len(largest) == 1


def test_resample_polylines_changes_density() -> None:
    polyline = Polyline(points=(Point(x=0, y=0), Point(x=100, y=0)))
    dense = resample_polylines([polyline], step_px=1.0)[0]
    sparse = resample_polylines([polyline], step_px=10.0)[0]

    assert len(dense.points) > len(sparse.points)


def test_tune_polylines_for_draw_filters_and_resamples() -> None:
    polylines = [
        Polyline(points=(Point(x=0, y=0), Point(x=40, y=0))),
        Polyline(points=(Point(x=0, y=0),)),
    ]
    tuned = tune_polylines_for_draw(polylines, min_points=2, sample_step_px=2.0)

    assert len(tuned) == 1
    assert len(tuned[0].points) > 2
