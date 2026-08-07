from fastapi import APIRouter, HTTPException
from typing import List, Dict, Any, Optional
import time, json

from app.models.entities import GenericResponse, ClassroomOverviewRequestObject, UsageInfo
from app.services.prompts import load_prompt
from app.services.llm_executor import chat_raw
from app.utils import (
    concatenate_segments,
    llm_json_response_repair,
    strip_think_blocks, 
)

# logging（优先用你的全局 logger）
try:
    from app.core.logging import get_logger
    log = get_logger(__name__)
except Exception:
    import logging
    log = logging.getLogger(__name__)

router = APIRouter(tags=["keywords-segments2"])

@router.post("/v1.2/extract_keywords", response_model=GenericResponse)
async def extract_keywords_v12(request: ClassroomOverviewRequestObject):
    """
    单次 LLM：直接返回 [{keyword, times:[...]}, ...] 的数组。
    - system prompt 请写到 prompt/course_keywords_all_in_one.md
    - user 部分仅传 'start-end:text' 多行，由 prompt 约束输出格式
    """
    if not request or not request.textSegments:
        raise HTTPException(status_code=400, detail="无效请求：文本为空")

    # 1) 组装输入（每行 'start-end:text'）
    lines: List[str] = concatenate_segments(request.textSegments)
    user_prompt = "\n".join(lines)

    # 2) 加载一次到位的 system prompt（你来写内容，负责约束返回为数组结构）
    sys_prompt = load_prompt("course_keywords_extract.md") 

    # 3) 调用 LLM（一次）
    t0 = time.time()
    content, usage = await chat_raw(
        user_prompt=user_prompt,
        system_prompt=sys_prompt,
        model="qwen3-8b",       # 留空则内部会走默认模型
        max_tokens=4096,
        temperature=0.3,
        extra_body={"top_k": 20, "chat_template_kwargs": {"enable_thinking": True}},
        # top_p=0.9,
        # extra_body={"top_k": 20, "chat_template_kwargs": {"enable_thinking": False}},
    )

    # 4) 解析（只做必要处理：strip_think -> json_loads 或 repair 后 loads）
    content = strip_think_blocks(content)

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        try:
            data = json.loads(llm_json_response_repair(content))
        except Exception as e:
            log.exception("[v1.2] JSON 解析失败")
            raise HTTPException(status_code=500, detail=f"模型返回非 JSON：{e}")

    if not isinstance(data, list):
        raise HTTPException(status_code=500, detail="模型返回不是数组结构")

    # 5) 直接包装结果（不做二次清洗，追求速度）
    cost_ms = int((time.time() - t0) * 1000)
    result_obj: Dict[str, Any] = {
        "keywords": data,                       # 直接放数组
        "finished_time": int(time.time()),
        "process_time_ms": cost_ms,
        "finished_reason": "stop",
    }

    log.info(f"[v1.2] 完成：{len(data)} 关键词，耗时 {cost_ms} ms")
    return GenericResponse(
        model="qwen3-8b" or "",
        result=result_obj,
        usage=usage if isinstance(usage, UsageInfo) else None,  # chat_raw 已做过 coerce
    )
