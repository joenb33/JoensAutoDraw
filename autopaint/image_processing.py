from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from autopaint.config import ProcessingConfig

RASTER_SUFFIXES = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".bmp",
        ".webp",
        ".tif",
        ".tiff",
        ".gif",
        ".ico",
        ".avif",
    }
)
LOSSY_SUFFIXES = frozenset({".jpg", ".jpeg", ".webp", ".avif"})
VECTOR_SUFFIXES = frozenset({".svg", ".gcode", ".nc", ".tap"})
RASTER_FILE_GLOB = "*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff *.gif *.ico *.avif"


@dataclass(frozen=True)
class PreparedRaster:
    gray: np.ndarray
    alpha: np.ndarray | None
    warnings: tuple[str, ...]


def is_raster_path(path: Path | str) -> bool:
    suffix = Path(path).suffix.lower()
    if suffix in VECTOR_SUFFIXES:
        return False
    if suffix in RASTER_SUFFIXES:
        return True
    return suffix != ""


def _collect_import_warnings(path: Path) -> list[str]:
    warnings: list[str] = []
    suffix = path.suffix.lower()
    if suffix in LOSSY_SUFFIXES:
        warnings.append(
            "Lossy image format (JPEG/WEBP/AVIF). PNG with solid edges usually traces cleaner."
        )
    return warnings


def _alpha_has_transparency(alpha: np.ndarray) -> bool:
    return bool(np.any(alpha < 250))


def _pil_to_arrays(image: Image.Image) -> tuple[np.ndarray, np.ndarray | None]:
    rgba = image.convert("RGBA")
    arr = np.array(rgba)
    rgb = arr[:, :, :3]
    alpha = arr[:, :, 3]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    if _alpha_has_transparency(alpha):
        return gray, alpha
    return gray, None


def prepare_raster_image(image_path: Path, *, auto_exif_rotate: bool = True) -> PreparedRaster:
    """Decode raster input with Pillow (EXIF-aware) into grayscale + optional alpha."""
    path = Path(image_path)
    if not path.is_file():
        raise FileNotFoundError(f"Image not found: {image_path}")

    warnings = _collect_import_warnings(path)
    try:
        with Image.open(path) as image:
            if auto_exif_rotate:
                image = ImageOps.exif_transpose(image)
            gray, alpha = _pil_to_arrays(image)
    except OSError as exc:
        raise FileNotFoundError(
            f"Could not decode image (corrupt or unsupported format?): {image_path}"
        ) from exc

    if alpha is not None:
        warnings.append("Transparent alpha detected; enable 'Use alpha as mask' for logo-style art.")

    return PreparedRaster(gray=gray, alpha=alpha, warnings=tuple(dict.fromkeys(warnings)))


def enhance_gray(gray: np.ndarray, config: ProcessingConfig) -> np.ndarray:
    clip_limit = float(config.clahe_clip_limit)
    if clip_limit <= 0:
        return gray
    clahe = cv2.createCLAHE(clipLimit=max(0.1, clip_limit), tileGridSize=(8, 8))
    return clahe.apply(gray)


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


def _blur_kernel_size(config: ProcessingConfig) -> int:
    blur_kernel = max(1, int(config.blur_kernel))
    if blur_kernel % 2 == 0:
        blur_kernel += 1
    return blur_kernel


def build_binary_mask_from_gray(gray: np.ndarray, config: ProcessingConfig) -> np.ndarray:
    gray = enhance_gray(gray, config)
    blur_kernel = _blur_kernel_size(config)
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


def build_binary_mask_from_prepared(
    prepared: PreparedRaster, config: ProcessingConfig
) -> np.ndarray:
    use_alpha = (
        config.use_alpha_mask
        and prepared.alpha is not None
        and _alpha_has_transparency(prepared.alpha)
    )
    if use_alpha:
        alpha = prepared.alpha.copy()
        blur_kernel = _blur_kernel_size(config)
        if blur_kernel > 1:
            alpha = cv2.GaussianBlur(alpha, (blur_kernel, blur_kernel), 0)
        if config.invert:
            alpha = cv2.bitwise_not(alpha)
        _, binary = cv2.threshold(alpha, 127, 255, cv2.THRESH_BINARY)
        return apply_morphology(
            binary,
            close_kernel=config.morph_close_kernel,
            open_kernel=config.morph_open_kernel,
        )
    return build_binary_mask_from_gray(prepared.gray, config)


def build_binary_mask(config: ProcessingConfig) -> np.ndarray:
    prepared = prepare_raster_image(
        config.image_path,
        auto_exif_rotate=config.auto_exif_rotate,
    )
    return build_binary_mask_from_prepared(prepared, config)


def load_image_grayscale(image_path: Path, *, auto_exif_rotate: bool = True) -> np.ndarray:
    """Load grayscale image with Unicode-safe Pillow decoding."""
    return prepare_raster_image(image_path, auto_exif_rotate=auto_exif_rotate).gray


def show_preview(gray: np.ndarray, binary: np.ndarray, planned: np.ndarray | None = None) -> None:
    cv2.imshow("AutoPaint - Original Grayscale", gray)
    cv2.imshow("AutoPaint - Binary Mask", binary)
    if planned is not None:
        cv2.imshow("AutoPaint - Planned Strokes", planned)
    cv2.waitKey(0)
    cv2.destroyAllWindows()
