from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from app.core.config import QualityAbnormalDetectionConfig, get_settings
from app.services.image_preprocess import (
    PreparedImage,
    clamp01,
    edge_density,
    highpass_noise_proxy,
    iter_grid_tiles,
    mask_from_grid_tiles,
    morphology_clean,
    normalized_area,
    prepare_image,
)


ABNORMAL_BLUR = 1
ABNORMAL_COLOR_CAST = 2
ABNORMAL_SNOW_NOISE = 3
ABNORMAL_GLITCH = 4


@dataclass(frozen=True)
class QualityAbnormalResultItem:
    type: int
    score: float
    message: str


@dataclass(frozen=True)
class QualityAbnormalDetectResult:
    is_abnormal: bool
    abnormal_types: list[int]
    results: list[QualityAbnormalResultItem]
    message: str


def _score_over(value: float, threshold: float, width: float | None = None) -> float:
    width = threshold if width is None else width
    if width <= 0:
        return 1.0 if value >= threshold else 0.0
    return clamp01((value - threshold) / width)


def _score_under(value: float, threshold: float) -> float:
    if threshold <= 0:
        return 1.0 if value <= threshold else 0.0
    return clamp01((threshold - value) / threshold)


def _detect_color_cast(
    image: PreparedImage, config: QualityAbnormalDetectionConfig
) -> QualityAbnormalResultItem | None:
    lab = image.roi_lab
    hsv = image.roi_hsv
    bgr = image.roi_bgr

    a_offset = float(lab[:, :, 1].mean() - 128)
    b_offset = float(lab[:, :, 2].mean() - 128)
    lab_cast = float((a_offset * a_offset + b_offset * b_offset) ** 0.5)
    mean_channels = bgr.reshape(-1, 3).mean(axis=0)
    imbalance = float(
        (mean_channels.max() - mean_channels.min()) / max(mean_channels.mean(), 1.0)
    )
    saturation = float(hsv[:, :, 1].mean())

    lab_score = _score_over(lab_cast, config.color_cast_lab_threshold)
    imbalance_score = _score_over(
        imbalance, config.color_cast_imbalance_threshold, width=0.35
    )
    score = clamp01(max(lab_score, imbalance_score))
    if score <= 0 or (saturation < 35 and lab_cast < config.color_cast_lab_threshold * 1.5):
        return None
    return QualityAbnormalResultItem(
        type=ABNORMAL_COLOR_CAST,
        score=round(max(score, 0.5), 4),
        message="疑似偏色",
    )


def _detect_snow_noise(
    image: PreparedImage, config: QualityAbnormalDetectionConfig
) -> tuple[QualityAbnormalResultItem | None, float, float]:
    gray = image.roi_gray
    noise = highpass_noise_proxy(gray)
    density = edge_density(gray)
    noise_score = _score_over(noise, config.snow_noise_threshold)
    edge_score = _score_over(density, config.snow_edge_density_threshold, width=0.15)
    score = clamp01(max(noise_score, edge_score))
    if noise >= config.snow_noise_threshold and density >= 0.12:
        return (
            QualityAbnormalResultItem(
                type=ABNORMAL_SNOW_NOISE,
                score=round(max(score, 0.55), 4),
                message="疑似雪花噪点",
            ),
            noise,
            density,
        )
    return None, noise, density


def _detect_blur(
    image: PreparedImage,
    config: QualityAbnormalDetectionConfig,
    snow_noise_score: float,
) -> QualityAbnormalResultItem | None:
    gray = image.roi_gray
    laplacian_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    density = edge_density(gray)
    lap_score = _score_under(laplacian_var, config.blur_laplacian_threshold)
    edge_score = _score_under(density, config.blur_edge_density_threshold)
    score = clamp01((lap_score * 0.7 + edge_score * 0.3) * (1.0 - snow_noise_score * 0.35))

    if (
        laplacian_var < config.blur_laplacian_threshold
        and density < config.blur_edge_density_threshold
        and score >= 0.25
    ):
        return QualityAbnormalResultItem(
            type=ABNORMAL_BLUR,
            score=round(max(score, 0.5), 4),
            message="疑似虚焦",
        )
    return None


def _detect_glitch(
    image: PreparedImage, config: QualityAbnormalDetectionConfig
) -> QualityAbnormalResultItem | None:
    bgr = image.bgr
    gray = image.gray
    hsv = image.hsv
    height, width = bgr.shape[:2]
    rows = max(1, config.glitch_grid_rows)
    cols = max(1, config.glitch_grid_cols)
    candidate_tiles: set[tuple[int, int]] = set()
    row_counts: dict[int, int] = {}
    row_saturation: dict[int, float] = {}

    for row in range(rows):
        y0 = row * height // rows
        y1 = (row + 1) * height // rows
        row_saturation[row] = float(hsv[y0:y1, :, 1].mean())

    for row, col, y0, y1, x0, x1 in iter_grid_tiles(height, width, rows, cols):
        tile_gray = gray[y0:y1, x0:x1]
        tile_hsv = hsv[y0:y1, x0:x1]
        if tile_gray.size == 0:
            continue
        laplacian_var = float(cv2.Laplacian(tile_gray, cv2.CV_64F).var())
        saturation = float(tile_hsv[:, :, 1].mean())
        if (
            laplacian_var < config.glitch_band_laplacian_threshold
            and saturation > config.glitch_band_saturation_threshold
            and row >= rows // 2
            and row_saturation[row] <= config.glitch_band_saturation_threshold * 2.4
        ):
            candidate_tiles.add((row, col))
            row_counts[row] = row_counts.get(row, 0) + 1

    min_columns_per_row = max(3, int(cols * 0.15))
    abnormal_rows = {row for row, count in row_counts.items() if count >= min_columns_per_row}
    if len(abnormal_rows) < 3:
        return None
    abnormal_tiles = {tile for tile in candidate_tiles if tile[0] in abnormal_rows}

    tile_mask = mask_from_grid_tiles(height, width, rows, cols, abnormal_tiles)
    tile_mask = morphology_clean(tile_mask, close_size=31, open_size=5)
    area_ratio = normalized_area(tile_mask)
    if area_ratio < config.glitch_min_area_ratio:
        return None

    score = clamp01(area_ratio / max(config.glitch_min_area_ratio * 2.0, 0.01))
    return QualityAbnormalResultItem(
        type=ABNORMAL_GLITCH,
        score=round(max(score, 0.55), 4),
        message="疑似花屏",
    )


def detect_quality_abnormal_from_base64(
    image_base64: str,
) -> QualityAbnormalDetectResult:
    settings = get_settings()
    config = settings.quality_abnormal_detection
    if not config.enabled:
        return QualityAbnormalDetectResult(False, [], [], "画面异常检测未启用")

    image = prepare_image(
        image_base64,
        settings.runtime.max_image_bytes,
        config.analyze_max_side,
        config.overlay_top_ratio,
        config.overlay_bottom_ratio,
    )
    return detect_quality_abnormal_from_array(image, config)


def detect_quality_abnormal_from_array(
    image: PreparedImage,
    config: QualityAbnormalDetectionConfig,
) -> QualityAbnormalDetectResult:
    if not config.enabled:
        return QualityAbnormalDetectResult(False, [], [], "画面异常检测未启用")

    results: list[QualityAbnormalResultItem] = []

    color_cast = _detect_color_cast(image, config)
    if color_cast:
        results.append(color_cast)

    snow_noise, _, _ = _detect_snow_noise(image, config)
    snow_score = snow_noise.score if snow_noise else 0.0
    if snow_noise:
        results.append(snow_noise)

    blur = _detect_blur(image, config, snow_score)
    if blur:
        results.append(blur)

    glitch = _detect_glitch(image, config)
    if glitch:
        results.append(glitch)

    abnormal_types = [item.type for item in results]
    if not results:
        return QualityAbnormalDetectResult(False, [], [], "未检测到画面异常")

    messages = "、".join(item.message.replace("疑似", "") for item in results)
    return QualityAbnormalDetectResult(
        is_abnormal=True,
        abnormal_types=abnormal_types,
        results=results,
        message=f"检测到画面异常：{messages}",
    )
