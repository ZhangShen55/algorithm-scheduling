from __future__ import annotations

import logging
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from app.core.config import get_settings


BEIJING_TZ = timezone(timedelta(hours=8))


def _now_ms_text() -> str:
    return str(int(datetime.now(BEIJING_TZ).timestamp() * 1000))
from app.schemas.inspect import InspectResponse, InspectScreenPart, InspectTiltPart
from app.schemas.screen import ScreenBox
from app.schemas.tilt import TiltResultData
from app.services.screen_detector import (
    ScreenBoxResult,
    _holder,
    _parse_boxes,
    decode_base64_image,
)
from app.services.tilt_detector import detect_image_tilt_from_array


logger = logging.getLogger(__name__)


def _to_screen_box(item: ScreenBoxResult) -> ScreenBox:
    return ScreenBox(label=item.label, confidence=item.confidence, box=item.box)


def _run_tilt(image, threshold: float) -> InspectTiltPart:
    settings = get_settings()
    detection = replace(settings.detection, tilt_threshold=threshold)
    start = time.time()
    try:
        result = detect_image_tilt_from_array(image, detection)
        cost_ms = round((time.time() - start) * 1000, 2)
        return InspectTiltPart(
            code=200,
            msg=result.message,
            cost_ms=cost_ms,
            result=TiltResultData(
                is_tilted=result.is_tilted,
                angle=round(result.angle, 2),
                cost_ms=cost_ms,
            ),
        )
    except Exception as exc:
        cost_ms = round((time.time() - start) * 1000, 2)
        logger.exception("inspect tilt failed")
        return InspectTiltPart(
            code=500,
            msg=f"Detection failed: {exc}",
            cost_ms=cost_ms,
            result=None,
        )


def _run_screen(image, conf: float | None, iou: float | None) -> InspectScreenPart:
    settings = get_settings()
    screen_cfg = settings.screen_detection
    conf_used = screen_cfg.conf if conf is None else conf
    iou_used = screen_cfg.iou if iou is None else iou
    allowed = frozenset(screen_cfg.allowed_class_ids)

    start = time.time()
    try:
        _holder.load()
        yolo_results = _holder.model.predict(
            image,
            conf=conf_used,
            iou=iou_used,
            device=_holder.device,
            verbose=False,
        )
        cost_ms = round((time.time() - start) * 1000, 2)
        if isinstance(yolo_results, list):
            yolo_result = yolo_results[0] if yolo_results else None
        else:
            yolo_result = yolo_results
        if yolo_result is None:
            detections = []
        else:
            detections = _parse_boxes(yolo_result, allowed)
        primary = detections[0] if detections else None
        msg = "检测完成"
        if not primary:
            msg = "检测完成，未识别到有效屏幕类型"
            logger.info("inspect_screen no allowed detections")
        else:
            logger.info(
                "inspect_screen label=%s conf=%.3f boxes=%s",
                primary.label,
                primary.confidence,
                len(detections),
            )
        return InspectScreenPart(
            code=200,
            msg=msg,
            cost_ms=cost_ms,
            primary=_to_screen_box(primary) if primary else None,
            detections=[_to_screen_box(item) for item in detections],
        )
    except Exception as exc:
        cost_ms = round((time.time() - start) * 1000, 2)
        logger.exception("inspect screen failed")
        return InspectScreenPart(
            code=500,
            msg=f"Detection failed: {exc}",
            cost_ms=cost_ms,
            primary=None,
            detections=[],
        )


def _summary_msg(tilt: InspectTiltPart, screen: InspectScreenPart) -> str:
    if tilt.code != 200 and screen.code != 200:
        return "倾斜检测与屏幕检测均失败"
    if tilt.code != 200:
        return "倾斜检测失败，屏幕检测完成" if screen.code == 200 else "倾斜检测失败，屏幕检测失败"
    if screen.code != 200:
        return "倾斜检测完成，屏幕检测失败"
    if screen.primary is None:
        return screen.msg
    return "检测完成"


def run_inspect(
    image_base64: str,
    start_time_text: str,
    tilt_threshold: float | None = None,
    conf: float | None = None,
    iou: float | None = None,
) -> InspectResponse:
    settings = get_settings()
    threshold = (
        settings.detection.tilt_threshold
        if tilt_threshold is None
        else float(tilt_threshold)
    )
    screen_cfg = settings.screen_detection
    conf_used = screen_cfg.conf if conf is None else float(conf)
    iou_used = screen_cfg.iou if iou is None else float(iou)

    image = decode_base64_image(image_base64, settings.runtime.max_image_bytes)
    tilt_part = _run_tilt(image, threshold)
    screen_part = _run_screen(image, conf, iou)

    return InspectResponse(
        code=200,
        start_time=start_time_text,
        end_time=_now_ms_text(),
        msg=_summary_msg(tilt_part, screen_part),
        tilt_threshold=threshold,
        conf=conf_used,
        iou=iou_used,
        tilt=tilt_part,
        screen=screen_part,
    )
