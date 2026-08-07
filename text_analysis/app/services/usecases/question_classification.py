import asyncio
import json
import re
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

from fastapi import HTTPException

from app.core.config import settings
from app.models.entities import QuestionClassificationRequestObject, QuestionSegment, UsageInfo
from app.services.llm_executor import chat_raw
from app.services.prompts import load_prompt
from app.utils import llm_json_response_repair, sum_usage

try:
    from app.core.logging import get_logger
    log = get_logger(__name__)
except Exception:
    import logging
    log = logging.getLogger(__name__)


FIVE_WH_CATEGORIES = ("what", "why", "how", "what_factors", "what_if")
PROMPT_FILE = "问句五何分类.md"

QuestionClassifyCall = Callable[
    [Dict[str, Any]],
    Awaitable[Tuple[Dict[str, Any], Optional[UsageInfo | Dict[str, int]]]],
]

_QUESTION_END_RE = re.compile(r"[?？]\s*$")
_SENTENCE_END_RE = re.compile(r"[。.!！？?]\s*$")
_NOISE_CHARS_RE = re.compile(r"[\s，。！？；：、,.!?;:\"'“”‘’（）()\[\]【】《》<>]")


async def analyze_question_classification(
    req: QuestionClassificationRequestObject,
    *,
    classify_call: Optional[QuestionClassifyCall] = None,
    llm_concurrency: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], UsageInfo, str]:
    if not req.segments:
        raise HTTPException(status_code=400, detail="无效请求：segments 为空")

    used_model = req.model or settings.OPENAI_MODEL
    usage_items: List[Any] = []
    bucket = _empty_teacher_bucket(
        task_id=req.task_id,
        course_id=req.course_id,
        confidence=req.confidence,
        min_len=req.min_len,
    )
    call = classify_call or _default_classify_call(used_model)
    concurrency = max(
        1,
        int(
            llm_concurrency
            if llm_concurrency is not None
            else settings.QUESTION_CLASSIFICATION_LLM_CONCURRENCY
        ),
    )
    semaphore = asyncio.Semaphore(concurrency)

    async def classify_candidate(candidate: Dict[str, Any]):
        async with semaphore:
            try:
                result, usage = await call(candidate)
                return candidate, result, usage, None
            except Exception as exc:
                return candidate, None, None, exc

    tasks = [
        asyncio.create_task(classify_candidate(candidate))
        for candidate in reconstruct_question_candidates(req.segments, min_len=req.min_len)
    ]

    for task in tasks:
        candidate, result, usage, exc = await task
        if exc is not None:
            log.warning(f"[question_classification] LLM 分类失败，已跳过候选：{exc}")
            continue
        usage_items.append(usage)

        category = _extract_valid_category(result)
        if category is None:
            continue
        bucket[category]["count"] += 1
        bucket[category]["question_info"][candidate["text"]] = candidate["time_range"]

    return [bucket], sum_usage(usage_items), used_model


def reconstruct_question_candidates(
    segments: Iterable[Any],
    *,
    min_len: int,
) -> List[Dict[str, Any]]:
    normalized = _normalize_question_segments(segments)
    if not normalized:
        return []

    candidates: List[Dict[str, Any]] = []
    seen: set[Tuple[int, int, str]] = set()
    min_chars = max(0, int(min_len or 0))

    for idx, segment in enumerate(normalized):
        if not _QUESTION_END_RE.search(segment.segment_text.strip()):
            continue

        start_idx = idx
        while start_idx > 0:
            previous = normalized[start_idx - 1]
            if _ends_sentence(previous.segment_text):
                break
            start_idx -= 1

        question_segments = normalized[start_idx : idx + 1]
        text = _join_segment_texts([item.segment_text for item in question_segments])
        if len(_semantic_text(text)) < min_chars:
            continue

        time_range = _format_time_range(question_segments[0].bg, question_segments[-1].ed)
        key = (start_idx, idx, text)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "text": text,
                "time_range": time_range,
                "start": float(question_segments[0].bg),
                "end": float(question_segments[-1].ed),
                "segments": [
                    {
                        "segment_text": item.segment_text,
                        "bg": float(item.bg),
                        "ed": float(item.ed),
                    }
                    for item in question_segments
                ],
            }
        )

    return candidates


def _empty_teacher_bucket(
    *,
    task_id: Optional[str],
    course_id: Optional[str],
    confidence: Optional[float],
    min_len: int,
) -> Dict[str, Any]:
    bucket: Dict[str, Any] = {
        "role": "teacher",
    }
    if task_id is not None:
        bucket["task_id"] = task_id
    if course_id is not None:
        bucket["course_id"] = course_id
    if confidence is not None:
        bucket["confidence"] = confidence
    bucket["min_len"] = int(min_len or 0)
    for category in FIVE_WH_CATEGORIES:
        bucket[category] = {"count": 0, "question_info": {}}
    return bucket


def _normalize_question_segments(segments: Iterable[Any]) -> List[QuestionSegment]:
    normalized: List[QuestionSegment] = []
    for item in segments or []:
        if isinstance(item, QuestionSegment):
            text = item.segment_text
            bg = item.bg
            ed = item.ed
            role = item.role
        elif isinstance(item, dict):
            text = item.get("segment_text", item.get("text", ""))
            bg = item.get("bg", 0.0)
            ed = item.get("ed", bg)
            role = item.get("role")
        else:
            text = getattr(item, "segment_text", getattr(item, "text", ""))
            bg = getattr(item, "bg", 0.0)
            ed = getattr(item, "ed", bg)
            role = getattr(item, "role", None)

        try:
            bg_value = float(bg)
            ed_value = float(ed)
        except (TypeError, ValueError):
            bg_value = ed_value = 0.0
        if ed_value < bg_value:
            bg_value, ed_value = ed_value, bg_value
        normalized.append(
            QuestionSegment(
                segment_text=str(text or "").strip(),
                bg=bg_value,
                ed=ed_value,
                role=role,
            )
        )

    return sorted(normalized, key=lambda item: (item.bg, item.ed))


def _join_segment_texts(texts: Iterable[str]) -> str:
    result = ""
    for raw in texts:
        text = str(raw or "").strip()
        if not text:
            continue
        if not result:
            result = text
            continue
        if result.endswith(("，", "、", "：", "；", ",", ":", ";")):
            result += text
        else:
            result += text
    return result


def _semantic_text(text: str) -> str:
    return _NOISE_CHARS_RE.sub("", str(text or ""))


def _ends_sentence(text: str) -> bool:
    return bool(_SENTENCE_END_RE.search(str(text or "").strip()))


def _format_time_range(start: float, end: float) -> str:
    return f"{_format_time(start)}-{_format_time(end)}"


def _format_time(value: float) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _extract_valid_category(data: Any) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    if data.get("is_valid") is not True and data.get("valid") is not True:
        return None
    category = _normalize_category(
        data.get("category")
        or data.get("type")
        or data.get("label")
        or data.get("question_type")
    )
    if category not in FIVE_WH_CATEGORIES:
        return None
    return category


def _normalize_category(value: Any) -> str:
    raw = str(value or "").strip().lower()
    aliases = {
        "what": "what",
        "是什么": "what",
        "事实类": "what",
        "why": "why",
        "为什么": "why",
        "原因类": "why",
        "how": "how",
        "如何": "how",
        "怎么": "how",
        "方法类": "how",
        "what_factors": "what_factors",
        "factors": "what_factors",
        "影响因素": "what_factors",
        "因素类": "what_factors",
        "what_if": "what_if",
        "if": "what_if",
        "假设": "what_if",
        "假设类": "what_if",
    }
    return aliases.get(raw, raw)


def _default_classify_call(model: str) -> QuestionClassifyCall:
    prompt = load_prompt(PROMPT_FILE)

    async def call(candidate: Dict[str, Any]):
        payload = {
            "question": candidate.get("text"),
            "time_range": candidate.get("time_range"),
            "segments": candidate.get("segments"),
        }
        content, usage = await chat_raw(
            system_prompt=prompt,
            user_prompt=json.dumps(payload, ensure_ascii=False),
            model=model,
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        repaired = llm_json_response_repair(content)
        return json.loads(repaired), usage

    return call
