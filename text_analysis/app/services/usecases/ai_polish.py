import logging  
from typing import List, Optional, Tuple
from fastapi import HTTPException   
from app.models.entities import AiPolishRequestObject, UsageInfo
from app.services.llm_executor import chat_raw
import json, random, asyncio
from app.utils import (
    strip_think_blocks,
    coerce_usage,
    sum_usage,
    llm_json_response_repair,
)
from app.services.prompts import load_prompt

# 日志
try:
    from app.core.logging import get_logger
    log = get_logger(__name__)
    log.setLevel(logging.DEBUG)
except Exception:
    import logging
    log = logging.getLogger(__name__)

# ---------- helpers ----------
def _build_user_input_str(req: AiPolishRequestObject) -> str:
    return (
        f"润色前原文：{req.text}\n"
        f"长度要求：合并为一段，字符数在 {req.min_chars}~{req.max_chars} 之间。\n"
        "保留原意，提升逻辑连贯与表达准确，必要时可适度补充衔接语。"
    )

async def _llm_once_json(
    *, messages: List[dict], model: Optional[str], temperature: float
) -> Tuple[dict, Optional[dict]]:

    content, usage = await chat_raw(
        messages=messages,
        model=model,
        max_tokens=1024,
        temperature=temperature,
        top_p=0.9,
        presence_penalty=1.1,
        extra_body={"top_k": 20, "chat_template_kwargs": {"enable_thinking": False}},
    )
    content = strip_think_blocks(content)
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = json.loads(llm_json_response_repair(content))
    return data, coerce_usage(usage)    

# main function
async def generate_ai_polish_with_retry(
    req: AiPolishRequestObject,
    *,
    retry_attempts: int = 3,
    enable_thinking: bool = False,
) -> Tuple[dict, UsageInfo, str]:
    """
    期望模型返回：{ "AI_Polish": "<润色后一整段文本>" }
    通过后处理校验：
      - 必须包含 AI_Polish 且为 str
      - 长度满足 [min_chars, max_chars]
    校验失败：指数退避，重试到上限后返回带数字错误码的 HTTPException
    错误码：
      2001 缺少输入文本
      2002 缺少 AI_Polish 键或类型错误
      2003 长度不合规
      2004 模型多次生成失败（兜底）
    """
    # 额外区间关系断言（避免 min > max）
    if req.min_chars > req.max_chars:
        raise HTTPException(
            status_code=400,
            detail={"error_code": 2001, "message": "参数错误：min_chars 不得大于 max_chars"}
        )

    if not (req.text or "").strip():
        raise HTTPException(
            status_code=400,
            detail={"error_code": 2001, "message": "text 不能为空"}
        )

    sys_prompt = load_prompt("ai_polish.md")
    user_input_str = _build_user_input_str(req)

    # 只允许一个键，且为一段话；禁止任何额外说明
    user_task = (
        "请严格返回 JSON，格式固定且唯一：\n"
        '{ "AI_Polish": "<仅一段润色后的正文，不含任何标题或解释>"}\n'
        "要求：\n"
        "1) 只允许一个键，键名必须是 AI_Polish；\n"
        "2) 将原文优化为一段流畅、准确、逻辑清晰的文字，可合并与润色改写，但必须保留原意；\n"
        f"3) 长度必须在 {req.min_chars} 到 {req.max_chars} 之间；\n"
        "4) 禁止输出除该 JSON 外的任何多余内容。"
    )

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_input_str},
        {"role": "user", "content": user_task},
    ]

    usages: List[Optional[UsageInfo]] = []
    last_reason = "unknown"
    last_code = 2004
    model = req.model

    for attempt in range(1, max(1, retry_attempts) + 1):
        data, u = await _llm_once_json(
            messages=messages,
            model=model,
            temperature=req.temperature or 0.4
        )
        usages.append(u)

        if isinstance(data, dict) and "AI_Polish" in data and isinstance(data["AI_Polish"], str):
            text = data["AI_Polish"].strip()
            length = len(text)
            if req.min_chars <= length <= req.max_chars:
                if attempt > 1:
                    log.info(f"[ai_polish] 校验通过，重试次数={attempt-1}")
                return {"AI_Polish": text, "length": length}, sum_usage(usages), (model or "auto")
            last_reason = f"长度不合规：{length}（要求 {req.min_chars}–{req.max_chars}）"
            last_code = 2003
        else:
            last_reason = "缺少 AI_Polish 键或内容为空/类型错误"
            last_code = 2002

        log.warning(f"[ai_polish] 校验失败 {attempt}/{retry_attempts}: {last_reason}")
        if attempt < retry_attempts:
            backoff = min(2 ** (attempt - 1), 4) + random.uniform(0, 0.3)
            await asyncio.sleep(backoff)

    # 重试用尽仍失败
    raise HTTPException(
        status_code=422,
        detail={"error_code": last_code, "message": last_reason}
    )