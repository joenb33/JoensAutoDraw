from __future__ import annotations

from typing import Iterable

import cv2
import numpy as np

from autopaint.types import Point, Polyline, Segment


def _row_to_segments(row: np.ndarray, y: int, max_line_gap: int) -> list[Segment]:
    segments: list[Segment] = []
    x = 0
    width = row.shape[0]

    while x < width:
        if row[x] == 0:
            x += 1
            continue

        x_start = x
        gap_count = 0

        while x < width:
            if row[x] > 0:
                gap_count = 0
            else:
                gap_count += 1
                if gap_count > max_line_gap:
                    x -= gap_count
                    break
            x += 1

        x_end = min(x, width - 1)
        if x_end > x_start:
            segments.append(Segment(y=y, x_start=x_start, x_end=x_end))
        x += 1

    return segments


def mask_to_segments(mask: np.ndarray, sample_step: int, max_line_gap: int) -> list[Segment]:
    height, _ = mask.shape
    all_segments: list[Segment] = []

    for y in range(0, height, sample_step):
        row = mask[y, :]
        all_segments.extend(_row_to_segments(row=row, y=y, max_line_gap=max_line_gap))

    return all_segments


def render_segment_preview(
    mask: np.ndarray, segments: Iterable[Segment], thickness: int = 1
) -> np.ndarray:
    preview = np.zeros_like(mask)
    for seg in segments:
        cv2.line(
            preview,
            (seg.x_start, seg.y),
            (seg.x_end, seg.y),
            color=255,
            thickness=max(1, int(thickness)),
            lineType=cv2.LINE_8,
        )
    return preview


def mask_to_contour_polylines(mask: np.ndarray, min_points: int = 2) -> list[Polyline]:
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    polylines: list[Polyline] = []
    for contour in contours:
        if contour.shape[0] < min_points:
            continue
        points = tuple(Point(x=int(p[0][0]), y=int(p[0][1])) for p in contour)
        if len(points) >= min_points:
            polylines.append(Polyline(points=points))
    return polylines


def simplify_polylines(polylines: list[Polyline], epsilon: float) -> list[Polyline]:
    simplified: list[Polyline] = []
    for polyline in polylines:
        contour = np.array([[[p.x, p.y]] for p in polyline.points], dtype=np.int32)
        approx = cv2.approxPolyDP(contour, epsilon=epsilon, closed=False)
        points = tuple(Point(x=int(p[0][0]), y=int(p[0][1])) for p in approx)
        if len(points) >= 2:
            simplified.append(Polyline(points=points))
    return simplified


def render_polyline_preview(
    mask: np.ndarray, polylines: Iterable[Polyline], thickness: int = 1
) -> np.ndarray:
    preview = np.zeros_like(mask)
    for polyline in polylines:
        for idx in range(1, len(polyline.points)):
            a = polyline.points[idx - 1]
            b = polyline.points[idx]
            cv2.line(
                preview,
                (a.x, a.y),
                (b.x, b.y),
                color=255,
                thickness=max(1, int(thickness)),
                lineType=cv2.LINE_8,
            )
    return preview

