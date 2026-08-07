from fastapi import APIRouter, HTTPException, Query

from app.models.entities import CourseEvaluationRequestObject, GenericResponse
from app.services.usecases.course_evaluation import generate_course_evaluation_with_retry
from app.core.config import settings

try:
    from app.core.logging import get_logger
    log = get_logger(__name__)
    log.setLevel(logging.INFO)
except Exception:
    import logging
    log = logging.getLogger(__name__)

router = APIRouter(tags=["course_evaluation"])

'''
  "eval_weight": {
    "base_score": 8.0,
    "politics": 0.2,
    "content": 0.2,
    "attitude": 0.2,
    "method": 0.2,
    "effect": 0.2
  }
'''
# 评价权重
EVAL_WEIGHT = settings.EVAL_WEIGHT

@router.post("/v1/course_evaluation", response_model=GenericResponse)
async def course_evaluation(
    request: CourseEvaluationRequestObject,
    retry_attempts: int = Query(3, ge=1, le=5, description="校验失败时最大重试次数"),
):
    if not request or not request.text or not request.course_name or not request.course_model:
        raise HTTPException(status_code=400, detail="无效请求：缺少 text/course_name/course_model")

    log.debug(f"收到课程评价请求：{request}")

    data, usage_sum = await generate_course_evaluation_with_retry(
        request,
        retry_attempts=retry_attempts,
        eval_weight=EVAL_WEIGHT
    )
    return GenericResponse(
        model=request.model,
        result=data,
        usage=usage_sum,
    )
