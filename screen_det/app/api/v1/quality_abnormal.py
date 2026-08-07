from __future__ import annotations

import logging
from json import JSONDecodeError

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError

from app.core.state import app_state
from app.schemas.quality_abnormal import (
    QualityAbnormalErrorResponse,
    QualityAbnormalItem,
    QualityAbnormalRequest,
    QualityAbnormalResponse,
)
from app.services.quality_abnormal_detector import (
    QualityAbnormalResultItem,
    detect_quality_abnormal_from_base64,
)


router = APIRouter(tags=["quality_abnormal"])
logger = logging.getLogger(__name__)


async def _parse_quality_abnormal_request(request: Request) -> str:
    content_type = request.headers.get("content-type", "").lower()
    if "application/json" not in content_type:
        raise ValueError("Content-Type must be application/json")
    payload = await request.json()
    if not isinstance(payload, dict):
        raise ValueError('Request body must be JSON: {"image": "<base64>"}')
    if "image" not in payload:
        raise ValueError('Missing field "image"')
    body = QualityAbnormalRequest(**payload)
    return body.image.strip()


def _to_item(item: QualityAbnormalResultItem) -> QualityAbnormalItem:
    return QualityAbnormalItem(type=item.type, score=item.score, message=item.message)


@router.post(
    "/detect_quality_abnormal",
    response_model=QualityAbnormalResponse,
    responses={400: {"model": QualityAbnormalErrorResponse}, 500: {"model": QualityAbnormalErrorResponse}},
)
async def detect_quality_abnormal(request: Request) -> QualityAbnormalResponse:
    try:
        image = await _parse_quality_abnormal_request(request)
        result = await run_in_threadpool(
            detect_quality_abnormal_from_base64,
            image,
        )
        app_state.increment_requests()
        return QualityAbnormalResponse(
            code=200,
            msg="检测完成",
            is_abnormal=result.is_abnormal,
            abnormal_types=result.abnormal_types,
            results=[_to_item(item) for item in result.results],
            message=result.message,
        )
    except (JSONDecodeError, ValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"code": 400, "msg": str(exc)})
    except Exception as exc:
        logger.exception("quality abnormal detection failed")
        raise HTTPException(
            status_code=500, detail={"code": 500, "msg": f"Detection failed: {exc}"}
        )
