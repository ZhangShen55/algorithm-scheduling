from __future__ import annotations

import logging
from json import JSONDecodeError

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError

from app.core.state import app_state
from app.schemas.occlusion import (
    OcclusionErrorResponse,
    OcclusionRequest,
    OcclusionResponse,
)
from app.services.occlusion_detector import detect_occlusion_from_base64


router = APIRouter(tags=["occlusion"])
logger = logging.getLogger(__name__)


async def _parse_occlusion_request(request: Request) -> OcclusionRequest:
    content_type = request.headers.get("content-type", "").lower()
    if "application/json" not in content_type:
        raise ValueError("Content-Type must be application/json")
    payload = await request.json()
    if not isinstance(payload, dict):
        raise ValueError('Request body must be JSON: {"image": "<base64>"}')
    if "image" not in payload:
        raise ValueError('Missing field "image"')
    body = OcclusionRequest(**payload)
    return body


@router.post(
    "/detect_occlusion",
    response_model=OcclusionResponse,
    responses={400: {"model": OcclusionErrorResponse}, 500: {"model": OcclusionErrorResponse}},
)
async def detect_occlusion(request: Request) -> OcclusionResponse:
    try:
        body = await _parse_occlusion_request(request)
        result = await run_in_threadpool(
            detect_occlusion_from_base64,
            body.image.strip(),
            body.threshold,
            body.area_ratio,
        )
        app_state.increment_requests()
        return OcclusionResponse(
            code=200,
            msg="检测完成",
            is_occluded=result.is_occluded,
            occlusion_area_ratio=result.occlusion_area_ratio,
            score=result.score,
            threshold=result.threshold,
            area_ratio=result.area_ratio,
            message=result.message,
        )
    except (JSONDecodeError, ValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"code": 400, "msg": str(exc)})
    except Exception as exc:
        logger.exception("occlusion detection failed")
        raise HTTPException(
            status_code=500, detail={"code": 500, "msg": f"Detection failed: {exc}"}
        )
