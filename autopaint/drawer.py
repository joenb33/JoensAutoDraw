from __future__ import annotations

import math
import json
from dataclasses import dataclass
from typing import Iterable
import time
import ctypes

import pyautogui

from autopaint.config import DrawConfig
from autopaint.failsafe import check_emergency_stop
from autopaint.types import Point, Polyline, Rect, Segment, ToolCommand


class BoundsViolation(Exception):
    """Raised when drawing coordinates leave the selected rectangle."""


@dataclass(frozen=True)
class RasterSize:
    width: int
    height: int


@dataclass(frozen=True)
class FitTransform:
    scale: float
    offset_x: float
    offset_y: float


@dataclass(frozen=True)
class DrawSimulation:
    move_events: int
    draw_pixel_events: int
    stroke_count: int
    estimated_seconds: float


def normalize_rect(x1: int, y1: int, x2: int, y2: int) -> Rect:
    left, right = sorted((x1, x2))
    top, bottom = sorted((y1, y2))
    return Rect(left=left, top=top, right=right, bottom=bottom)


def _build_fit_transform(src: RasterSize, target: Rect) -> FitTransform:
    if src.width <= 0 or src.height <= 0:
        raise ValueError("Source dimensions must be positive.")
    if target.width <= 0 or target.height <= 0:
        raise ValueError("Target rectangle must have positive size.")

    src_span_x = max(src.width - 1, 1)
    src_span_y = max(src.height - 1, 1)
    scale_x = target.width / src_span_x
    scale_y = target.height / src_span_y
    scale = min(scale_x, scale_y)

    draw_width = src_span_x * scale
    draw_height = src_span_y * scale
    offset_x = target.left + (target.width - draw_width) / 2.0
    offset_y = target.top + (target.height - draw_height) / 2.0
    return FitTransform(scale=scale, offset_x=offset_x, offset_y=offset_y)


def map_to_screen(
    x: int, y: int, src: RasterSize, target: Rect, fit: FitTransform | None = None
) -> tuple[int, int]:
    transform = fit if fit is not None else _build_fit_transform(src=src, target=target)
    screen_x = int(round(transform.offset_x + x * transform.scale))
    screen_y = int(round(transform.offset_y + y * transform.scale))
    return screen_x, screen_y


def _map_to_screen_float(x: int, y: int, fit: FitTransform) -> tuple[float, float]:
    return fit.offset_x + x * fit.scale, fit.offset_y + y * fit.scale


# Step length in screen pixels along each float segment so rounding does not collapse
# adjacent source points to the same mouse pixel (common when preview looks full but
# drawing shows only dots).
_SCREEN_WALK_STEP = 0.65


_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_DEBUG_LOG_ENABLED = False
_TRACE_MOVE_EVENTS = False
_TRACE_MAX_EVENT_LOGS = 0


def _debug_log(hypothesis_id: str, message: str, data: dict) -> None:
    if not _DEBUG_LOG_ENABLED:
        return
    # region agent log
    payload = {
        "sessionId": "8cfe1f",
        "runId": "gui-fluid-check",
        "hypothesisId": hypothesis_id,
        "location": "autopaint/drawer.py",
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    try:
        with open("debug-8cfe1f.log", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=True) + "\n")
    except Exception:
        pass
    # endregion


def _expand_float_segment(
    fx0: float, fy0: float, fx1: float, fy1: float, step: float = _SCREEN_WALK_STEP
) -> list[tuple[int, int]]:
    """Walk from (fx0,fy0) to (fx1,fy1) in float space; emit unique integer pixels."""
    dx = fx1 - fx0
    dy = fy1 - fy0
    dist = math.hypot(dx, dy)
    if dist < 1e-9:
        px, py = int(round(fx0)), int(round(fy0))
        return [(px, py)]

    ux, uy = dx / dist, dy / dist
    out: list[tuple[int, int]] = []
    travelled = 0.0
    while travelled <= dist + 1e-6:
        x = fx0 + ux * travelled
        y = fy0 + uy * travelled
        px, py = int(round(x)), int(round(y))
        if not out or (px, py) != out[-1]:
            out.append((px, py))
        if travelled >= dist:
            break
        travelled = min(travelled + step, dist)

    ex, ey = int(round(fx1)), int(round(fy1))
    if not out or (ex, ey) != out[-1]:
        out.append((ex, ey))
    return out


def _polyline_to_screen_pixel_path(
    points: tuple[Point, ...], fit: FitTransform
) -> list[tuple[int, int]]:
    if len(points) < 2:
        return []

    out: list[tuple[int, int]] = []
    prev_fx, prev_fy = _map_to_screen_float(points[0].x, points[0].y, fit)

    for pt in points[1:]:
        fx, fy = _map_to_screen_float(pt.x, pt.y, fit)
        seg = _expand_float_segment(prev_fx, prev_fy, fx, fy)
        if not out:
            out.extend(seg)
        else:
            for p in seg:
                if p != out[-1]:
                    out.append(p)
        prev_fx, prev_fy = fx, fy

    return out


def _segment_to_screen_pixel_path(
    x_start: int, x_end: int, y: int, fit: FitTransform
) -> list[tuple[int, int]]:
    fx0, fy0 = _map_to_screen_float(x_start, y, fit)
    fx1, fy1 = _map_to_screen_float(x_end, y, fit)
    return _expand_float_segment(fx0, fy0, fx1, fy1)


def _sleep_if_needed(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def _distance_sq(a: tuple[int, int], b: tuple[int, int]) -> int:
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return dx * dx + dy * dy


def _reverse_polyline(polyline: Polyline) -> Polyline:
    return Polyline(points=tuple(reversed(polyline.points)))


def _order_polylines_for_travel(
    polylines: list[Polyline], fit: FitTransform
) -> list[Polyline]:
    if len(polylines) <= 1:
        return polylines

    def endpoint_pixels(polyline: Polyline) -> tuple[tuple[int, int], tuple[int, int]]:
        first = polyline.points[0]
        last = polyline.points[-1]
        return (
            (int(round(fit.offset_x + first.x * fit.scale)), int(round(fit.offset_y + first.y * fit.scale))),
            (int(round(fit.offset_x + last.x * fit.scale)), int(round(fit.offset_y + last.y * fit.scale))),
        )

    remaining = [p for p in polylines if len(p.points) >= 2]
    if not remaining:
        return polylines

    # Stable and deterministic start point near top-left.
    remaining.sort(key=lambda p: (p.points[0].y, p.points[0].x))
    ordered: list[Polyline] = [remaining.pop(0)]
    _, current_end = endpoint_pixels(ordered[0])

    while remaining:
        best_idx = 0
        best_reverse = False
        best_dist = None

        for idx, candidate in enumerate(remaining):
            start_px, end_px = endpoint_pixels(candidate)
            dist_start = _distance_sq(current_end, start_px)
            dist_end = _distance_sq(current_end, end_px)
            reverse = dist_end < dist_start
            candidate_dist = dist_end if reverse else dist_start
            if best_dist is None or candidate_dist < best_dist:
                best_dist = candidate_dist
                best_idx = idx
                best_reverse = reverse

        selected = remaining.pop(best_idx)
        if best_reverse:
            selected = _reverse_polyline(selected)
        ordered.append(selected)
        _, current_end = endpoint_pixels(selected)

    return ordered


def _assert_in_bounds(x: int, y: int, rect: Rect) -> None:
    if not rect.contains(x, y):
        raise BoundsViolation(f"Point ({x}, {y}) out of bounds for target rect {rect}.")


def _move_cursor_fast(x: int, y: int) -> None:
    ctypes.windll.user32.SetCursorPos(int(x), int(y))


def _mouse_left_down_fast() -> None:
    ctypes.windll.user32.mouse_event(_MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)


def _mouse_left_up_fast() -> None:
    ctypes.windll.user32.mouse_event(_MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def _move_cursor(x: int, y: int, draw_config: DrawConfig) -> None:
    if draw_config.compatibility_mode:
        # Hybrid compatibility: keep pyautogui for button semantics but use fast
        # cursor moves to avoid dragTo overhead on dense paths.
        _move_cursor_fast(x, y)
        return
    _move_cursor_fast(x, y)


def _mouse_left_down(draw_config: DrawConfig) -> None:
    if draw_config.compatibility_mode:
        pyautogui.mouseDown()
        return
    _mouse_left_down_fast()


def _mouse_left_up(draw_config: DrawConfig) -> None:
    if draw_config.compatibility_mode:
        pyautogui.mouseUp()
        return
    _mouse_left_up_fast()


def _compatibility_stride(draw_config: DrawConfig) -> int:
    # In compatibility mode (pyautogui drag backend), per-point calls are expensive.
    # Use an adaptive stride so the speed slider has strong visible effect.
    if draw_config.move_duration <= 0.003:
        return 32
    if draw_config.move_duration <= 0.006:
        return 24
    if draw_config.move_duration <= 0.01:
        return 16
    if draw_config.move_duration <= 0.02:
        return 10
    if draw_config.move_duration <= 0.03:
        return 6
    return 1


def _densify_point_path(
    points: list[tuple[int, int]], max_step: float = _SCREEN_WALK_STEP
) -> list[tuple[int, int]]:
    """Fill gaps between sparse anchors so apps like MS Paint see a continuous drag."""
    if len(points) <= 1:
        return points
    out: list[tuple[int, int]] = [points[0]]
    for x1, y1 in points[1:]:
        x0, y0 = out[-1]
        segment = _expand_float_segment(float(x0), float(y0), float(x1), float(y1), step=max_step)
        for px, py in segment[1:]:
            if out[-1] != (px, py):
                out.append((px, py))
    return out


def _prepare_drag_points(
    points: list[tuple[int, int]], draw_config: DrawConfig
) -> list[tuple[int, int]]:
    if len(points) <= 1:
        return points
    if not draw_config.compatibility_mode:
        return points
    stride = _compatibility_stride(draw_config)
    if stride <= 1 or len(points) <= 3:
        return points
    anchors = points[::stride]
    if anchors[-1] != points[-1]:
        anchors.append(points[-1])
    if len(anchors) <= 1:
        return points
    return _densify_point_path(anchors)


def _decimate_points_for_compatibility(
    points: list[tuple[int, int]], draw_config: DrawConfig
) -> list[tuple[int, int]]:
    return _prepare_drag_points(points, draw_config)


def _drag_cursor(x: int, y: int, draw_config: DrawConfig) -> None:
    if draw_config.compatibility_mode:
        _move_cursor_fast(x, y)
        if draw_config.step_pause_seconds > 0:
            _sleep_if_needed(min(draw_config.step_pause_seconds, 0.001))
        return
    _move_cursor_fast(x, y)
    _sleep_if_needed(draw_config.step_pause_seconds)


def draw_segments(
    segments: Iterable[Segment],
    source_size: RasterSize,
    target_rect: Rect,
    draw_config: DrawConfig,
) -> None:
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0
    pyautogui.MINIMUM_DURATION = 0
    pyautogui.MINIMUM_SLEEP = 0
    run_started = time.perf_counter()
    fit = _build_fit_transform(src=source_size, target=target_rect)
    _debug_log(
        "H11",
        "draw_segments start",
        {
            "mode": "segments",
            "source_width": source_size.width,
            "source_height": source_size.height,
            "target_width": target_rect.width,
            "target_height": target_rect.height,
            "fit_scale": fit.scale,
            "move_duration": draw_config.move_duration,
            "pyautogui_minimum_duration": getattr(pyautogui, "MINIMUM_DURATION", None),
            "backend": "win32_setcursorpos",
            "dry_run": draw_config.dry_run,
        },
    )
    segments_seen = 0
    drawn_paths = 0
    skipped_short = 0
    total_path_pixels = 0
    effective_path_pixels = 0
    mouse_down_count = 0
    stop_check_seconds = 0.0
    move_seconds = 0.0
    interrupted = False
    move_events = 0
    trace_event_logs = 0
    trace_event_logs_dropped = 0
    _debug_log(
        "H15",
        "segments deep trace config",
        {
            "move_duration": draw_config.move_duration,
            "step_pause_seconds": draw_config.step_pause_seconds,
            "stroke_settle_seconds": draw_config.stroke_settle_seconds,
        },
    )
    try:
        for segment_idx, segment in enumerate(segments):
            segments_seen += 1
            stop_t0 = time.perf_counter()
            check_emergency_stop()
            stop_check_seconds += time.perf_counter() - stop_t0

            path = _segment_to_screen_pixel_path(
                x_start=segment.x_start, x_end=segment.x_end, y=segment.y, fit=fit
            )
            if len(path) < 2:
                skipped_short += 1
                continue
            drawn_paths += 1
            total_path_pixels += len(path)

            for px, py in path:
                _assert_in_bounds(px, py, target_rect)

            if draw_config.dry_run:
                sx, sy = path[0]
                ex, ey = path[-1]
                print(f"DRY-RUN: segment pixels={len(path)} from ({sx},{sy}) to ({ex},{ey})")
                continue

            effective_path_pixels += len(path)
            if _TRACE_MOVE_EVENTS and trace_event_logs < _TRACE_MAX_EVENT_LOGS:
                _debug_log(
                    "H15",
                    "segment path prepared",
                    {
                        "segment_idx": segment_idx,
                        "source_y": segment.y,
                        "source_x_start": segment.x_start,
                        "source_x_end": segment.x_end,
                        "raw_path_pixels": len(path),
                        "effective_path_pixels": len(path),
                        "path_start": path[0],
                        "path_end": path[-1],
                    },
                )
                trace_event_logs += 1
            elif _TRACE_MOVE_EVENTS:
                trace_event_logs_dropped += 1
            sx, sy = path[0]
            pos_before_start = pyautogui.position()
            move_t0 = time.perf_counter()
            _move_cursor(sx, sy, draw_config)
            move_seconds += time.perf_counter() - move_t0
            _sleep_if_needed(draw_config.move_duration)
            pos_after_start = pyautogui.position()
            move_events += 1
            if _TRACE_MOVE_EVENTS and trace_event_logs < _TRACE_MAX_EVENT_LOGS:
                _debug_log(
                    "H15",
                    "segment moveTo start",
                    {
                        "segment_idx": segment_idx,
                        "move_event_idx": move_events,
                        "target": [sx, sy],
                        "cursor_before": [pos_before_start.x, pos_before_start.y],
                        "cursor_after": [pos_after_start.x, pos_after_start.y],
                    },
                )
                trace_event_logs += 1
            elif _TRACE_MOVE_EVENTS:
                trace_event_logs_dropped += 1
            stop_t0 = time.perf_counter()
            check_emergency_stop()
            stop_check_seconds += time.perf_counter() - stop_t0
            _mouse_left_down(draw_config)
            _sleep_if_needed(draw_config.stroke_settle_seconds)
            mouse_down_count += 1
            try:
                draw_points = _decimate_points_for_compatibility(path[1:], draw_config)
                for px, py in draw_points:
                    stop_t0 = time.perf_counter()
                    check_emergency_stop()
                    stop_check_seconds += time.perf_counter() - stop_t0
                    pos_before = pyautogui.position()
                    move_t0 = time.perf_counter()
                    _drag_cursor(px, py, draw_config)
                    move_seconds += time.perf_counter() - move_t0
                    pos_after = pyautogui.position()
                    move_events += 1
                    if _TRACE_MOVE_EVENTS and trace_event_logs < _TRACE_MAX_EVENT_LOGS:
                        _debug_log(
                            "H15",
                            "segment moveTo step",
                            {
                                "segment_idx": segment_idx,
                                "move_event_idx": move_events,
                                "target": [px, py],
                                "cursor_before": [pos_before.x, pos_before.y],
                                "cursor_after": [pos_after.x, pos_after.y],
                            },
                        )
                        trace_event_logs += 1
                    elif _TRACE_MOVE_EVENTS:
                        trace_event_logs_dropped += 1
            finally:
                _mouse_left_up(draw_config)
    except Exception as exc:
        interrupted = True
        pos = pyautogui.position()
        _debug_log(
            "H15",
            "segments interrupted",
            {
                "error_type": type(exc).__name__,
                "error": str(exc),
                "cursor_at_interrupt": [pos.x, pos.y],
                "segments_seen": segments_seen,
                "drawn_paths": drawn_paths,
                "move_events": move_events,
            },
        )
        raise
    finally:
        _debug_log(
            "H11",
            "draw_segments end",
            {
                "elapsed_seconds": round(time.perf_counter() - run_started, 3),
                "segments_seen": segments_seen,
                "drawn_paths": drawn_paths,
                "skipped_short": skipped_short,
                "total_path_pixels": total_path_pixels,
                "effective_path_pixels": effective_path_pixels,
                "mouse_down_count": mouse_down_count,
                "move_events": move_events,
                "trace_event_logs": trace_event_logs,
                "trace_event_logs_dropped": trace_event_logs_dropped,
                "stop_check_seconds": round(stop_check_seconds, 3),
                "move_seconds": round(move_seconds, 3),
                "interrupted": interrupted,
            },
        )


def draw_polylines(
    polylines: Iterable[Polyline],
    source_size: RasterSize,
    target_rect: Rect,
    draw_config: DrawConfig,
) -> None:
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0
    pyautogui.MINIMUM_DURATION = 0
    pyautogui.MINIMUM_SLEEP = 0
    run_started = time.perf_counter()
    fit = _build_fit_transform(src=source_size, target=target_rect)
    _debug_log(
        "H12",
        "draw_polylines start",
        {
            "mode": "contour",
            "source_width": source_size.width,
            "source_height": source_size.height,
            "target_width": target_rect.width,
            "target_height": target_rect.height,
            "fit_scale": fit.scale,
            "move_duration": draw_config.move_duration,
            "pyautogui_minimum_duration": getattr(pyautogui, "MINIMUM_DURATION", None),
            "backend": "win32_setcursorpos",
            "dry_run": draw_config.dry_run,
        },
    )
    polylines_seen = 0
    drawn_paths = 0
    skipped_short = 0
    total_path_pixels = 0
    effective_path_pixels = 0
    mouse_down_count = 0
    stop_check_seconds = 0.0
    move_seconds = 0.0
    interrupted = False
    move_events = 0
    trace_event_logs = 0
    trace_event_logs_dropped = 0
    polyline_list = list(polylines)
    if draw_config.optimize_contour_travel:
        polyline_list = _order_polylines_for_travel(polyline_list, fit)
    _debug_log(
        "H15",
        "contour deep trace config",
        {
            "move_duration": draw_config.move_duration,
            "step_pause_seconds": draw_config.step_pause_seconds,
            "stroke_settle_seconds": draw_config.stroke_settle_seconds,
            "optimize_contour_travel": draw_config.optimize_contour_travel,
        },
    )
    try:
        for polyline_idx, polyline in enumerate(polyline_list):
            polylines_seen += 1
            if len(polyline.points) < 2:
                continue

            stop_t0 = time.perf_counter()
            check_emergency_stop()
            stop_check_seconds += time.perf_counter() - stop_t0

            path = _polyline_to_screen_pixel_path(polyline.points, fit)
            if len(path) < 2:
                skipped_short += 1
                continue
            drawn_paths += 1
            total_path_pixels += len(path)

            for px, py in path:
                _assert_in_bounds(px, py, target_rect)

            if draw_config.dry_run:
                sx, sy = path[0]
                ex, ey = path[-1]
                print(
                    f"DRY-RUN: polyline pixels={len(path)} "
                    f"from ({sx},{sy}) to ({ex},{ey}) src_pts={len(polyline.points)}"
                )
                continue

            effective_path_pixels += len(path)
            if _TRACE_MOVE_EVENTS and trace_event_logs < _TRACE_MAX_EVENT_LOGS:
                _debug_log(
                    "H15",
                    "polyline path prepared",
                    {
                        "polyline_idx": polyline_idx,
                        "source_points": len(polyline.points),
                        "raw_path_pixels": len(path),
                        "effective_path_pixels": len(path),
                        "path_start": path[0],
                        "path_end": path[-1],
                    },
                )
                trace_event_logs += 1
            elif _TRACE_MOVE_EVENTS:
                trace_event_logs_dropped += 1
            sx, sy = path[0]
            pos_before_start = pyautogui.position()
            move_t0 = time.perf_counter()
            _move_cursor(sx, sy, draw_config)
            move_seconds += time.perf_counter() - move_t0
            _sleep_if_needed(draw_config.move_duration)
            pos_after_start = pyautogui.position()
            move_events += 1
            if _TRACE_MOVE_EVENTS and trace_event_logs < _TRACE_MAX_EVENT_LOGS:
                _debug_log(
                    "H15",
                    "polyline moveTo start",
                    {
                        "polyline_idx": polyline_idx,
                        "move_event_idx": move_events,
                        "target": [sx, sy],
                        "cursor_before": [pos_before_start.x, pos_before_start.y],
                        "cursor_after": [pos_after_start.x, pos_after_start.y],
                    },
                )
                trace_event_logs += 1
            elif _TRACE_MOVE_EVENTS:
                trace_event_logs_dropped += 1
            _mouse_left_down(draw_config)
            _sleep_if_needed(draw_config.stroke_settle_seconds)
            mouse_down_count += 1
            try:
                draw_points = _decimate_points_for_compatibility(path[1:], draw_config)
                for px, py in draw_points:
                    stop_t0 = time.perf_counter()
                    check_emergency_stop()
                    stop_check_seconds += time.perf_counter() - stop_t0
                    pos_before = pyautogui.position()
                    move_t0 = time.perf_counter()
                    _drag_cursor(px, py, draw_config)
                    move_seconds += time.perf_counter() - move_t0
                    pos_after = pyautogui.position()
                    move_events += 1
                    if _TRACE_MOVE_EVENTS and trace_event_logs < _TRACE_MAX_EVENT_LOGS:
                        _debug_log(
                            "H15",
                            "polyline moveTo step",
                            {
                                "polyline_idx": polyline_idx,
                                "move_event_idx": move_events,
                                "target": [px, py],
                                "cursor_before": [pos_before.x, pos_before.y],
                                "cursor_after": [pos_after.x, pos_after.y],
                            },
                        )
                        trace_event_logs += 1
                    elif _TRACE_MOVE_EVENTS:
                        trace_event_logs_dropped += 1
            finally:
                _mouse_left_up(draw_config)
    except Exception as exc:
        interrupted = True
        pos = pyautogui.position()
        _debug_log(
            "H15",
            "contour interrupted",
            {
                "error_type": type(exc).__name__,
                "error": str(exc),
                "cursor_at_interrupt": [pos.x, pos.y],
                "polylines_seen": polylines_seen,
                "drawn_paths": drawn_paths,
                "move_events": move_events,
            },
        )
        raise
    finally:
        _debug_log(
            "H12",
            "draw_polylines end",
            {
                "elapsed_seconds": round(time.perf_counter() - run_started, 3),
                "polylines_seen": polylines_seen,
                "drawn_paths": drawn_paths,
                "skipped_short": skipped_short,
                "total_path_pixels": total_path_pixels,
                "effective_path_pixels": effective_path_pixels,
                "mouse_down_count": mouse_down_count,
                "move_events": move_events,
                "trace_event_logs": trace_event_logs,
                "trace_event_logs_dropped": trace_event_logs_dropped,
                "stop_check_seconds": round(stop_check_seconds, 3),
                "move_seconds": round(move_seconds, 3),
                "interrupted": interrupted,
            },
        )


def _append_unique_points(
    target: list[tuple[int, int]], points: Iterable[tuple[int, int]]
) -> None:
    for px, py in points:
        if not target or target[-1] != (px, py):
            target.append((px, py))


def _execute_stroke_pixels(
    stroke_pixels: list[tuple[int, int]],
    target_rect: Rect,
    draw_config: DrawConfig,
) -> None:
    if len(stroke_pixels) < 1:
        return

    drag_points = _prepare_drag_points(stroke_pixels, draw_config)
    if len(drag_points) < 1:
        return

    start_x, start_y = drag_points[0]
    _move_cursor(start_x, start_y, draw_config)
    _sleep_if_needed(draw_config.move_duration)
    _mouse_left_down(draw_config)
    _sleep_if_needed(draw_config.stroke_settle_seconds)
    try:
        for px, py in drag_points[1:]:
            check_emergency_stop()
            _assert_in_bounds(px, py, target_rect)
            _drag_cursor(px, py, draw_config)
    finally:
        _mouse_left_up(draw_config)


def _simulate_tool_command_paths(
    commands: Iterable[ToolCommand],
    fit: FitTransform,
    draw_config: DrawConfig,
) -> DrawSimulation:
    move_events = 0
    draw_pixel_events = 0
    stroke_count = 0
    estimated_seconds = 0.0
    pen_is_down = False
    last_float_pos: tuple[float, float] | None = None
    stroke_pixels: list[tuple[int, int]] = []

    def finish_stroke() -> None:
        nonlocal pen_is_down, stroke_pixels, draw_pixel_events, estimated_seconds
        if not pen_is_down:
            return
        drag_points = _prepare_drag_points(stroke_pixels, draw_config)
        draw_pixel_events += max(0, len(drag_points) - 1)
        estimated_seconds += draw_config.stroke_settle_seconds
        estimated_seconds += max(0, len(drag_points) - 1) * draw_config.step_pause_seconds
        pen_is_down = False
        stroke_pixels = []

    for cmd in commands:
        if cmd.kind in {"move", "draw"}:
            if cmd.x is None or cmd.y is None:
                continue
            fx, fy = _map_to_screen_float(cmd.x, cmd.y, fit)

            if cmd.kind == "move":
                finish_stroke()
                move_events += 1
                estimated_seconds += draw_config.move_duration
            elif cmd.kind == "draw":
                if not pen_is_down:
                    stroke_count += 1
                    estimated_seconds += draw_config.stroke_settle_seconds
                    pen_is_down = True
                    stroke_pixels = []
                if not stroke_pixels and last_float_pos is not None:
                    anchor = (
                        int(round(last_float_pos[0])),
                        int(round(last_float_pos[1])),
                    )
                    _append_unique_points(stroke_pixels, [anchor])
                if last_float_pos is not None:
                    path = _expand_float_segment(last_float_pos[0], last_float_pos[1], fx, fy)
                    _append_unique_points(stroke_pixels, path[1:] if len(path) > 1 else path)

            last_float_pos = (fx, fy)
            continue

        if cmd.kind == "down" and not pen_is_down:
            stroke_count += 1
            pen_is_down = True
            stroke_pixels = []
        elif cmd.kind == "up" and pen_is_down:
            finish_stroke()

    if pen_is_down:
        finish_stroke()

    return DrawSimulation(
        move_events=move_events,
        draw_pixel_events=draw_pixel_events,
        stroke_count=stroke_count,
        estimated_seconds=estimated_seconds,
    )


def simulate_tool_commands(
    commands: Iterable[ToolCommand],
    source_size: RasterSize,
    target_rect: Rect,
    draw_config: DrawConfig,
) -> DrawSimulation:
    fit = _build_fit_transform(src=source_size, target=target_rect)
    stats = _simulate_tool_command_paths(commands=commands, fit=fit, draw_config=draw_config)
    return DrawSimulation(
        move_events=stats.move_events,
        draw_pixel_events=stats.draw_pixel_events,
        stroke_count=stats.stroke_count,
        estimated_seconds=stats.estimated_seconds + draw_config.countdown_seconds,
    )


def draw_tool_commands(
    commands: Iterable[ToolCommand],
    source_size: RasterSize,
    target_rect: Rect,
    draw_config: DrawConfig,
) -> None:
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0
    pyautogui.MINIMUM_DURATION = 0
    pyautogui.MINIMUM_SLEEP = 0
    fit = _build_fit_transform(src=source_size, target=target_rect)
    pen_is_down = False
    last_float_pos: tuple[float, float] | None = None
    stroke_pixels: list[tuple[int, int]] = []

    def finish_stroke() -> None:
        nonlocal pen_is_down, stroke_pixels
        if not pen_is_down:
            return
        if draw_config.dry_run:
            drag_count = len(_prepare_drag_points(stroke_pixels, draw_config))
            print(
                f"DRY-RUN: stroke raw_pixels={len(stroke_pixels)} "
                f"drag_steps={max(0, drag_count - 1)}"
            )
        elif stroke_pixels:
            _execute_stroke_pixels(stroke_pixels, target_rect, draw_config)
        pen_is_down = False
        stroke_pixels = []

    try:
        for cmd in commands:
            check_emergency_stop()
            if cmd.kind in {"move", "draw"}:
                if cmd.x is None or cmd.y is None:
                    continue
                fx, fy = _map_to_screen_float(cmd.x, cmd.y, fit)
                sx, sy = int(round(fx)), int(round(fy))
                _assert_in_bounds(sx, sy, target_rect)

                if draw_config.dry_run:
                    print(f"DRY-RUN: {cmd.kind} ({sx},{sy})")
                    if cmd.kind == "move":
                        finish_stroke()
                    elif cmd.kind == "draw":
                        if not pen_is_down:
                            pen_is_down = True
                        if not stroke_pixels and last_float_pos is not None:
                            anchor = (
                                int(round(last_float_pos[0])),
                                int(round(last_float_pos[1])),
                            )
                            _append_unique_points(stroke_pixels, [anchor])
                        if last_float_pos is not None:
                            path = _expand_float_segment(
                                last_float_pos[0], last_float_pos[1], fx, fy
                            )
                            _append_unique_points(
                                stroke_pixels, path[1:] if len(path) > 1 else path
                            )
                    last_float_pos = (fx, fy)
                    continue

                if cmd.kind == "move":
                    finish_stroke()
                    _move_cursor(sx, sy, draw_config)
                    _sleep_if_needed(draw_config.move_duration)
                elif cmd.kind == "draw":
                    if not pen_is_down:
                        pen_is_down = True
                    if not stroke_pixels and last_float_pos is not None:
                        anchor = (
                            int(round(last_float_pos[0])),
                            int(round(last_float_pos[1])),
                        )
                        _append_unique_points(stroke_pixels, [anchor])
                    if last_float_pos is not None:
                        path = _expand_float_segment(
                            last_float_pos[0], last_float_pos[1], fx, fy
                        )
                        _append_unique_points(
                            stroke_pixels, path[1:] if len(path) > 1 else path
                        )
                last_float_pos = (fx, fy)
                continue

            if draw_config.dry_run:
                print(f"DRY-RUN: {cmd.kind}")
                if cmd.kind == "up":
                    finish_stroke()
                elif cmd.kind == "down" and not pen_is_down:
                    pen_is_down = True
                    stroke_pixels = []
                continue

            if cmd.kind == "down" and not pen_is_down:
                pen_is_down = True
                stroke_pixels = []
            elif cmd.kind == "up" and pen_is_down:
                finish_stroke()
    except Exception:
        if not draw_config.dry_run:
            force_release_mouse(draw_config)
        raise
    finally:
        if pen_is_down and not draw_config.dry_run:
            finish_stroke()


def force_release_mouse(draw_config: DrawConfig | None = None) -> None:
    """Best-effort release so a failed/interrupted run does not leave the button held."""
    del draw_config
    try:
        pyautogui.mouseUp()
    except Exception:
        pass
    try:
        _mouse_left_up_fast()
    except Exception:
        pass


def capture_target_rect() -> Rect:
    input("Place mouse at target TOP-LEFT and press Enter...")
    x1, y1 = pyautogui.position()
    print(f"Captured top-left: ({x1}, {y1})")

    input("Place mouse at target BOTTOM-RIGHT and press Enter...")
    x2, y2 = pyautogui.position()
    print(f"Captured bottom-right: ({x2}, {y2})")

    rect = normalize_rect(x1, y1, x2, y2)
    if rect.width <= 0 or rect.height <= 0:
        raise ValueError("Invalid target rectangle. Width and height must be > 0.")
    return rect

