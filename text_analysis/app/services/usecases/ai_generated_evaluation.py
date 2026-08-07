
import asyncio, random
from typing import Tuple, List, Optional
from fastapi import HTTPException

from app.services.llm_executor import chat_raw
from app.services.prompts import load_prompt
from app.models.entities import UsageInfo, AiGeneratedReviewRequestObject

import json

from app.utils import (
    strip_think_blocks,
    coerce_usage,
    sum_usage,
    llm_json_response_repair
)

# logging 使用全局
try:
    from app.core.logging import get_logger
    log = get_logger(__name__)
    log.setLevel(logging.DEBUG)
except Exception:
    import logging
    log = logging.getLogger(__name__)


def _build_user_input_str(req: AiGeneratedReviewRequestObject) -> str:
    """严格、稳定地拼接 user 输入串（便于回溯与复用 prompt）。"""
    adv = "、".join(req.advantage_tags) if req.advantage_tags else "（无）"
    prob = "、".join(req.problem_tags) if req.problem_tags else "（无）"
    return (
        f"优势标签：{adv}\n"
        f"问题标签：{prob}\n"
        f"字数上限：{req.max_chars}（不要超过）\n"
    )


async def _llm_once_json(
    *, messages: List[dict], model: Optional[str], temperature: float, enable_thinking: bool = False
) -> tuple[dict, Optional[UsageInfo]]:
    content, usage = await chat_raw(
        messages=messages,
        model=model,
        max_tokens=1024,
        temperature=temperature,
        top_p=0.9,
        presence_penalty=1.1,
        extra_body={"top_k": 20, "chat_template_kwargs": {"enable_thinking": enable_thinking}},
    )
    content = strip_think_blocks(content)
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = json.loads(llm_json_response_repair(content))
    return data, coerce_usage(usage)



async def generate_ai_review_from_tags_with_retry(
    req: AiGeneratedReviewRequestObject,
    *,
    retry_attempts: int = 3,
    enable_thinking: bool = False,
) -> Tuple[dict, UsageInfo, str]:
    """
    - 载入系统提示词 prompts/ai_generated_evalusation.md
    - 用户输入串 + 任务指令
    - LLM 调用（指数退避）→ JSON 解析/修复
    - 结构校验：必须包含 "AI_Review": <str>
    - 字数校验
    - 返回 ({"AI_Review": text, "length": length}, usage_sum, used_model)
    - 校验失败：抛出 HTTPException，detail 内含 error_code & message
    """

    sys_prompt = load_prompt("ai_generated_review.md")
    user_input_str = _build_user_input_str(req)

    user_task = (
        "请基于上面的要点撰写课程/课堂评价，并严格输出 JSON："
        '{ "AI_Review": "......" }'
        "\n写作要求：只允许一个键，键名必须是 AI_Review；键值是一整段自然语言，"
        "涵盖总体评价、优点、问题和改进建议；不要输出任何额外文字。"
        f"\n1) 字数必须小于{req.max_chars}。"
    )

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_input_str},
        {"role": "user", "content": user_task},
    ]

    usages: List[Optional[UsageInfo]] = []
    last_reason = "unknown"
    last_code = 1004 # 默认兜底错误码： 生成失效
    model = req.model

    for attempt in range(1, max(1, retry_attempts) + 1):
        data, u = await _llm_once_json(
            messages=messages, model=model, temperature=req.temperature or 0.4, enable_thinking=enable_thinking
        )
        usages.append(u)

        if isinstance(data, dict) and "AI_Review" in data and isinstance(data["AI_Review"], str):
            text = data["AI_Review"].strip()
            length = len(text)
            if length <= req.max_chars:
                if attempt > 1:
                    log.info(f"[ai_generated_evalusation] 校验通过，重试次数={attempt-1}")
                return {"AI_Review": text, "length": length}, sum_usage(usages), (model or "auto")
            
            last_reason = f"长度不合规：{length}（要求小于{req.max_chars}）"
            last_code = 1003
        else:
            last_reason = "缺少 AI评价 键或内容为空"
            last_code = 1002
        
        log.warning(f"[ai_generated_evalusation] 校验失败 {attempt}/{retry_attempts}: {last_reason}")

        if attempt < retry_attempts:
            backoff = min(2 ** (attempt - 1), 4) + random.uniform(0, 0.3)
            await asyncio.sleep(backoff)

        raise HTTPException(
        status_code=422,
        detail={
            "error_code": last_code,
            "message": last_reason
        }
    )