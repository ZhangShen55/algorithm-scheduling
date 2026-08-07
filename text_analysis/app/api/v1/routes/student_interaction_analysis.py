from fastapi import APIRouter, HTTPException

from app.models.entities import GenericResponse, StudentInteractionAnalysisRequestObject
from app.services.usecases.student_interaction_analysis import analyze_student_interactions

try:
    from app.core.logging import get_logger
    log = get_logger(__name__)
except Exception:
    import logging
    log = logging.getLogger(__name__)


router = APIRouter(tags=["student_interaction_analysis"])


@router.post("/v1/student_interaction_analysis", response_model=GenericResponse)
async def student_interaction_analysis(request: StudentInteractionAnalysisRequestObject):
    if not request or not request.textSegments:
        raise HTTPException(status_code=400, detail="无效请求：文本为空")
    if request.course_start >= request.course_end:
        raise HTTPException(status_code=400, detail="course_start 必须小于 course_end")

    try:
        result, usage, used_model = await analyze_student_interactions(request)
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("[student_interaction_analysis] 分析失败")
        raise HTTPException(status_code=500, detail=f"学生互动分析失败：{exc}") from exc

    return GenericResponse[dict](
        model=used_model,
        result=result,
        usage=usage,
    )
