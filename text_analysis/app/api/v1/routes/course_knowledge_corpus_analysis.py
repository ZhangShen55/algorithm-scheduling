from fastapi import APIRouter, HTTPException

from app.models.entities import CourseKnowledgeCorpusAnalysisRequestObject, GenericResponse
from app.services.usecases.course_knowledge_corpus_analysis import analyze_course_knowledge_corpus

try:
    from app.core.logging import get_logger
    log = get_logger(__name__)
except Exception:
    import logging
    log = logging.getLogger(__name__)


router = APIRouter(tags=["course_knowledge_corpus_analysis"])


@router.post("/v1/course_knowledge_corpus", response_model=GenericResponse)
async def course_knowledge_corpus_analysis(request: CourseKnowledgeCorpusAnalysisRequestObject):
    if not request or not request.textSegments:
        raise HTTPException(status_code=400, detail="无效请求：文本为空")
    if request.course_start >= request.course_end:
        raise HTTPException(status_code=400, detail="course_start 必须小于 course_end")

    try:
        result, usage, used_model = await analyze_course_knowledge_corpus(request)
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("[course_knowledge_corpus_analysis] 分析失败")
        raise HTTPException(status_code=500, detail=f"课堂知识点与语料分析失败：{exc}") from exc

    return GenericResponse[dict](
        model=used_model,
        result=result,
        usage=usage,
    )
