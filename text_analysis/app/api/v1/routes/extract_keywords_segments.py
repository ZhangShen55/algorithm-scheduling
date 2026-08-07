import asyncio
import time

from typing import List, Dict, Any
from fastapi import APIRouter, HTTPException
from app.models.entities import (
    GenericResponse, ClassroomOverviewRequestObject, ChatMessage, UsageInfo
)
from app.services.llm_client import get_async_client, get_model_name
from app.services.prompts import load_prompt
from app.services.usecases.extract_keywords_segments import (
    discover_top_keywords, locate_times_for_part
)
from app.utils import *


# 优先主进程log
try:
    from app.core.logging import get_logger
    log = get_logger(__name__)
    log.setLevel(logging.INFO)
except Exception:
    import logging
    log = logging.getLogger(__name__)
    log.setLevel(logging.INFO)

router = APIRouter(tags=["keywords-segments"])

@router.post("/v1.1/extract_keywords", response_model=GenericResponse)
async def extract_keywords_v11(request: ClassroomOverviewRequestObject):
    """
    策略：
    1) 用全文（不带时间）发现 6 个关键词（1 次调用）
    2) 把 textSegments 四等分；每段把“start-end:text”喂给模型，要求仅标注上述 6 词的时间段（4 次调用，可并发）
    3) 汇总四段的 times，去重并按起始时间排序
    4) 返回 {keywords:[{keyword, times: [...]}, ...]}，并汇总真实 usage
    """
    if not request or not request.textSegments:
        raise HTTPException(status_code=400, detail="无效请求：文本为空")

    start = time.time()

    # 提取出6个关键词
    plain_text = segments_to_plain_text(request.textSegments)
    try:
        top_keywords, usage_disc = await discover_top_keywords(model=request.model, plain_text=plain_text, enable_thinking=True)
        log.debug(f"[v1.1] 发现关键词：{top_keywords}, usage={usage_disc}")
    except HTTPException:
        raise
    except Exception as e:
        log.exception("[v1.1] 发现关键词阶段失败")
        raise HTTPException(status_code=500, detail=f"发现关键词失败：{e}")

    # 2) 四等分 + 并发定位时间
    parts = split_into_4_parts(request.textSegments)
    if not parts:
        raise HTTPException(status_code=400, detail="文本片段分组为空")

    async def run_part(idx: int):
        lines = concatenate_segments(parts[idx])
        part_lines = "\n".join(lines)
        try:
            return await locate_times_for_part(model=request.model, part_lines=part_lines, keywords=top_keywords, enable_thinking=True)
        except HTTPException:
            raise
        except Exception as e:
            log.exception(f"[v1.1] 定位时间失败 part={idx}")
            raise HTTPException(status_code=500, detail=f"定位时间失败(part={idx})：{e}")

    tasks = [asyncio.create_task(run_part(i)) for i in range(len(parts))]
    part_results = await asyncio.gather(*tasks)

    # 3) 汇总：keyword -> times[]
    agg: Dict[str, List[str]] = {k: [] for k in top_keywords}
    usages: List[UsageInfo | None] = [usage_disc]
    for (arr, u) in part_results:
        usages.append(u)
        for item in arr:                   
            k = item.get("keyword", "")
            ts = item.get("times", []) or []
            if k in agg:
                agg[k].extend(ts)

    # 去重 + 排序
    for k in list(agg.keys()):
        agg[k] = sort_times(agg[k])

    # 4) 组装结果
    result_keywords = [{"keyword": k, "times": agg[k]} for k in top_keywords]
    cost_ms = int((time.time() - start) * 1000)
    result_obj: Dict[str, Any] = {
        "keywords": result_keywords,
        "finished_time": int(time.time()),
        "process_time_ms": cost_ms,
        "finished_reason": "stop",
    }

    # usage 汇总（1 + 4 次）
    usage_total = sum_usage(usages)

    log.debug(f"[v1.1] 完成：{len(result_keywords)} 关键词，耗时 {cost_ms} ms")
    return GenericResponse(
        model=request.model,
        result=result_obj,
        usage=usage_total
    )