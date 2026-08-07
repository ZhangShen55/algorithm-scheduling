from __future__ import annotations

import logging
from json import JSONDecodeError

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError

from app.api.v1.common import now_ms_text
from app.core.state import app_state
from app.schemas.screen import ScreenBox, ScreenDetectResponse, ScreenImageResult
from app.schemas.tilt import ErrorResponse
from app.services.screen_detector import (
    ScreenBoxResult,
    ScreenImageDetectResult,
    detect_screen_from_base64_list,
)


router = APIRouter(tags=["screen"])
logger = logging.getLogger(__name__)


def _screen_request_from_payload(payload: object) -> tuple[list[str], float | None, float | None]:
    if not isinstance(payload, dict):
        raise ValueError('Request body must be JSON: {"images": "<base64>" or ["..."]}')
    if "images" not in payload:
        raise ValueError('Missing field "images"')

    images = payload["images"]
    conf = payload.get("conf")
    iou = payload.get("iou")
    if conf is not None:
        conf = float(conf)
    if iou is not None:
        iou = float(iou)

    if isinstance(images, str):
        if not images.strip():
            raise ValueError('Field "images" must be a non-empty base64 string')
        return [images.strip()], conf, iou

    if isinstance(images, list):
        if not images:
            raise ValueError('Field "images" must be a non-empty array')
        parsed: list[str] = []
        for index, item in enumerate(images):
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"images[{index}] must be a non-empty base64 string")
            parsed.append(item.strip())
        return parsed, conf, iou

    raise ValueError('Field "images" must be a base64 string or string array')


async def _parse_screen_request(request: Request) -> tuple[list[str], float | None, float | None]:
    content_type = request.headers.get("content-type", "").lower()
    if "application/json" not in content_type:
        raise ValueError("Content-Type must be application/json")
    payload = await request.json()
    return _screen_request_from_payload(payload)


def _to_screen_box(item: ScreenBoxResult) -> ScreenBox:
    return ScreenBox(label=item.label, confidence=item.confidence, box=item.box)


def _build_screen_response(
    items: list[ScreenImageDetectResult],
    start_time_text: str,
    conf: float,
    iou: float,
) -> ScreenDetectResponse:
    msg = "检测完成"
    if items and all(item.primary is None for item in items):
        msg = "检测完成，未识别到有效屏幕类型"
    return ScreenDetectResponse(
        code=200,
        start_time=start_time_text,
        end_time=now_ms_text(),
        msg=msg,
        conf=conf,
        iou=iou,
        total=len(items),
        results=[
            ScreenImageResult(
                index=item.index,
                cost_ms=item.cost_ms,
                primary=_to_screen_box(item.primary) if item.primary else None,
                detections=[_to_screen_box(det) for det in item.detections],
            )
            for item in items
        ],
    )


def _detect_screen(
    images_base64: list[str],
    start_time_text: str,
    conf: float | None,
    iou: float | None,
) -> ScreenDetectResponse:
    start_time_text = start_time_text or now_ms_text()
    items, conf_used, iou_used = detect_screen_from_base64_list(
        images_base64, conf=conf, iou=iou
    )
    app_state.increment_requests()
    return _build_screen_response(items, start_time_text, conf_used, iou_used)


@router.post(
    "/detect_screen",
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def detect_screen_async(request: Request) -> ScreenDetectResponse:
    try:
        images_base64, conf, iou = await _parse_screen_request(request)
        start_time_text = now_ms_text()
        return await run_in_threadpool(
            _detect_screen, images_base64, start_time_text, conf, iou
        )
    except (JSONDecodeError, ValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"code": 400, "msg": str(exc)})
    except Exception as exc:
        logger.exception("Async screen detection failed")
        raise HTTPException(
            status_code=500, detail={"code": 500, "msg": f"Detection failed: {exc}"}
        )
