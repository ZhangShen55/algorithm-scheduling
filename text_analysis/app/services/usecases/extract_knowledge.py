import json, asyncio, random
from typing import Dict, Any, List, Optional, Tuple

from fastapi import HTTPException
from app.services.prompts import load_prompt
from app.services.llm_executor import chat_raw
from app.models.entities import UsageInfo, ExtractKeywordsCoverRequestObject

# utils
from app.utils import (
    strip_think_blocks,
    llm_json_response_repair,
    coerce_usage,
    sum_usage,
    shuffle_knowledge_modules,
)

# logging
try:
    from app.core.logging import get_logger
    log = get_logger(__name__)
except Exception:
    import logging
    log = logging.getLogger(__name__)

def _calc_tf_stats(knowledge: Dict[str, Dict[str, bool]]) -> tuple[int, int]:
    """统计所有模块里 True/False 的总数"""
    t = f = 0
    for module, points in knowledge.items():
        for _, v in points.items():
            if v is True:
                t += 1
            elif v is False:
                f += 1
    return t, f

def _validate_extraction_result(data: Dict[str, Any]) -> Tuple[bool, str]:
    """校验目标结构：{course_name: str, knowledge: {模块: {知识点: bool}}}"""
    if not isinstance(data, dict):
        return False, "顶层不是对象"
    if "course_name" not in data or not isinstance(data["course_name"], str) or not data["course_name"].strip():
        return False, "缺少或非法的 course_name"
    if "knowledge" not in data or not isinstance(data["knowledge"], dict):
        return False, "缺少或非法的 knowledge"
    knowledge = data["knowledge"]
    if not knowledge:
        return False, "knowledge 为空"
    for module_title, points in knowledge.items():
        if not isinstance(module_title, str) or not module_title.strip():
            return False, "存在空的模块标题"
        if not isinstance(points, dict) or not points:
            return False, f"模块 `{module_title}` 的值不是非空对象"
        for k, v in points.items():
            if not isinstance(k, str) or not k.strip():
                return False, f"模块 `{module_title}` 中存在空的知识点名"
            if not isinstance(v, bool):
                return False, f"模块 `{module_title}` 的知识点 `{k}` 取值不是布尔"
 
    # 合理性校验
    true_cnt, false_cnt = _calc_tf_stats(knowledge)

    # 1) 全True 或 全False 则不合理
    if true_cnt == 0 or false_cnt == 0:
        return False, "所有知识点标注全为 True 或全为 False（可疑结果）"

    # 2) 所有模块 True 的总数 < 3，则不合理
    if true_cnt < 3:
        return False, "所有模块中 True 的数量小于 3（可疑结果）"   

    return True, "ok"


def _build_messages(req: ExtractKeywordsCoverRequestObject) -> List[dict]:
    """如果没传 messages，就用文件 prompt 组装一套；否则把 ChatMessage 转原生 dict。"""
    if req.messages is not None:
        return [m.model_dump() for m in req.messages]

    sys_prompt = load_prompt("知识点提取覆盖.md")
    user_payload = {
        "course_name": req.course_name,
        "text": req.text,
        "require_format": {
            "course_name": "课程名称（string）",
            "knowledge": {"模块标题": {"知识点": True}}
        }
    }
    return [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]


async def _llm_once(
    *, messages: List[dict], model: Optional[str], temperature: float
) -> Tuple[Dict[str, Any], Optional[UsageInfo]]:
    """单次调用：清理 <think> → 解析 JSON（失败则 repair 一次）"""
    content, usage = await chat_raw(
        messages=messages,
        model=model,
        max_tokens=1024,
        temperature=temperature,
        top_p=0.9,
        presence_penalty=1.2,
        response_format={"type": "json_object"},
        extra_body={"top_k": 20, "chat_template_kwargs": {"enable_thinking": False}},
    )
    content = strip_think_blocks(content)
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = json.loads(llm_json_response_repair(content))
    return data, coerce_usage(usage)


async def extract_knowledge_with_retry(
    req: ExtractKeywordsCoverRequestObject,
    *,
    model: Optional[str] = "qwen3-8b",
    retry_attempts: int = 3,
    shuffle: bool = False,
    seed: Optional[int] = None,
    shuffle_modules: bool = False,
) -> Tuple[Dict[str, Any], UsageInfo]:
    """
    用例编排：
    - 组装 messages（或使用传入的）
    - LLM 调用并校验，不通过则指数退避重试
    - 可选打乱模块内“知识点”顺序（和/或模块顺序）
    - 返回 (data, usage_sum)
    """
    messages = _build_messages(req)
    usages: List[Optional[UsageInfo]] = []
    last_reason = "unknown"

    for attempt in range(1, max(1, retry_attempts) + 1):
        data, u = await _llm_once(messages=messages, model=model, temperature=req.temperature or 0.6)
        usages.append(u)

        ok, reason = _validate_extraction_result(data)
        if ok:
            if shuffle:
                data = shuffle_knowledge_modules(
                    data,
                    seed=seed,
                    shuffle_modules=shuffle_modules,
                    shuffle_points=True,
                )
            if attempt > 1:
                log.info(f"[extract_knowledge] 校验通过，重试次数={attempt-1}")
            return data, sum_usage(usages)

        last_reason = reason or "invalid structure"
        log.warning(f"[extract_knowledge] 校验不通过，尝试 {attempt}/{retry_attempts}：{last_reason}")

        if attempt < retry_attempts:
            backoff = min(2 ** (attempt - 1), 4) + random.uniform(0, 0.3)
            await asyncio.sleep(backoff)

    raise HTTPException(status_code=400, detail=f"模型返回不符合知识结构要求：{last_reason}")
