from __future__ import annotations

import logging
import time
from dataclasses import replace

from app.api.v1.common import now_ms_text
from app.core.config import get_settings
from app.schemas.aggregate import (
    AggregateDetectResponse,
    AggregateEffectiveParams,
    AggregateErrorPart,
    AggregateOcclusionPart,
    AggregateQualityAbnormalPart,
    AggregateModule,
    AggregateScreenPart,
    AggregateTiltPart,
    VALID_MODULES,
)
from app.schemas.quality_abnormal import QualityAbnormalItem
from app.schemas.screen import ScreenBox
from app.schemas.tilt import TiltResultData
from app.services.image_preprocess import PreparedImage, prepare_image
from app.services.occlusion_detector import detect_occlusion_from_array
from app.services.quality_abnormal_detector import (
    QualityAbnormalResultItem,
    detect_quality_abnormal_from_array,
)
from app.services.screen_detector import ScreenBoxResult, detect_screen_from_arrays as detect_screen_from_array
from app.services.tilt_detector import detect_image_tilt_from_array


logger = logging.getLogger(__name__)


def _resolve_modules(include: list[str] | None) -> list[AggregateModule]:
    settings = get_settings()
    configured = list(settings.aggregate_detection.default_modules if include is None else include)
    result: list[AggregateModule] = []
    for module in configured:
        if module not in VALID_MODULES:
            raise ValueError(f"Invalid aggregate module: {module}")
        if module not in result:
            result.append(module)  # type: ignore[arg-type]
    return result


def _to_quality_item(item: QualityAbnormalResultItem) -> QualityAbnormalItem:
    return QualityAbnormalItem(type=item.type, score=item.score, message=item.message)


def _to_screen_box(item: ScreenBoxResult) -> ScreenBox:
    return ScreenBox(label=item.label, confidence=item.confidence, box=item.box)


def _error_part(start: float, exc: Exception) -> AggregateErrorPart:
    return AggregateErrorPart(
        code=500,
        msg=f"Detection failed: {exc}",
        cost_ms=round((time.time() - start) * 1000, 2),
    )


def _prepare_shared_image(image_base64: str) -> PreparedImage:
    settings = get_settings()
    aggregate = settings.aggregate_detection
    max_side = max(
        settings.quality_abnormal_detection.analyze_max_side,
        settings.occlusion_detection.analyze_max_side,
    )
    return prepare_image(
        image_base64,
        settings.runtime.max_image_bytes,
        max_side,
        settings.quality_abnormal_detection.overlay_top_ratio,
        settings.quality_abnormal_detection.overlay_bottom_ratio,
    )


def _run_tilt(image: PreparedImage, threshold: float) -> AggregateTiltPart:
    settings = get_settings()
    start = time.time()
    detection = replace(settings.detection, tilt_threshold=threshold)
    result = detect_image_tilt_from_array(image.bgr, detection)
    cost_ms = round((time.time() - start) * 1000, 2)
    return AggregateTiltPart(
        code=200,
        msg=result.message,
        cost_ms=cost_ms,
        result=TiltResultData(
            is_tilted=result.is_tilted,
            angle=round(result.angle, 2),
            cost_ms=cost_ms,
        ),
    )


def _run_screen(
    image: PreparedImage,
    conf: float,
    iou: float,
    device: str,
) -> AggregateScreenPart:
    start = time.time()
    items, _, _ = detect_screen_from_array([image.bgr], conf=conf, iou=iou, device=device)
    item = items[0] if items else None
    cost_ms = round((time.time() - start) * 1000, 2)
    primary = item.primary if item else None
    detections = item.detections if item else []
    return AggregateScreenPart(
        code=200,
        msg="检测完成" if primary else "检测完成，未识别到有效屏幕类型",
        cost_ms=cost_ms,
        primary=_to_screen_box(primary) if primary else None,
        detections=[_to_screen_box(det) for det in detections],
    )


def _run_quality_abnormal(image: PreparedImage) -> AggregateQualityAbnormalPart:
    settings = get_settings()
    start = time.time()
    result = detect_quality_abnormal_from_array(
        image,
        settings.quality_abnormal_detection,
    )
    cost_ms = round((time.time() - start) * 1000, 2)
    return AggregateQualityAbnormalPart(
        code=200,
        msg="检测完成",
        cost_ms=cost_ms,
        is_abnormal=result.is_abnormal,
        abnormal_types=result.abnormal_types,
        results=[_to_quality_item(item) for item in result.results],
        message=result.message,
    )


def _run_occlusion(
    image: PreparedImage,
    threshold: float,
    area_ratio: float,
    device: str,
) -> AggregateOcclusionPart:
    settings = get_settings()
    start = time.time()
    result = detect_occlusion_from_array(
        image.bgr,
        settings.occlusion_detection,
        threshold,
        area_ratio,
        device=device,
    )
    cost_ms = round((time.time() - start) * 1000, 2)
    return AggregateOcclusionPart(
        code=200,
        msg="检测完成",
        cost_ms=cost_ms,
        is_occluded=result.is_occluded,
        occlusion_area_ratio=result.occlusion_area_ratio,
        score=result.score,
        threshold=result.threshold,
        area_ratio=result.area_ratio,
        message=result.message,
    )


def _build_problem_types(
    response: AggregateDetectResponse,
) -> list[AggregateModule]:
    problems: list[AggregateModule] = []
    if (
        response.tilt
        and response.tilt.code == 200
        and isinstance(response.tilt, AggregateTiltPart)
        and response.tilt.result
        and response.tilt.result.is_tilted
    ):
        problems.append("tilt")

    if (
        response.screen
        and response.screen.code == 200
        and isinstance(response.screen, AggregateScreenPart)
        and (response.screen.primary is None or response.screen.primary.label in {0, 1, 2})
    ):
        problems.append("screen")

    if (
        response.quality_abnormal
        and response.quality_abnormal.code == 200
        and isinstance(response.quality_abnormal, AggregateQualityAbnormalPart)
        and response.quality_abnormal.is_abnormal
    ):
        problems.append("quality_abnormal")

    if (
        response.occlusion
        and response.occlusion.code == 200
        and isinstance(response.occlusion, AggregateOcclusionPart)
        and response.occlusion.is_occluded
    ):
        problems.append("occlusion")
    return problems


def run_detect_all(
    image_base64: str,
    start_time_text: str,
    tilt_threshold: float | None = None,
    screen_conf: float | None = None,
    screen_iou: float | None = None,
    occlusion_threshold: float | None = None,
    occlusion_area_ratio: float | None = None,
    include: list[str] | None = None,
) -> AggregateDetectResponse:
    settings = get_settings()
    aggregate = settings.aggregate_detection
    modules = _resolve_modules(include)
    device = settings.yolo.device
    threshold = aggregate.tilt_threshold if tilt_threshold is None else float(tilt_threshold)
    conf = aggregate.screen_conf if screen_conf is None else float(screen_conf)
    iou = aggregate.screen_iou if screen_iou is None else float(screen_iou)
    occlusion_conf = (
        aggregate.occlusion_threshold if occlusion_threshold is None else float(occlusion_threshold)
    )
    occlusion_area = (
        aggregate.occlusion_area_ratio
        if occlusion_area_ratio is None
        else float(occlusion_area_ratio)
    )

    start = time.time()
    image = _prepare_shared_image(image_base64)
    failed_modules: list[AggregateModule] = []
    tilt_part = None
    screen_part = None
    quality_part = None
    occlusion_part = None

    if "tilt" in modules:
        module_start = time.time()
        try:
            tilt_part = _run_tilt(image, threshold)
        except Exception as exc:
            logger.exception("aggregate tilt failed")
            failed_modules.append("tilt")
            tilt_part = _error_part(module_start, exc)

    if "screen" in modules:
        module_start = time.time()
        try:
            screen_part = _run_screen(image, conf, iou, device)
        except Exception as exc:
            logger.exception("aggregate screen failed")
            failed_modules.append("screen")
            screen_part = _error_part(module_start, exc)

    if "quality_abnormal" in modules:
        module_start = time.time()
        try:
            quality_part = _run_quality_abnormal(image)
        except Exception as exc:
            logger.exception("aggregate quality abnormal failed")
            failed_modules.append("quality_abnormal")
            quality_part = _error_part(module_start, exc)

    if "occlusion" in modules:
        module_start = time.time()
        try:
            occlusion_part = _run_occlusion(image, occlusion_conf, occlusion_area, device)
        except Exception as exc:
            logger.exception("aggregate occlusion failed")
            failed_modules.append("occlusion")
            occlusion_part = _error_part(module_start, exc)

    response = AggregateDetectResponse(
        code=200,
        msg="检测完成" if not failed_modules else "检测完成，部分模块失败",
        start_time=start_time_text,
        end_time=now_ms_text(),
        cost_ms=round((time.time() - start) * 1000, 2),
        executed_modules=modules,
        failed_modules=failed_modules,
        effective_params=AggregateEffectiveParams(
            tilt_threshold=threshold,
            screen_conf=conf,
            screen_iou=iou,
            occlusion_threshold=occlusion_conf,
            occlusion_area_ratio=occlusion_area,
            include=modules,
            device=device,
        ),
        problem_types=[],
        tilt=tilt_part,
        screen=screen_part,
        quality_abnormal=quality_part,
        occlusion=occlusion_part,
    )
    response.problem_types = _build_problem_types(response)
    return response
