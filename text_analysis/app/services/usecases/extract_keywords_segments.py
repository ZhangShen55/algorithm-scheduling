from fastapi import APIRouter, HTTPException
from typing import List, Dict, Any, Tuple, Optional
import asyncio, time, json

from app.models.entities import GenericResponse, ClassroomOverviewRequestObject, ChatMessage, UsageInfo
from app.services.prompts import load_prompt
from app.services.llm_executor import chat_raw, chat__raw_ex
from app.utils import (
    concatenate_segments,
    split_into_4_parts,
    segments_to_plain_text,
    parse_time_pair,
    sort_times,
    sum_usage,
    llm_json_response_repair,
)

try:
    from app.core.logging import get_logger
    log = get_logger(__name__)
    log.setLevel(logging.INFO)
except Exception:
    import logging
    log = logging.getLogger(__name__)

router = APIRouter(tags=["keywords-segments"])

# ---------- 使用 chat_raw 的两个步骤 ----------

async def discover_top_keywords(*, model: Optional[str], plain_text: str,enable_thinking: bool = False) -> Tuple[List[str], UsageInfo | None]:
    """Step1: 全文不带时间 ——> 提取出关键词（6个）"""
    sys_prompt = load_prompt("课程关键词提取.md")
    log.debug(f"sys_prompt: {sys_prompt}")
    content, usage = await chat_raw(
        user_prompt=plain_text,
        system_prompt=sys_prompt,
        model=model,
        max_tokens=512,
        temperature=0.7,
        top_p=0.9,
        extra_body={"top_k": 20, "chat_template_kwargs": {"enable_thinking": enable_thinking}},
        # response_format 可不传，因为有些服务端只支持 json_object
    )
    try:
        data = json.loads(content)["keywords"]
    except json.JSONDecodeError:
        data = json.loads(llm_json_response_repair(content))["keywords"]
    if not isinstance(data, list):
        raise HTTPException(status_code=500, detail="发现关键词失败：返回不是数组")
    # 归一化 & 去重，数量你可以在 prompt 中限制为 6
    ks: List[str] = []
    for x in data:
        if isinstance(x, str):
            k = x.strip()
            if k and k not in ks:
                ks.append(k)
    if not ks:
        raise HTTPException(status_code=500, detail="未发现任何关键词")
    log.info(f"[v1.1] 发现关键词: {ks}")
    return ks, usage

async def locate_times_for_part(*, model: Optional[str], part_lines: str, keywords: List[str], enable_thinking: bool = False) -> Tuple[List[Dict[str, Any]], UsageInfo | None]:
    """
    Step2: 定位关键字出现的时间区间
    """
    sys_prompt = load_prompt("课程关键词时间定位部分.md")
    user_prompt = (
        f"//no_think 关键词数组：{json.dumps(keywords, ensure_ascii=False)}\n"
        
        f"课堂文本：\n{part_lines}\n"

    )
    # log.info(f"user_prompt: {user_prompt}")
    content, usage = await chat__raw_ex(
        user_prompt=user_prompt,
        system_prompt=sys_prompt,
        model=model,
        max_tokens=8192,
        temperature=0.7,
        top_p=1,
        extra_body={"top_k": 20, "chat_template_kwargs": {"enable_thinking": enable_thinking}},
    )
    # 宽松解析为 list
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = json.loads(llm_json_response_repair(content))
    if not isinstance(data, list):
        raise HTTPException(status_code=500, detail="定位时间失败：返回不是数组")
    # 只保留规范项
    cleaned: List[Dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        k = str(item.get("keyword", "")).strip()
        ts = item.get("times", [])
        if not k:
            continue
        if not isinstance(ts, list):
            ts = []
        # 过滤非法时间并排序去重
        valid = []
        for t in ts:
            pair = parse_time_pair(str(t))
            if pair is not None:
                valid.append(f"{pair[0]}-{pair[1]}")
        cleaned.append({"keyword": k, "times": sort_times(valid)})
    return cleaned, usage

