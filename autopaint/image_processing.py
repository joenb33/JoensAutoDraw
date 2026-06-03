from pathlib import Path

import cv2
import numpy as np

from autopaint.config import ProcessingConfig


def load_image_grayscale(image_path: Path) -> np.ndarray:
    """Load grayscale image. Uses imdecode so Unicode paths work on Windows.

    ``cv2.imread(str(path))`` often fails when the path contains non-ASCII
    characters (e.g. Swedish folder or file names).
    """
    path = Path(image_path)
    if not path.is_file():
        raise FileNotFoundError(f"Image not found: {image_path}")

    data = np.frombuffer(path.read_bytes(), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise FileNotFoundError(
            f"Could not decode image (corrupt or unsupported format?): {image_path}"
        )
    return image


def build_binary_mask(config: ProcessingConfig) -> np.ndarray:
    gray = load_image_grayscale(config.image_path)
    blurred = cv2.GaussianBlur(gray, (config.blur_kernel, config.blur_kernel), 0)

    threshold_flag = cv2.THRESH_BINARY_INV
    if config.invert:
        threshold_flag = cv2.THRESH_BINARY

    _, binary = cv2.threshold(blurred, config.threshold, 255, threshold_flag)
    return binary


def show_preview(gray: np.ndarray, binary: np.ndarray, planned: np.ndarray | None = None) -> None:
    cv2.imshow("AutoPaint - Original Grayscale", gray)
    cv2.imshow("AutoPaint - Binary Mask", binary)
    if planned is not None:
        cv2.imshow("AutoPaint - Planned Strokes", planned)
    cv2.waitKey(0)
    cv2.destroyAllWindows()

