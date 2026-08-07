from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.core.config import settings
from app.models.entities import ClassroomOverviewRequestObject, GenericResponse
from app.services.usecases.course_time_analysis import analyze_course_time

try:
    from app.core.logging import get_logger
    log = get_logger(__name__)
except Exception:
    import logging
    log = logging.getLogger(__name__)


router = APIRouter(tags=["course_time_analysis"])


@router.post("/v1/course_time_analysis", response_model=GenericResponse)
async def course_time_analysis(
    request: ClassroomOverviewRequestObject,
    enable_llm_validation: Optional[bool] = Query(
        None,
        description="是否启用 LLM 候选校验；不传则使用 config.toml 配置",
    ),
):
    if not request or not request.textSegments:
        raise HTTPException(status_code=400, detail="无效请求：文本为空")

    use_llm = (
        settings.COURSE_TIME_ANALYSIS_ENABLE_LLM_VALIDATION
        if enable_llm_validation is None
        else enable_llm_validation
    )
    try:
        result, usage = await analyze_course_time(
            request.textSegments,
            model=request.model,
            enable_llm_validation=use_llm,
            llm_concurrency=settings.COURSE_TIME_ANALYSIS_LLM_CONCURRENCY,
            llm_retry_attempts=settings.COURSE_TIME_ANALYSIS_LLM_RETRY_ATTEMPTS,
            max_llm_candidates=settings.COURSE_TIME_ANALYSIS_MAX_LLM_CANDIDATES,
            min_break_duration_sec=settings.COURSE_TIME_ANALYSIS_MIN_BREAK_DURATION_SEC,
            max_break_duration_sec=settings.COURSE_TIME_ANALYSIS_MAX_BREAK_DURATION_SEC,
            course_start_candidate_budget=settings.COURSE_TIME_ANALYSIS_COURSE_START_CANDIDATE_BUDGET,
            course_end_candidate_budget=settings.COURSE_TIME_ANALYSIS_COURSE_END_CANDIDATE_BUDGET,
            break_start_candidate_budget=settings.COURSE_TIME_ANALYSIS_BREAK_START_CANDIDATE_BUDGET,
            break_end_candidate_budget=settings.COURSE_TIME_ANALYSIS_BREAK_END_CANDIDATE_BUDGET,
            weak_candidate_budget=settings.COURSE_TIME_ANALYSIS_WEAK_CANDIDATE_BUDGET,
            candidate_context_before_sec=settings.COURSE_TIME_ANALYSIS_CANDIDATE_CONTEXT_BEFORE_SEC,
            candidate_context_after_sec=settings.COURSE_TIME_ANALYSIS_CANDIDATE_CONTEXT_AFTER_SEC,
            fallback_window_sec=settings.COURSE_TIME_ANALYSIS_FALLBACK_WINDOW_SEC,
            max_fallback_windows=settings.COURSE_TIME_ANALYSIS_MAX_FALLBACK_WINDOWS,
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("[course_time_analysis] 分析失败")
        raise HTTPException(status_code=500, detail=f"课程时间分析失败：{exc}")

    return GenericResponse(
        model=request.model,
        result=result,
        usage=usage,
    )
