from fastapi import APIRouter, HTTPException

from app.models.entities import GenericResponse, LanguageExpressionAnalysisRequestObject
from app.services.usecases.language_expression_analysis import analyze_language_expression

try:
    from app.core.logging import get_logger
    log = get_logger(__name__)
except Exception:
    import logging
    log = logging.getLogger(__name__)


router = APIRouter(tags=["language_expression_analysis"])


@router.post("/v1/language_expression_analysis", response_model=GenericResponse)
async def language_expression_analysis(request: LanguageExpressionAnalysisRequestObject):
    if not request or not request.textSegments:
        raise HTTPException(status_code=400, detail="无效请求：文本为空")

    try:
        result, usage, used_model = await analyze_language_expression(request)
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("[language_expression_analysis] 分析失败")
        raise HTTPException(status_code=500, detail=f"语言表达分析失败：{exc}") from exc

    return GenericResponse[dict](
        model=used_model,
        result=result,
        usage=usage,
    )
