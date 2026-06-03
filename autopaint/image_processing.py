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


def _effective_morph_kernel(value: int) -> int:
    if value <= 0:
        return 0
    kernel = max(3, int(value))
    if kernel % 2 == 0:
        kernel += 1
    return kernel


def apply_morphology(mask: np.ndarray, close_kernel: int, open_kernel: int) -> np.ndarray:
    result = mask
    close_k = _effective_morph_kernel(close_kernel)
    open_k = _effective_morph_kernel(open_kernel)
    if close_k > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_k, close_k))
        result = cv2.morphologyEx(result, cv2.MORPH_CLOSE, kernel)
    if open_k > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (open_k, open_k))
        result = cv2.morphologyEx(result, cv2.MORPH_OPEN, kernel)
    return result


def build_binary_mask_from_gray(gray: np.ndarray, config: ProcessingConfig) -> np.ndarray:
    blur_kernel = max(1, int(config.blur_kernel))
    if blur_kernel % 2 == 0:
        blur_kernel += 1
    blurred = cv2.GaussianBlur(gray, (blur_kernel, blur_kernel), 0)

    if config.use_otsu:
        threshold_flag = cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU
        if config.invert:
            threshold_flag = cv2.THRESH_BINARY | cv2.THRESH_OTSU
        _, binary = cv2.threshold(blurred, 0, 255, threshold_flag)
    else:
        threshold_flag = cv2.THRESH_BINARY_INV
        if config.invert:
            threshold_flag = cv2.THRESH_BINARY
        _, binary = cv2.threshold(blurred, config.threshold, 255, threshold_flag)

    return apply_morphology(
        binary,
        close_kernel=config.morph_close_kernel,
        open_kernel=config.morph_open_kernel,
    )


def build_binary_mask(config: ProcessingConfig) -> np.ndarray:
    gray = load_image_grayscale(config.image_path)
    return build_binary_mask_from_gray(gray, config)


def show_preview(gray: np.ndarray, binary: np.ndarray, planned: np.ndarray | None = None) -> None:
    cv2.imshow("AutoPaint - Original Grayscale", gray)
    cv2.imshow("AutoPaint - Binary Mask", binary)
    if planned is not None:
        cv2.imshow("AutoPaint - Planned Strokes", planned)
    cv2.waitKey(0)
    cv2.destroyAllWindows()
