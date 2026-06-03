from __future__ import annotations

import math
import re
from pathlib import Path

try:
    from svgelements import SVG
except ImportError:  # pragma: no cover - dependency error path
    SVG = None

from autopaint.types import Point, Polyline

_GCODE_LINE_RE = re.compile(r"([GMTFSXYZIJR])\s*([-+]?\d*\.?\d+)", re.IGNORECASE)


def _sample_line(x0: float, y0: float, x1: float, y1: float, step: float) -> list[Point]:
    dx = x1 - x0
    dy = y1 - y0
    dist = math.hypot(dx, dy)
    if dist < 1e-9:
        return [Point(x=int(round(x0)), y=int(round(y0)))]

    samples = max(1, int(math.ceil(dist / max(0.5, step))))
    out: list[Point] = []
    for idx in range(samples + 1):
        t = idx / samples
        x = int(round(x0 + dx * t))
        y = int(round(y0 + dy * t))
        if not out or out[-1] != Point(x=x, y=y):
            out.append(Point(x=x, y=y))
    return out


def _normalize_polylines(polylines: list[Polyline]) -> tuple[list[Polyline], int, int]:
    non_empty = [p for p in polylines if len(p.points) >= 2]
    if not non_empty:
        raise ValueError("No drawable paths found in vector source.")

    min_x = min(pt.x for poly in non_empty for pt in poly.points)
    min_y = min(pt.y for poly in non_empty for pt in poly.points)
    shifted: list[Polyline] = []
    max_x = 0
    max_y = 0
    for poly in non_empty:
        points = tuple(Point(x=pt.x - min_x, y=pt.y - min_y) for pt in poly.points)
        max_x = max(max_x, max(pt.x for pt in points))
        max_y = max(max_y, max(pt.y for pt in points))
        shifted.append(Polyline(points=points))
    return shifted, max_x + 1, max_y + 1


def _split_on_large_jumps(points: list[Point], jump_threshold_px: float) -> list[Polyline]:
    if len(points) < 2:
        return []
    threshold_sq = jump_threshold_px * jump_threshold_px
    chunks: list[list[Point]] = [[points[0]]]
    for pt in points[1:]:
        prev = chunks[-1][-1]
        dx = pt.x - prev.x
        dy = pt.y - prev.y
        if dx * dx + dy * dy > threshold_sq:
            if len(chunks[-1]) >= 2:
                chunks.append([pt])
            else:
                chunks[-1] = [pt]
        else:
            chunks[-1].append(pt)
    return [Polyline(points=tuple(chunk)) for chunk in chunks if len(chunk) >= 2]


def load_svg_polylines(
    path: Path,
    sample_step_px: float = 1.0,
    min_polyline_points: int = 2,
    jump_threshold_px: float = 8.0,
) -> tuple[list[Polyline], int, int]:
    if SVG is None:
        raise RuntimeError(
            "SVG support requires 'svgelements'. Install with: pip install svgelements"
        )
    svg = SVG.parse(str(path))
    polylines: list[Polyline] = []

    for element in svg.elements():
        if not hasattr(element, "length") or not hasattr(element, "point"):
            continue
        try:
            length = float(element.length(error=1e-2))
        except Exception:
            continue
        if length <= 0:
            continue

        segments = max(2, int(math.ceil(length / max(0.5, sample_step_px))))
        points: list[Point] = []
        for idx in range(segments + 1):
            t = idx / segments
            try:
                pt = element.point(t)
            except Exception:
                continue
            x = int(round(float(pt.real)))
            y = int(round(float(pt.imag)))
            p = Point(x=x, y=y)
            if not points or points[-1] != p:
                points.append(p)

        if len(points) < 2:
            continue
        split_polylines = _split_on_large_jumps(
            points=points,
            jump_threshold_px=max(1.0, float(jump_threshold_px)),
        )
        for poly in split_polylines:
            if len(poly.points) >= max(2, int(min_polyline_points)):
                polylines.append(poly)

    return _normalize_polylines(polylines)


def load_gcode_polylines(
    path: Path,
    sample_step_px: float = 1.0,
    min_polyline_points: int = 2,
) -> tuple[list[Polyline], int, int]:
    x = 0.0
    y = 0.0
    pen_down = False
    current: list[Point] = []
    polylines: list[Polyline] = []

    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.split(";")[0].strip()
        if not line:
            continue
        if "(" in line:
            line = re.sub(r"\([^)]*\)", "", line).strip()
            if not line:
                continue

        tokens = {k.upper(): float(v) for k, v in _GCODE_LINE_RE.findall(line)}
        g = int(tokens["G"]) if "G" in tokens else None

        if "M" in tokens:
            m_code = int(tokens["M"])
            if m_code in {3, 4}:
                pen_down = True
            elif m_code == 5:
                pen_down = False
                if len(current) >= max(2, int(min_polyline_points)):
                    polylines.append(Polyline(points=tuple(current)))
                current = []

        nx = tokens.get("X", x)
        ny = tokens.get("Y", y)

        if g in {0, 1}:
            step_points = _sample_line(x, y, nx, ny, sample_step_px)
            if g == 0:
                if len(current) >= max(2, int(min_polyline_points)):
                    polylines.append(Polyline(points=tuple(current)))
                current = []
                pen_down = False
            else:
                if not pen_down:
                    pen_down = True
                for pt in step_points:
                    if not current or current[-1] != pt:
                        current.append(pt)

        x, y = nx, ny

    if len(current) >= max(2, int(min_polyline_points)):
        polylines.append(Polyline(points=tuple(current)))

    return _normalize_polylines(polylines)
