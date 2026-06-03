from __future__ import annotations

from typing import Iterable

from autopaint.types import Point, Polyline, Segment, ToolCommand


def _append_draw_points(points: Iterable[Point], out: list[ToolCommand]) -> None:
    first = True
    for pt in points:
        if first:
            out.append(ToolCommand(kind="move", x=pt.x, y=pt.y))
            out.append(ToolCommand(kind="down"))
            first = False
        else:
            out.append(ToolCommand(kind="draw", x=pt.x, y=pt.y))
    if not first:
        out.append(ToolCommand(kind="up"))


def polylines_to_commands(
    polylines: Iterable[Polyline], min_polyline_points: int = 2
) -> list[ToolCommand]:
    commands: list[ToolCommand] = []
    min_points = max(2, int(min_polyline_points))
    for polyline in polylines:
        if len(polyline.points) < min_points:
            continue
        _append_draw_points(polyline.points, commands)
    return commands


def segments_to_commands(segments: Iterable[Segment]) -> list[ToolCommand]:
    commands: list[ToolCommand] = []
    for seg in segments:
        if seg.x_end <= seg.x_start:
            continue
        points = (
            Point(x=seg.x_start, y=seg.y),
            Point(x=seg.x_end, y=seg.y),
        )
        _append_draw_points(points, commands)
    return commands
