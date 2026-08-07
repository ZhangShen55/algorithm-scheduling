import time
from typing import List, Dict
from fastapi import APIRouter, HTTPException
from app.models.entities import ClassroomOverviewRequestObject, GenericResponse, UsageInfo
from app.models.schemas import  LessonOverview
from app.services.retry import json_retry
from app.services.usecases.course_overviews import run_until_all_pass, parts_from_segments_simple
from app.services.usecases.summary import call_summary_ex


from app.utils import *
from app.core.config import settings
from app.core.logging import get_logger



router = APIRouter(tags=["course_overviews"])
try:
    from app.core.logging import get_logger
    log = get_logger(__name__)
    log.setLevel(logging.INFO)
except Exception:
    import logging
    log = logging.getLogger(__name__)

def _time_key(x: Dict[str, str]) -> int:
    # x{"time":"12-345", ...}
    t = x.get("time", "0-0")
    s = int(t.split("-", 1)[0]) if "-" in t else 0
    return s


@router.post("/v1/course_overviews", response_model=GenericResponse)
async def course_overviews(request: ClassroomOverviewRequestObject):
    if not request or not request.textSegments:
        raise HTTPException(status_code=400, detail="无效请求：文本为空")
    # 单次串行请求≈35s
    stat_time = time.time()

    course_start_time = int(float(request.textSegments[0].bg))
    course_end_time = int(float(request.textSegments[-1].ed))

    # 1) 按配置分段 + 并发跑到“全部通过校验”为止（或轮次用尽）
    parts = parts_from_segments_simple(
        request.textSegments,
        settings.COURSE_OVERVIEW_SEGMENT_COUNT,
    )
    try:
        seg_results, seg_usages = await run_until_all_pass(
            parts,
            model=request.model,
            concurrency=settings.COURSE_OVERVIEW_CONCURRENCY,
            timeout_sec=555555,
            max_rounds=5,
            enable_thinking=False
        )
    except Exception as e:
        log.error(f"[course_overviews]: 课程概览分段生成失败：{e}")
        raise HTTPException(status_code=500, detail=f"分段生成失败：{e}")
    try:
        key_points: List[str] = [s.key_points for s in seg_results]
    except Exception as e:
        log.error(f"[course_overviews]: key_points 提取失败：{e}")
        raise HTTPException(status_code=500, detail=f"key_points 提取失败：{e}")
    
    ###什么是'伏'###
    ##民间有两种说法：##
    #1.阴气不足为了躲避阳气，所以躲起来了，称为'伏'#
    #2.大'伏'天，意思是不易劳作，适合伏着，故而用'伏'表示#
    #已经入秋多日，忽觉得这个'伏'字用的很妙，所以了解下，第一种解释是网络提供，第二种是我的理解#

    # 3) 二次总结：full_overview + overall_label（同时拿usage）
    try:
        summary_result, summary_usage = await call_summary_ex(key_points=key_points, model=request.model)
    except Exception as e:
        log.error(f"[course_overviews]: 脑图总结生成失败：{e}")
        raise HTTPException(status_code=500, detail=f"总结生成失败：{e}")

    # 4) 组装最终 overview
    document_skims = sorted([s.document_skims for s in seg_results], key=_time_key)
    nodes = [s.nodes.model_dump() for s in seg_results]

    end_time = time.time()
    process_time_ms = int((end_time - stat_time) * 1000)

    overview = LessonOverview(
        overview={
            "full_overview": summary_result["full_overview"],
            "key_points": key_points,
            "document_skims": document_skims,
            "mindmap": {
                "overall_label": summary_result["overall_label"],
                "total_time": f"{course_start_time}-{course_end_time}",
                "nodes": nodes,
            },
        },
        process_time_ms=process_time_ms,
        finished_reason="stop",
    )

    # 5) token 统计：分段请求合计 + 1 次总结
    total_usage_info = sum_usage(seg_usages + [summary_usage])

    return GenericResponse(
        model=request.model,
        result=overview,
        usage=total_usage_info,
    )
