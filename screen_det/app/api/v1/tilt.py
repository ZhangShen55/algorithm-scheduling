from __future__ import annotations

import json
import logging
import time
from dataclasses import replace
from json import JSONDecodeError

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError

from app.api.v1.common import now_ms_text
from app.core.config import get_settings
from app.core.state import app_state
from app.schemas.tilt import ErrorResponse, TiltResponse, TiltResultData
from app.services.tilt_detector import detect_from_base64


router = APIRouter(tags=["tilt"])
logger = logging.getLogger(__name__)


def _resolve_tilt_threshold(override: float | None) -> float:
    settings = get_settings()
    if override is None:
        return settings.detection.tilt_threshold
    return float(override)


def _detect(
    image_base64: str,
    start_time_text: str | None = None,
    tilt_threshold: float | None = None,
) -> TiltResponse:
    settings = get_settings()
    threshold_used = _resolve_tilt_threshold(tilt_threshold)
    detection = replace(settings.detection, tilt_threshold=threshold_used)
    start_time_text = start_time_text or now_ms_text()
    start_time = time.time()
    result = detect_from_base64(
        image_base64,
        detection,
        settings.runtime.max_image_bytes,
    )
    cost_ms = round((time.time() - start_time) * 1000, 2)
    app_state.increment_requests()
    logger.info(
        "tilt_detection_internal is_tilted=%s angle=%.2f threshold=%.2f cost_ms=%.2f",
        result.is_tilted,
        result.angle,
        threshold_used,
        cost_ms,
    )
    return TiltResponse(
        code=200,
        start_time=start_time_text,
        end_time=now_ms_text(),
        msg=result.message,
        tilt_threshold=threshold_used,
        result=TiltResultData(
            is_tilted=result.is_tilted,
            angle=round(result.angle, 2),
            cost_ms=cost_ms,
        ),
    )


def _tilt_request_from_payload(payload: object) -> tuple[str, float | None]:
    if isinstance(payload, str):
        if not payload.strip():
            raise ValueError("Request body is empty")
        return payload.strip(), None
    if isinstance(payload, dict):
        image_base64 = None
        if "images" in payload:
            images = payload["images"]
            if isinstance(images, str) and images.strip():
                image_base64 = images.strip()
            else:
                raise ValueError('Field "images" must be a non-empty base64 string')
        elif "image" in payload:
            value = payload["image"]
            if isinstance(value, str) and value.strip():
                image_base64 = value.strip()
            else:
                raise ValueError('Field "image" must be a non-empty base64 string')
        else:
            raise ValueError('Missing field "images" or "image"')

        tilt_threshold = payload.get("tilt_threshold")
        if tilt_threshold is not None:
            tilt_threshold = float(tilt_threshold)
        return image_base64, tilt_threshold

    raise ValueError(
        'Request body must be raw base64 text or JSON: {"images": "<base64>"}'
    )


async def _parse_tilt_request(request: Request) -> tuple[str, float | None]:
    content_type = request.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        payload = await request.json()
        return _tilt_request_from_payload(payload)

    body = (await request.body()).decode("utf-8").strip()
    if not body:
        raise ValueError("Request body is empty")
    if body.startswith("{"):
        return _tilt_request_from_payload(json.loads(body))
    return body, None


@router.post(
    "/detect_tilt",
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def detect_tilt_async(request: Request) -> TiltResponse:
    try:
        image_base64, tilt_threshold = await _parse_tilt_request(request)
        start_time_text = now_ms_text()
        return await run_in_threadpool(
            _detect, image_base64, start_time_text, tilt_threshold
        )
    except (JSONDecodeError, ValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"code": 400, "msg": str(exc)})
    except Exception as exc:
        logger.exception("Async tilt detection failed")
        raise HTTPException(
            status_code=500, detail={"code": 500, "msg": f"Detection failed: {exc}"}
        )
