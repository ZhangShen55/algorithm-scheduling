# app/api/routes/ai_polish.py

from fastapi import APIRouter, Query, HTTPException
from app.models.entities import AiPolishRequestObject, GenericResponse
from app.services.usecases.ai_polish import generate_ai_polish_with_retry

try:
    from app.core.logging import get_logger
    import logging
    log = get_logger(__name__)
    log.setLevel(logging.INFO)
except Exception:
    import logging
    log = logging.getLogger(__name__)

router = APIRouter(tags=["ai_polish"])

@router.post("/v1/ai_polish", response_model=GenericResponse[dict])
async def ai_polish(
    request: AiPolishRequestObject,
    retry_attempts: int = Query(3, ge=1, le=5, description="生成失败时最大重试次数"),
):
    if not request.text.strip():
        raise HTTPException(status_code=400, detail={"error_code": 2001, "message": "text 不能为空"})

    log.debug(f"收到 AI 润色请求：{request}")

    data, usage_sum, used_model = await generate_ai_polish_with_retry(
        request, retry_attempts=retry_attempts
    )

    return GenericResponse[dict](
        model=used_model,
        result=data,   # {"AI_Polish": "<润色后一段>", "length": N}
        usage=usage_sum,
    )
