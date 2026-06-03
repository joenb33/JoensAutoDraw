from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from autopaint.config import ProcessingConfig
from autopaint.image_processing import apply_morphology, build_binary_mask_from_gray


def test_morph_close_fills_small_hole() -> None:
    mask = np.full((40, 40), 255, dtype=np.uint8)
    mask[20, 20] = 0

    closed = apply_morphology(mask, close_kernel=5, open_kernel=0)

    assert closed[20, 20] == 255


def test_build_binary_mask_from_gray_supports_otsu(temp_square_image: Path) -> None:
    gray = cv2.imread(str(temp_square_image), cv2.IMREAD_GRAYSCALE)
    config = ProcessingConfig(image_path=temp_square_image, use_otsu=True)

    mask = build_binary_mask_from_gray(gray, config)

    assert mask.shape == gray.shape
    assert mask.max() == 255
    assert mask.min() == 0
