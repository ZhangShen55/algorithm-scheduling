import json
import logging
from typing import Dict, List, Optional, Tuple
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_random_exponential, RetryCallState

from app.services.llm_executor import chat_raw
from app.utils import llm_json_response_repair


try:
    from app.core.logging import get_logger
    log = get_logger(__name__)
except Exception:
    log = logging.getLogger("app.summary")

SUMMARY_SYS = "你只回答合法 JSON"

class SummaryOut(BaseModel):
    full_overview: str
    overall_label: str

def build_summary_prompt(key_points: List[str]) -> str:
    return f"""
你是一名教学助教，能够通过几个课程要点就能对课程进行总结。
已知课程关键要点列表：
{json.dumps(key_points, ensure_ascii=False)}
任务 1:根据要点写 200 字左右的课程概要，以 “本课程” 开头，输出字段 full_overview。
任务 2:根据要点提炼 10–15 字的总标题，输出字段 overall_label。
严格返回下列 JSON（单行）：
{{"full_overview":"<200字概要>","overall_label":"<总标题>"}}
""".strip()

def build_summary_prompt_en(key_points: List[str]) -> str:
    return f"""
You are a teaching assistant who can summarize the course by identifying a few key points.
List of Key Points for Known Courses:
{json.dumps(key_points, ensure_ascii=False)}
Task 1: Write a course summary of about 200 words based on the key points, starting with 'This Course', and output the field 'full_overview'.
Task 2: Extract a 10-15 word overall title based on the key points and output the field 'overall_1abel'.
Strictly return the following JSON (single line):
{{"full_overview":"<200 word summary>","overall_label":"<General Title>"}}
""".strip()

def _validate_summary(out: SummaryOut) -> None:
    """
    不满足即抛异常，触发重试：
      1) JSON 校验失败（由 Pydantic 抛 ValidationError）
      2) full_overview 含 “概要” 或 不含 “本课程”
      3) overall_label 含 “总标题”
    """
    fo = (out.full_overview or "").strip()
    ol = (out.overall_label or "").strip()

    if "概要" in fo:
        raise AssertionError("full_overview 出现禁用词 ‘概要’")
    # 按你的要求：不出现“本课程”就重试（如要改为必须开头，可用 fo.startswith("本课程")）
    if "本课程" not in fo and "This Course" not in fo:
        raise AssertionError("full_overview 未包含 ‘本课程’")
    if "总标题" in ol:
        raise AssertionError("overall_label 出现禁用词 ‘总标题’")

def _before_sleep_log(rs: RetryCallState) -> None:
    exc = rs.outcome.exception() if rs.outcome else None
    log.warning(f"[summary] 重试第 {rs.attempt_number} 次，原因：{exc}")

@retry(
    retry=retry_if_exception_type((json.JSONDecodeError, ValidationError, AssertionError)),
    stop=stop_after_attempt(3),                            
    wait=wait_random_exponential(min=1, max=4),
    reraise=True,
    before_sleep=_before_sleep_log,
)
async def call_summary_ex(
    *,
    key_points: List[str],
    model: Optional[str] = None,
) -> Tuple[Dict[str, str], Dict[str, int]]:
    prompt = build_summary_prompt(key_points)

    content, usage = await chat_raw(
        user_prompt=prompt,
        system_prompt=SUMMARY_SYS,
        model=model,
        max_tokens=512,
        temperature=0.7,
        top_p=0.8,
        presence_penalty=1.5,
        response_format={"type": "json_object"},
        extra_body={"top_k": 20, "chat_template_kwargs": {"enable_thinking": False}},
    )

    # 1) 修复 + 解析
    fixed = llm_json_response_repair(content or "")
    try:
        data = json.loads(fixed)
    except json.JSONDecodeError as e:
        # 让 tenacity 捕获并重试
        raise e

    # 2) 结构校验（Pydantic）
    try:
        out = SummaryOut(**data)
    except ValidationError as e:
        raise e

    # 3) 业务规则校验（禁用词 & 必含词）
    _validate_summary(out)

    # 通过
    return out.model_dump(), usage
