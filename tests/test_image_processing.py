from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from autopaint.config import ProcessingConfig
from autopaint.image_processing import (
    apply_morphology,
    build_binary_mask_from_gray,
    build_binary_mask_from_prepared,
    prepare_raster_image,
)
from autopaint.planner import scaled_contour_epsilon


def test_morph_close_fills_small_hole() -> None:
    mask = np.full((40, 40), 255, dtype=np.uint8)
    mask[20, 20] = 0

    closed = apply_morphology(mask, close_kernel=5, open_kernel=0)

    assert closed[20, 20] == 255


def test_build_binary_mask_from_gray_supports_otsu(temp_square_image: Path) -> None:
    prepared = prepare_raster_image(temp_square_image)
    config = ProcessingConfig(image_path=temp_square_image, use_otsu=True)

    mask = build_binary_mask_from_gray(prepared.gray, config)

    assert mask.shape == prepared.gray.shape
    assert mask.max() == 255
    assert mask.min() == 0


def test_prepare_raster_warns_on_jpeg(tmp_path: Path) -> None:
    path = tmp_path / "photo.jpg"
    Image.new("RGB", (32, 32), color=(128, 128, 128)).save(path, format="JPEG")

    prepared = prepare_raster_image(path)

    assert prepared.gray.shape == (32, 32)
    assert any("Lossy" in warning for warning in prepared.warnings)


def test_alpha_mask_used_for_transparent_png(tmp_path: Path) -> None:
    path = tmp_path / "logo.png"
    image = Image.new("RGBA", (40, 40), color=(0, 0, 0, 0))
    for x in range(10, 30):
        for y in range(10, 30):
            image.putpixel((x, y), (255, 0, 0, 255))
    image.save(path)

    prepared = prepare_raster_image(path)
    config = ProcessingConfig(image_path=path, use_alpha_mask=True, use_otsu=False, threshold=140)
    mask = build_binary_mask_from_prepared(prepared, config)

    assert prepared.alpha is not None
    assert mask[20, 20] == 255
    assert mask[5, 5] == 0


def test_clahe_improves_local_contrast(tmp_path: Path) -> None:
    path = tmp_path / "flat.png"
    gray = np.full((60, 60), 120, dtype=np.uint8)
    gray[20:40, 20:40] = 140
    Image.fromarray(gray, mode="L").save(path)

    prepared = prepare_raster_image(path)
    plain = ProcessingConfig(image_path=path, clahe_clip_limit=0.0)
    enhanced = ProcessingConfig(image_path=path, clahe_clip_limit=3.0)
    mask_plain = build_binary_mask_from_prepared(prepared, plain)
    mask_enhanced = build_binary_mask_from_prepared(prepared, enhanced)

    assert not np.array_equal(mask_plain, mask_enhanced)


def test_scaled_contour_epsilon_grows_with_image_size() -> None:
    small = scaled_contour_epsilon(1.2, width=200, height=200, auto_scale=True)
    large = scaled_contour_epsilon(1.2, width=1200, height=1200, auto_scale=True)

    assert large > small
    assert scaled_contour_epsilon(1.2, width=1200, height=1200, auto_scale=False) == pytest.approx(1.2)


def test_sketch_trace_builds_edge_mask(tmp_path: Path) -> None:
    from autopaint.image_processing import build_sketch_mask_from_gray, prepare_raster_image

    path = tmp_path / "shape.png"
    gray = np.zeros((80, 80), dtype=np.uint8)
    gray[20:60, 20:60] = 220
    Image.fromarray(gray, mode="L").save(path)
    prepared = prepare_raster_image(path)
    config = ProcessingConfig(image_path=path, trace_mode="sketch", canny_low=20, canny_high=80)

    mask = build_sketch_mask_from_gray(prepared.gray, config)

    assert mask.shape == prepared.gray.shape
    assert mask.max() == 255
    assert int(mask.sum()) > 0


def test_create_plan_sketch_mode(temp_square_image) -> None:
    from autopaint.pipeline import create_plan

    plan = create_plan(
        processing=ProcessingConfig(image_path=temp_square_image, trace_mode="sketch"),
        contour_epsilon=1.2,
    )

    assert plan.polyline_count >= 1
