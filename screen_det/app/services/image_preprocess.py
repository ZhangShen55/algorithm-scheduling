from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from app.services.tilt_detector import decode_base64_image


@dataclass(frozen=True)
class PreparedImage:
    bgr: np.ndarray
    gray: np.ndarray
    hsv: np.ndarray
    lab: np.ndarray
    roi_y0: int
    roi_y1: int

    @property
    def roi_bgr(self) -> np.ndarray:
        return self.bgr[self.roi_y0 : self.roi_y1]

    @property
    def roi_gray(self) -> np.ndarray:
        return self.gray[self.roi_y0 : self.roi_y1]

    @property
    def roi_hsv(self) -> np.ndarray:
        return self.hsv[self.roi_y0 : self.roi_y1]

    @property
    def roi_lab(self) -> np.ndarray:
        return self.lab[self.roi_y0 : self.roi_y1]


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def resize_max_side(image: np.ndarray, max_side: int) -> np.ndarray:
    if max_side <= 0:
        return image
    height, width = image.shape[:2]
    longest = max(height, width)
    if longest <= max_side:
        return image
    scale = max_side / longest
    new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    return cv2.resize(image, new_size, interpolation=cv2.INTER_AREA)


def prepare_image(
    image_base64: str,
    max_image_bytes: int,
    analyze_max_side: int,
    overlay_top_ratio: float = 0.0,
    overlay_bottom_ratio: float = 0.0,
) -> PreparedImage:
    bgr = decode_base64_image(image_base64, max_image_bytes)
    bgr = resize_max_side(bgr, analyze_max_side)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)

    height = bgr.shape[0]
    roi_y0 = int(height * clamp01(overlay_top_ratio))
    roi_y1 = int(height * (1.0 - clamp01(overlay_bottom_ratio)))
    if roi_y1 <= roi_y0:
        roi_y0 = 0
        roi_y1 = height

    return PreparedImage(
        bgr=bgr,
        gray=gray,
        hsv=hsv,
        lab=lab,
        roi_y0=roi_y0,
        roi_y1=roi_y1,
    )


def highpass_noise_proxy(gray: np.ndarray) -> float:
    kernel = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], dtype=np.float32)
    highpass = cv2.filter2D(gray.astype(np.float32), -1, kernel)
    return float(np.median(np.abs(highpass - np.median(highpass))))


def edge_density(gray: np.ndarray, threshold1: int = 50, threshold2: int = 150) -> float:
    edges = cv2.Canny(gray, threshold1, threshold2)
    return float((edges > 0).mean())


def normalized_area(mask: np.ndarray) -> float:
    if mask.size == 0:
        return 0.0
    return clamp01(float((mask > 0).sum()) / float(mask.size))


def iter_grid_tiles(
    height: int,
    width: int,
    rows: int,
    cols: int,
):
    rows = max(1, int(rows))
    cols = max(1, int(cols))
    for row in range(rows):
        y0 = row * height // rows
        y1 = (row + 1) * height // rows
        for col in range(cols):
            x0 = col * width // cols
            x1 = (col + 1) * width // cols
            if y1 > y0 and x1 > x0:
                yield row, col, y0, y1, x0, x1


def mask_from_grid_tiles(
    height: int,
    width: int,
    rows: int,
    cols: int,
    selected_tiles: set[tuple[int, int]],
) -> np.ndarray:
    mask = np.zeros((height, width), dtype=np.uint8)
    for row, col, y0, y1, x0, x1 in iter_grid_tiles(height, width, rows, cols):
        if (row, col) in selected_tiles:
            mask[y0:y1, x0:x1] = 255
    return mask


def morphology_clean(mask: np.ndarray, close_size: int = 9, open_size: int = 3) -> np.ndarray:
    result = mask.astype(np.uint8)
    if close_size > 1:
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (close_size, close_size))
        result = cv2.morphologyEx(result, cv2.MORPH_CLOSE, kernel)
    if open_size > 1:
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (open_size, open_size))
        result = cv2.morphologyEx(result, cv2.MORPH_OPEN, kernel)
    return result
