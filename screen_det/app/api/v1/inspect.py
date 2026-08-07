from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError

from app.api.v1.common import now_ms_text
from app.core.state import app_state
from app.schemas.inspect import InspectRequest, InspectResponse
from app.schemas.tilt import ErrorResponse
from app.services.inspect_detector import run_inspect


router = APIRouter(tags=["inspect"])
logger = logging.getLogger(__name__)


@router.post(
    "/detect_inspect",
    response_model=InspectResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def detect_inspect(body: InspectRequest) -> InspectResponse:
    try:
        start_time_text = now_ms_text()
        response = await run_in_threadpool(
            run_inspect,
            body.image.strip(),
            start_time_text,
            body.tilt_threshold,
            body.conf,
            body.iou,
        )
        app_state.increment_requests()
        return response
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"code": 400, "msg": str(exc)})
    except Exception as exc:
        logger.exception("detect_inspect failed")
        raise HTTPException(
            status_code=500, detail={"code": 500, "msg": f"Detection failed: {exc}"}
        )
