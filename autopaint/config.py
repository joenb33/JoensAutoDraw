from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ContourMode = Literal["external", "all", "largest"]
TraceMode = Literal["threshold", "sketch"]


@dataclass(frozen=True)
class ProcessingConfig:
    image_path: Path
    threshold: int = 140
    blur_kernel: int = 5
    sample_step: int = 2
    max_line_gap: int = 2
    invert: bool = False
    vector_sample_step: float = 1.0
    vector_min_polyline_points: int = 2
    vector_jump_threshold_px: float = 8.0
    use_otsu: bool = False
    morph_close_kernel: int = 0
    morph_open_kernel: int = 0
    contour_mode: ContourMode = "external"
    min_contour_area: int = 25
    auto_exif_rotate: bool = True
    use_alpha_mask: bool = True
    clahe_clip_limit: float = 0.0
    auto_scale_epsilon: bool = True
    trace_mode: TraceMode = "threshold"
    canny_low: int = 40
    canny_high: int = 120


@dataclass(frozen=True)
class DrawConfig:
    move_duration: float = 0.002
    countdown_seconds: int = 3
    dry_run: bool = False
    step_pause_seconds: float = 0.0
    stroke_settle_seconds: float = 0.0
    optimize_contour_travel: bool = True
    compatibility_mode: bool = True
