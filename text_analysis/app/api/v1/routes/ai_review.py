
from fastapi import APIRouter, HTTPException, Query
from app.models.entities import AiGeneratedReviewRequestObject, GenericResponse
from app.services.usecases.ai_generated_evaluation import generate_ai_review_from_tags_with_retry

try:
    from app.core.logging import get_logger
    import logging
    log = get_logger(__name__)
    log.setLevel(logging.INFO)
except Exception:
    import logging
    log = logging.getLogger(__name__)

router = APIRouter(tags=["ai_review"])

@router.post("/v1/ai_review", response_model=GenericResponse[dict])
async def ai_review(
    request: AiGeneratedReviewRequestObject,
    retry_attempts: int = Query(3, ge=1, le=5, description="生成失败时最大重试次数"),
):
    if not request.advantage_tags and not request.problem_tags:
        raise HTTPException(status_code=400, detail="无效请求：请至少提供一类标签（优势或问题）")

    log.debug(f"收到 AI 写评价请求：{request}")

    text, usage_sum, used_model = await generate_ai_review_from_tags_with_retry(
        request, retry_attempts=retry_attempts
    )

    return GenericResponse[dict](
        model=used_model,
        result=text,
        usage=usage_sum,
    )
