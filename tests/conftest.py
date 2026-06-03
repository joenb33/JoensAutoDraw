from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest


@pytest.fixture
def horizontal_stroke_mask() -> np.ndarray:
    mask = np.zeros((20, 40), dtype=np.uint8)
    mask[10, 5:35] = 255
    return mask


@pytest.fixture
def square_mask() -> np.ndarray:
    mask = np.zeros((100, 100), dtype=np.uint8)
    cv2.rectangle(mask, (20, 20), (80, 80), 255, -1)
    return mask


@pytest.fixture
def temp_square_image(tmp_path: Path) -> Path:
    image_path = tmp_path / "square.png"
    mask = np.zeros((100, 100), dtype=np.uint8)
    cv2.rectangle(mask, (20, 20), (80, 80), 255, -1)
    cv2.imwrite(str(image_path), mask)
    return image_path
