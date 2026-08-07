from fastapi import APIRouter, HTTPException, Query
from app.models.entities import GenericResponse
from app.models.entities import AiSummaryItemRequestObject, AiSummaryBatchRequestObject
from app.services.usecases.ai_summaries import (
    generate_ai_summary_with_retry,
    summarize_items_batch
)

try:
    from app.core.logging import get_logger
    log = get_logger(__name__)
    log.setLevel(logging.INFO)
except Exception:
    import logging
    log = logging.getLogger(__name__)

router = APIRouter(tags=["ai_summary"])


@router.post("/v1/ai_summary", response_model=GenericResponse, summary="单条 AI 亮点/问题总结")
async def ai_summary(
    request: AiSummaryItemRequestObject,
    retry_attempts: int = Query(3, ge=1, le=5, description="校验失败时最大重试次数")
):
    if not request or not request.text:
        raise HTTPException(status_code=400, detail="无效请求：缺少 text")

    log.debug(f"收到 AI 单条总结请求：len(text)={len(request.text)}")

    data, usage_sum, used_model = await generate_ai_summary_with_retry(
        request,
        retry_attempts=retry_attempts
    )
    return GenericResponse[dict](
        model = used_model,
        result = data,
        usage = usage_sum,
    )


@router.post("/v1/ai_summaries", response_model=GenericResponse, summary="批量 AI 亮点/问题总结")
async def ai_summaries(
    request: AiSummaryBatchRequestObject,
    retry_attempts: int = Query(3, ge=1, le=6, description="校验失败时最大重试次数"),
    max_concurrency: int = Query(6, ge=1, le=32, description="批量并发上限"),
    enable_thinking: bool = Query(False, description="是否启用模型思考（仅部分模型支持）"),
):
    if not request or not request.items:
        raise HTTPException(status_code=400, detail="无效请求：缺少 items")


    log.debug(f"收到 AI 批量总结请求：size={len(request.items)}, max_concurrency={request.max_concurrency}")

    result_dict, usage_sum, used_model = await summarize_items_batch(
        request,
        retry_attempts=retry_attempts,
        enable_thinking=enable_thinking,
    )
    return GenericResponse[dict](
        model = used_model or "",
        result = result_dict,
        usage = usage_sum
    )