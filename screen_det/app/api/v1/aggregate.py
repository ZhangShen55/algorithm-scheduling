from __future__ import annotations

import logging
from json import JSONDecodeError

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError

from app.api.v1.common import now_ms_text
from app.core.config import get_settings
from app.core.state import app_state
from app.schemas.aggregate import (
    AggregateDetectRequest,
    AggregateDetectResponse,
    AggregateErrorResponse,
)
from app.services.aggregate_detector import run_detect_all


router = APIRouter(tags=["aggregate"])
logger = logging.getLogger(__name__)


async def _parse_aggregate_request(request: Request) -> AggregateDetectRequest:
    content_type = request.headers.get("content-type", "").lower()
    if "application/json" not in content_type:
        raise ValueError("Content-Type must be application/json")
    payload = await request.json()
    if not isinstance(payload, dict):
        raise ValueError('Request body must be JSON: {"image": "<base64>"}')
    if "image" not in payload:
        raise ValueError('Missing field "image"')
    return AggregateDetectRequest(**payload)


@router.post(
    "/detect_all",
    response_model=AggregateDetectResponse,
    responses={
        400: {"model": AggregateErrorResponse},
        500: {"model": AggregateErrorResponse},
        503: {"model": AggregateErrorResponse},
    },
)
async def detect_all(request: Request) -> AggregateDetectResponse:
    try:
        settings = get_settings()
        if not settings.aggregate_detection.enabled:
            raise HTTPException(
                status_code=503,
                detail={"code": 503, "msg": "聚合检测接口未启用"},
            )
        body = await _parse_aggregate_request(request)
        response = await run_in_threadpool(
            run_detect_all,
            body.image.strip(),
            now_ms_text(),
            body.tilt_threshold,
            body.screen_conf,
            body.screen_iou,
            body.occlusion_threshold,
            body.occlusion_area_ratio,
            body.include,
        )
        app_state.increment_requests()
        return response
    except HTTPException:
        raise
    except (JSONDecodeError, ValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"code": 400, "msg": str(exc)})
    except Exception as exc:
        logger.exception("detect_all failed")
        raise HTTPException(
            status_code=500,
            detail={"code": 500, "msg": f"Detection failed: {exc}"},
        )
