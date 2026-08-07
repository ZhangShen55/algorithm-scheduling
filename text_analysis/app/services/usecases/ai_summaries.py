
import asyncio, random, json
from typing import Tuple, List, Optional, Dict, Any

from fastapi import HTTPException

from app.services.llm_executor import chat_raw
from app.services.prompts import load_prompt
from app.models.entities import UsageInfo, AiSummaryItemRequestObject, AiSummaryBatchRequestObject, AiSummaryItem

from app.utils import (
    strip_think_blocks,
    coerce_usage,
    sum_usage,
    llm_json_response_repair,
)

# logging 使用全局
try:
    from app.core.logging import get_logger
    import logging
    log = get_logger(__name__)
    log.setLevel(logging.DEBUG)
except Exception:
    import logging
    log = logging.getLogger(__name__)


# ========= 辅助：拼接用户输入串 =========
def _build_user_input_str(req: AiSummaryItemRequestObject) -> str:
    return f"【原始文本】{req.text}\n【任务】请对上述文本进行要点提炼，分别给出“亮点总结”和“问题总结”。"


# ========= 单次 LLM 调用并解析 JSON =========
async def _llm_once_json(
    *, messages: List[dict], model: Optional[str], temperature: float, enable_thinking: bool = False
) -> tuple[dict, Optional[UsageInfo]]:
    """
    - chat_raw：调用上游大模型
    - 返回：解析后的 JSON 与 usage
    """
    content, usage = await chat_raw(
        messages=messages,
        model=model,
        max_tokens=512,
        temperature=temperature,
        top_p=0.9,
        presence_penalty=1.0,
        extra_body={"top_k": 20, "chat_template_kwargs": {"enable_thinking": False}},
    )
    content = strip_think_blocks(content)
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = json.loads(llm_json_response_repair(content))
    return data, coerce_usage(usage)


# ========= 主流程（与模板同构） =========
async def generate_ai_summary_with_retry(
    req: AiSummaryItemRequestObject,
    *,
    retry_attempts: int = 3,
    enable_thinking: bool = False,
) -> Tuple[dict, UsageInfo, str]:
    """
    - 载入系统提示词 prompts/ai_summary.md
    - 用户输入串 + 任务指令
    - LLM 调用（指数退避）→ JSON 解析/修复
    - 结构校验：必须包含 "highlights": str, "issues": str
    - 内容校验：不允许高亮与问题同时为空（若文本确实全是正/负向信息，允许其中一个为空）
    - 返回 ({"highlights": str, "issues": str}, usage_sum, used_model)
    - 校验失败：抛出 HTTPException，detail 内含 error_code & message
    """

    # 1) 系统提示词（和之前确认的 MD 一致：只允许输出 highlights/issues 两字段）
    sys_prompt = load_prompt("ai_summary.md")

    # 2) 用户输入串与任务指令
    user_input_str = _build_user_input_str(req)
    user_task = (
        "【输出格式】只输出严格 JSON："
        '{ "highlights": "...", "issues": "..." }。\n'
        "【写作要求】亮点与问题各 1–2 句；仅基于原文抽取/轻度改写，不得编造；"
        "如确无该类信息可置空，但不允许两者同时为空；禁止输出任何其他字段或解释。"
    )

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_input_str},
        {"role": "user", "content": user_task},
    ]

    usages: List[Optional[UsageInfo]] = []
    last_reason = "unknown"
    last_code = 1004  # 兜底错误码：生成失效
    model = req.model

    for attempt in range(1, max(1, retry_attempts) + 1):
        data, u = await _llm_once_json(
            messages=messages, model=model, temperature=req.temperature or 0.4
        )
        usages.append(u)

        # 3) 结构校验
        if isinstance(data, dict) and "highlights" in data and "issues" in data \
           and isinstance(data["highlights"], str) and isinstance(data["issues"], str):
            highlights = (data["highlights"] or "").strip()
            issues = (data["issues"] or "").strip()

            # 4) 内容校验：不允许两个同时为空
            if (not highlights) and (not issues):
                last_reason = "highlights 与 issues 同时为空"
                last_code = 1002
            else:
                # 通过
                if attempt > 1:
                    log.info(f"[ai_summary] 校验通过，重试次数={attempt-1}")
                return {"highlights": highlights, "issues": issues}, sum_usage(usages), (model or "auto")

        else:
            last_reason = "缺少 highlights/issues 字段或类型不正确"
            last_code = 1001

        log.warning(f"[ai_summary] 校验失败 {attempt}/{retry_attempts}: {last_reason}")

        if attempt < retry_attempts:
            backoff = min(2 ** (attempt - 1), 4) + random.uniform(0, 0.3)
            await asyncio.sleep(backoff)

    # 5) 全部失败
    raise HTTPException(
        status_code=422,
        detail={
            "error_code": last_code,
            "message": last_reason
        }
    )

async def summarize_items_batch(
    req: AiSummaryBatchRequestObject,
    *,
    retry_attempts: int = 3,
    enable_thinking: bool = False,
) -> Tuple[List[dict], UsageInfo, str]:
    """
    - 并发处理 items；逐条容错；聚合 usage
    - 返回 ({"results":[...]} , usage_sum, used_model)
    """
    semaphore = asyncio.Semaphore(req.max_concurrency or 6)

    all_usages: List[Optional[UsageInfo]] = []
    used_model_overall: Optional[str] = None

    async def _one(it: AiSummaryItem) -> Dict[str, Any]:
        nonlocal used_model_overall

        _id = it.id
        _text = (it.text or "").strip()
        if not _text:
            return {"id": _id, "code": 2001, "message": "文本为空", "highlights": "", "issues": ""}

        single_req = AiSummaryItemRequestObject(
            id=_id,
            text=_text,
            model=req.model,
            temperature=req.temperature,
        )

        try:
            data, usage, used_model = await generate_ai_summary_with_retry(
                single_req,
                retry_attempts=retry_attempts,
                enable_thinking=enable_thinking,
            )
            if usage:
                all_usages.append(usage)
            if not used_model_overall and used_model:
                used_model_overall = used_model

            return {
                "id": _id,
                "code": 0,
                "message": "ok",
                "highlights": data.get("highlights", ""),
                "issues": data.get("issues", ""),
            }

        except HTTPException as he:
            return {
                "id": _id,
                "code": 8002,
                "message": f"结构错误: {he.detail}",
                "highlights": "",
                "issues": "",
            }
        except asyncio.TimeoutError:
            return {"id": _id, "code": 8001, "message": "上游超时", "highlights": "", "issues": ""}
        except Exception as e:
            return {
                "id": _id,
                "code": 8002,
                "message": f"上游异常/解析失败: {type(e).__name__}",
                "highlights": "",
                "issues": "",
            }

    async def _wrap(it: AiSummaryItem) -> Dict[str, Any]:
        async with asyncio.Semaphore(req.max_concurrency):
            return await _one(it)

    tasks = [asyncio.create_task(_wrap(i)) for i in req.items]
    results = await asyncio.gather(*tasks)

    usage_sum = sum_usage(all_usages)
    used_model_final = used_model_overall or (req.model or "auto")
    return {"results": results}, usage_sum, used_model_final