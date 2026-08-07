from fastapi import APIRouter, HTTPException

from app.models.entities import GenericResponse, QuestionClassificationRequestObject
from app.services.usecases.question_classification import analyze_question_classification

try:
    from app.core.logging import get_logger
    log = get_logger(__name__)
except Exception:
    import logging
    log = logging.getLogger(__name__)


router = APIRouter(tags=["question_classification"])


@router.post("/text/question", response_model=GenericResponse)
async def question_classification(request: QuestionClassificationRequestObject):
    if not request or not request.segments:
        raise HTTPException(status_code=400, detail="无效请求：segments 为空")

    try:
        result, usage, used_model = await analyze_question_classification(request)
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("[question_classification] 分析失败")
        raise HTTPException(status_code=500, detail=f"问句分类失败：{exc}") from exc

    return GenericResponse[list](
        model=used_model,
        result=result,
        usage=usage,
    )
