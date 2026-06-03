from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from autopaint.config import DrawConfig, ProcessingConfig
from autopaint.drawer import BoundsViolation
from autopaint.failsafe import EmergencyStop, esc_backend_description
from autopaint.validation import DrawValidationError
from autopaint.image_processing import show_preview
from autopaint.pipeline import create_plan, execute_draw


_DEBUG_LOG_ENABLED = False


def _debug_log(hypothesis_id: str, message: str, data: dict) -> None:
    if not _DEBUG_LOG_ENABLED:
        return
    # region agent log
    payload = {
        "sessionId": "8cfe1f",
        "runId": "initial",
        "hypothesisId": hypothesis_id,
        "location": "autopaint/main.py",
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


def _odd_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0 or parsed % 2 == 0:
        raise argparse.ArgumentTypeError("Must be a positive odd integer.")
    return parsed


def _int_min(min_value: int):
    def _parse(value: str) -> int:
        parsed = int(value)
        if parsed < min_value:
            raise argparse.ArgumentTypeError(f"Must be >= {min_value}.")
        return parsed

    return _parse


def _float_min(min_value: float):
    def _parse(value: str) -> float:
        parsed = float(value)
        if parsed < min_value:
            raise argparse.ArgumentTypeError(f"Must be >= {min_value}.")
        return parsed

    return _parse


def _threshold_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0 or parsed > 255:
        raise argparse.ArgumentTypeError("Must be in range 0..255.")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AutoPaint raster/vector-to-mouse drawer")
    parser.add_argument(
        "--image",
        type=Path,
        required=True,
        help="Path to input source (.png/.jpg/.svg/.gcode/.nc/.tap).",
    )
    parser.add_argument(
        "--threshold", type=_threshold_int, default=140, help="Threshold value 0..255."
    )
    parser.add_argument("--blur", type=_odd_int, default=5, help="Odd Gaussian blur kernel.")
    parser.add_argument("--step", type=_int_min(1), default=2, help="Row sampling step.")
    parser.add_argument(
        "--line-gap",
        type=_int_min(0),
        default=2,
        help="Max white-gap allowed inside stroke.",
    )
    parser.add_argument(
        "--vector-step",
        type=_float_min(0.5),
        default=1.0,
        help="Sampling step in pixels for SVG/G-code paths.",
    )
    parser.add_argument(
        "--vector-min-points",
        type=_int_min(2),
        default=2,
        help="Drop vector paths with fewer points than this.",
    )
    parser.add_argument(
        "--vector-jump-threshold",
        type=_float_min(1.0),
        default=8.0,
        help="Split SVG sampled paths at large jumps (pen-up detection).",
    )
    parser.add_argument(
        "--speed", type=_float_min(0.0), default=0.002, help="Mouse move duration."
    )
    parser.add_argument(
        "--countdown", type=_int_min(0), default=3, help="Countdown before draw."
    )
    parser.add_argument(
        "--step-pause-ms",
        type=_float_min(0.0),
        default=1.2,
        help="Pause between emitted mouse pixels (ms).",
    )
    parser.add_argument(
        "--stroke-settle-ms",
        type=_float_min(0.0),
        default=3.0,
        help="Pause after mouse-down before stroke steps (ms).",
    )
    parser.add_argument(
        "--no-optimize-contour-travel",
        action="store_true",
        help="Disable contour path travel optimization.",
    )
    parser.add_argument(
        "--fast-input-backend",
        action="store_true",
        help="Use low-level fast input backend (less compatible with some apps).",
    )
    parser.add_argument("--preview", action="store_true", help="Show preview windows.")
    parser.add_argument("--dry-run", action="store_true", help="Print draw actions only.")
    parser.add_argument("--invert", action="store_true", help="Invert threshold behavior.")
    parser.add_argument(
        "--otsu",
        action="store_true",
        help="Use Otsu automatic threshold instead of fixed --threshold.",
    )
    parser.add_argument(
        "--morph-close",
        type=_int_min(0),
        default=0,
        help="Morphological close kernel (0=off, odd values fill gaps).",
    )
    parser.add_argument(
        "--morph-open",
        type=_int_min(0),
        default=0,
        help="Morphological open kernel (0=off, odd values remove speckle).",
    )
    parser.add_argument(
        "--contour-mode",
        choices=("external", "all", "largest"),
        default="external",
        help="Contour extraction scope for CNC mode.",
    )
    parser.add_argument(
        "--min-contour-area",
        type=_int_min(0),
        default=25,
        help="Ignore contours smaller than this area in pixels.",
    )
    parser.add_argument(
        "--no-auto-exif-rotate",
        action="store_true",
        help="Disable automatic EXIF orientation correction on import.",
    )
    parser.add_argument(
        "--no-alpha-mask",
        action="store_true",
        help="Ignore transparent alpha channels when building the mask.",
    )
    parser.add_argument(
        "--clahe",
        type=_float_min(0.0),
        default=0.0,
        help="CLAHE contrast clip limit before threshold (0=off, try 2-4 for photos).",
    )
    parser.add_argument(
        "--no-auto-scale-epsilon",
        action="store_true",
        help="Disable contour epsilon scaling for large images.",
    )
    parser.add_argument(
        "--mode",
        choices=("segments", "contour"),
        default="contour",
        help="Drawing mode. contour is usually cleaner.",
    )
    parser.add_argument(
        "--contour-epsilon",
        type=_float_min(0.0),
        default=1.2,
        help="Contour simplification amount. Higher means fewer points.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    _debug_log(
        "H1",
        "Parsed CLI args",
        {
            "image": str(args.image),
            "threshold": args.threshold,
            "blur": args.blur,
            "step": args.step,
            "line_gap": args.line_gap,
            "vector_step": args.vector_step,
            "vector_min_points": args.vector_min_points,
            "vector_jump_threshold": args.vector_jump_threshold,
            "speed": args.speed,
            "countdown": args.countdown,
            "step_pause_ms": args.step_pause_ms,
            "stroke_settle_ms": args.stroke_settle_ms,
            "mode": args.mode,
            "contour_epsilon": args.contour_epsilon,
            "optimize_contour_travel": not args.no_optimize_contour_travel,
            "compatibility_mode": not args.fast_input_backend,
        },
    )

    processing = ProcessingConfig(
        image_path=args.image,
        threshold=args.threshold,
        blur_kernel=args.blur,
        sample_step=args.step,
        max_line_gap=args.line_gap,
        invert=args.invert,
        vector_sample_step=args.vector_step,
        vector_min_polyline_points=args.vector_min_points,
        vector_jump_threshold_px=args.vector_jump_threshold,
        use_otsu=args.otsu,
        morph_close_kernel=args.morph_close,
        morph_open_kernel=args.morph_open,
        contour_mode=args.contour_mode,
        min_contour_area=args.min_contour_area,
        auto_exif_rotate=not args.no_auto_exif_rotate,
        use_alpha_mask=not args.no_alpha_mask,
        clahe_clip_limit=args.clahe,
        auto_scale_epsilon=not args.no_auto_scale_epsilon,
    )
    draw_conf = DrawConfig(
        move_duration=args.speed,
        countdown_seconds=args.countdown,
        dry_run=args.dry_run,
        step_pause_seconds=args.step_pause_ms / 1000.0,
        stroke_settle_seconds=args.stroke_settle_ms / 1000.0,
        optimize_contour_travel=not args.no_optimize_contour_travel,
        compatibility_mode=not args.fast_input_backend,
    )
    _debug_log(
        "H2",
        "Constructed configs",
        {
            "sample_step": processing.sample_step,
            "threshold": processing.threshold,
            "blur_kernel": processing.blur_kernel,
            "move_duration": draw_conf.move_duration,
            "countdown_seconds": draw_conf.countdown_seconds,
            "dry_run": draw_conf.dry_run,
            "step_pause_seconds": draw_conf.step_pause_seconds,
            "stroke_settle_seconds": draw_conf.stroke_settle_seconds,
            "optimize_contour_travel": draw_conf.optimize_contour_travel,
            "compatibility_mode": draw_conf.compatibility_mode,
        },
    )

    plan = create_plan(
        processing=processing,
        contour_epsilon=args.contour_epsilon,
    )
    _debug_log(
        "H3",
        "Plan created",
        {
            "source_width": plan.source_width,
            "source_height": plan.source_height,
            "segment_count": plan.segment_count,
            "polyline_count": plan.polyline_count,
        },
    )

    print(f"Loaded image: {processing.image_path}")
    print(f"Source size: {plan.source_width}x{plan.source_height}")
    print(f"Generated {plan.segment_count} stroke segments")
    print(f"Generated {plan.polyline_count} contour polylines")
    print(f"Generated {plan.command_count} tool commands")
    print(f"Selected mode: {args.mode}")
    for warning in plan.import_warnings:
        print(f"Import note: {warning}")
    print(f"Emergency stop: ESC ({esc_backend_description()}) or move cursor to top-left corner")

    if args.preview:
        show_preview(
            gray=plan.gray,
            binary=plan.mask,
            planned=plan.preview_polylines if args.mode == "contour" else plan.preview_segments,
        )

    execute_draw(
        draw_mode=args.mode,
        plan=plan,
        draw_config=draw_conf,
    )
    _debug_log(
        "H4",
        "Draw execution finished",
        {"draw_mode": args.mode},
    )

    print("Drawing completed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except EmergencyStop as stop:
        _debug_log("H4", "Emergency stop caught", {"error": str(stop)})
        print(f"Stopped safely: {stop}")
        raise SystemExit(2)
    except DrawValidationError as validation_error:
        print(f"Preflight blocked draw: {validation_error}")
        raise SystemExit(4)
    except BoundsViolation as bounds:
        _debug_log("H5", "Bounds violation caught", {"error": str(bounds)})
        print(f"Safety stop: {bounds}")
        raise SystemExit(3)
    except Exception as exc:
        _debug_log(
            "H1",
            "Unhandled exception in CLI",
            {"error_type": type(exc).__name__, "error": str(exc)},
        )
        raise

