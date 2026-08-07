from __future__ import annotations

import base64
import binascii
import io
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, UnidentifiedImageError

from app.core.config import DetectionConfig


@dataclass(frozen=True)
class TiltResult:
    is_tilted: bool
    angle: float
    message: str
    image: np.ndarray


def decode_base64_image(image_base64: str, max_image_bytes: int) -> np.ndarray:
    if "," in image_base64 and image_base64.lstrip().startswith("data:"):
        image_base64 = image_base64.split(",", 1)[1]
    image_base64 = "".join(image_base64.split())

    try:
        image_data = base64.b64decode(image_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Invalid base64 image data") from exc

    if len(image_data) > max_image_bytes:
        raise ValueError(f"Image is larger than {max_image_bytes} bytes")

    try:
        pil_image = Image.open(io.BytesIO(image_data)).convert("RGB")
    except UnidentifiedImageError as exc:
        raise ValueError("Unsupported or corrupted image") from exc

    return cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)


def detect_image_tilt_from_array(
    image: np.ndarray, config: DetectionConfig
) -> TiltResult:
    image_display = image.copy()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    kernel_size = (config.gaussian_kernel_size, config.gaussian_kernel_size)
    blur = cv2.GaussianBlur(gray, kernel_size, 0)
    edges = cv2.Canny(blur, config.canny_threshold1, config.canny_threshold2)

    line_detector = cv2.createLineSegmentDetector(0)
    lines, _, _, _ = line_detector.detect(edges)
    if lines is None:
        return TiltResult(False, 0.0, "未检测到有效直线", image_display)

    valid_data = []
    _, image_width = image.shape[:2]
    min_line_length = image_width * config.min_line_length_ratio

    for line in lines:
        x1, y1, x2, y2 = line[0]
        line_length = float(np.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2))
        if line_length < min_line_length:
            continue

        angle = float(np.arctan2(y2 - y1, x2 - x1) * 180 / np.pi)
        is_horizontal = config.horizontal_angle_min <= angle <= config.horizontal_angle_max
        is_vertical = config.vertical_angle_min <= angle <= config.vertical_angle_max
        if not is_horizontal and not is_vertical:
            continue

        tilt_angle = angle if is_horizontal else angle - 90
        valid_data.append((tilt_angle, line_length, x1, y1, x2, y2))

    if len(valid_data) < config.min_valid_lines:
        return TiltResult(False, 0.0, "有效参考线段不足", image_display)

    valid_data_sorted = sorted(valid_data, key=lambda item: item[0])
    count = len(valid_data_sorted)
    start = int(count * config.trim_start_ratio)
    end = int(count * config.trim_end_ratio)
    trimmed_data = valid_data_sorted[start:end]
    if len(trimmed_data) < 3:
        trimmed_data = valid_data_sorted

    for _, _, x1, y1, x2, y2 in trimmed_data:
        cv2.line(
            image_display,
            (int(x1), int(y1)),
            (int(x2), int(y2)),
            (0, 255, 0),
            2,
        )

    angles = [item[0] for item in trimmed_data]
    lengths = [item[1] for item in trimmed_data]
    overall_tilt = abs(float(np.average(angles, weights=lengths)))
    is_tilted = overall_tilt > config.tilt_threshold

    text = f"Tilt: {overall_tilt:.2f} deg | {'TILTED' if is_tilted else 'NORMAL'}"
    cv2.putText(
        image_display,
        text,
        (30, 50),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.2,
        (0, 0, 255),
        3,
    )

    return TiltResult(is_tilted, overall_tilt, "检测完成", image_display)


def detect_from_base64(
    image_base64: str, config: DetectionConfig, max_image_bytes: int
) -> TiltResult:
    image = decode_base64_image(image_base64, max_image_bytes)
    return detect_image_tilt_from_array(image, config)
