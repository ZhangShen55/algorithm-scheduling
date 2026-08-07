import asyncio
import json
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

from fastapi import HTTPException

from app.core.config import settings
from app.models.entities import StudentInteractionAnalysisRequestObject, TextSegment, UsageInfo
from app.services.llm_executor import chat_raw
from app.services.prompts import load_prompt
from app.services.usecases.language_expression_analysis import (
    clean_segment_text,
    filter_effective_segments,
    locate_evidence_time_range,
    normalize_text_segments,
    split_segments_into_chunks,
)
from app.utils import llm_json_response_repair, sum_usage

try:
    from app.core.logging import get_logger
    log = get_logger(__name__)
except Exception:
    import logging
    log = logging.getLogger(__name__)


INTERACTION_TYPES = {"t_s", "s_s"}
RECALL_PROMPT_FILE = "学生互动粗召回.md"
VERIFY_PROMPT_FILE = "学生互动二次复核.md"

RecallCall = Callable[[Dict[str, Any]], Awaitable[Tuple[Dict[str, Any], Optional[UsageInfo | Dict[str, int]]]]]
VerifyCall = Callable[[Dict[str, Any]], Awaitable[Tuple[Dict[str, Any], Optional[UsageInfo | Dict[str, int]]]]]


async def analyze_student_interactions(
    req: StudentInteractionAnalysisRequestObject,
    *,
    recall_call: Optional[RecallCall] = None,
    verify_call: Optional[VerifyCall] = None,
    chunk_chars: Optional[int] = None,
    chunk_overlap_chars: Optional[int] = None,
    max_chunks: Optional[int] = None,
    chunk_concurrency: Optional[int] = None,
    chunk_retry_attempts: Optional[int] = None,
    verify_context_seconds: Optional[int] = None,
    verify_retry_attempts: Optional[int] = None,
    merge_gap_seconds: Optional[int] = None,
    max_candidates_per_chunk: Optional[int] = None,
) -> Tuple[Dict[str, Any], UsageInfo, str]:
    if not req.textSegments:
        raise HTTPException(status_code=400, detail="无效请求：文本为空")
    if req.course_start >= req.course_end:
        raise HTTPException(status_code=400, detail="course_start 必须小于 course_end")

    used_model = req.model or settings.OPENAI_MODEL
    usage_items: List[Any] = []

    try:
        effective_segments, _filtering = filter_effective_segments(
            req.textSegments,
            course_start=req.course_start,
            course_end=req.course_end,
            breaks=req.breaks or [],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    chunks = split_segments_into_chunks(
        effective_segments,
        chunk_chars=chunk_chars or settings.STUDENT_INTERACTION_ANALYSIS_CHUNK_CHARS,
        overlap_chars=(
            chunk_overlap_chars
            if chunk_overlap_chars is not None
            else settings.STUDENT_INTERACTION_ANALYSIS_CHUNK_OVERLAP_CHARS
        ),
        max_chunks=max_chunks or settings.STUDENT_INTERACTION_ANALYSIS_MAX_CHUNKS,
    )
    if not chunks:
        return {"interactions": []}, UsageInfo(), used_model

    recall_retry = chunk_retry_attempts or settings.STUDENT_INTERACTION_ANALYSIS_CHUNK_RETRY_ATTEMPTS
    verify_retry = verify_retry_attempts or settings.STUDENT_INTERACTION_ANALYSIS_VERIFY_RETRY_ATTEMPTS
    verify_seconds = verify_context_seconds or settings.STUDENT_INTERACTION_ANALYSIS_VERIFY_CONTEXT_SECONDS
    candidate_limit = max(1, int(max_candidates_per_chunk or settings.STUDENT_INTERACTION_ANALYSIS_MAX_CANDIDATES_PER_CHUNK))
    sem = asyncio.Semaphore(max(1, int(chunk_concurrency or settings.STUDENT_INTERACTION_ANALYSIS_CHUNK_CONCURRENCY)))
    verified: List[Dict[str, Any]] = []

    async def run_recall(chunk: Dict[str, Any]):
        async with sem:
            return await _call_recall_with_retry(
                chunk,
                call=recall_call or _default_recall_call(used_model),
                retry_attempts=recall_retry,
            )

    tasks = [asyncio.create_task(run_recall(chunk)) for chunk in chunks]
    for chunk, task in zip(chunks, tasks):
        try:
            recall_result, usage = await task
            usage_items.append(usage)
        except Exception as exc:
            log.warning(f"[student_interaction_analysis] chunk={chunk['chunk_id']} 粗召回失败：{exc}")
            continue

        raw_candidates = recall_result.get("interactions") if isinstance(recall_result, dict) else []
        if not isinstance(raw_candidates, list):
            raw_candidates = []
        for raw in raw_candidates[:candidate_limit]:
            candidate = normalize_candidate(raw, chunk["segments"])
            if candidate is None:
                continue
            context_segments = build_verification_context(
                req.textSegments,
                candidate_range=candidate["time_range"],
                course_start=req.course_start,
                course_end=req.course_end,
                breaks=req.breaks or [],
                context_seconds=verify_seconds,
            )
            if not context_segments:
                continue
            payload = {
                "candidate": candidate,
                "text": _segments_to_text(context_segments),
                "segments": context_segments,
                "time_range": {"start": context_segments[0].bg, "end": context_segments[-1].ed},
            }
            try:
                verify_result, usage = await _call_verify_with_retry(
                    payload,
                    call=verify_call or _default_verify_call(used_model),
                    retry_attempts=verify_retry,
                )
                usage_items.append(usage)
            except Exception as exc:
                log.warning(f"[student_interaction_analysis] 候选复核失败：{exc}")
                continue
            item = normalize_verified_interaction(verify_result, candidate, context_segments)
            if item is not None:
                verified.append(item)

    result = merge_interactions(
        _filter_interactions_in_effective_range(verified, req.course_start, req.course_end, req.breaks or []),
        merge_gap_seconds=merge_gap_seconds or settings.STUDENT_INTERACTION_ANALYSIS_MERGE_GAP_SECONDS,
    )
    return {"interactions": result}, sum_usage(usage_items), used_model


def normalize_candidate(item: Any, segments: Iterable[Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    interaction_type = str(item.get("type") or "").strip()
    if interaction_type not in INTERACTION_TYPES:
        return None
    evidence = str(item.get("evidence") or "").strip()
    summary = str(item.get("summary") or "").strip()
    if not evidence or not summary:
        return None
    time_range = locate_evidence_time_range(evidence, segments)
    if time_range is None or time_range.get("start") >= time_range.get("end"):
        return None
    return {
        "type": interaction_type,
        "time_range": time_range,
        "summary": summary,
        "evidence": evidence,
    }


def build_verification_context(
    text_segments: Iterable[Any],
    *,
    candidate_range: Dict[str, float],
    course_start: float,
    course_end: float,
    breaks: Iterable[Any],
    context_seconds: int,
) -> List[TextSegment]:
    try:
        candidate_start = float(candidate_range["start"])
        candidate_end = float(candidate_range["end"])
    except (KeyError, TypeError, ValueError):
        return []
    lower = max(float(course_start), candidate_start - max(0, int(context_seconds or 0)))
    upper = min(float(course_end), candidate_end + max(0, int(context_seconds or 0)))
    if lower >= upper:
        return []
    filtered, _ = filter_effective_segments(
        text_segments,
        course_start=lower,
        course_end=upper,
        breaks=breaks or [],
    )
    return [seg for seg in filtered if seg.bg >= lower and seg.ed <= upper]


def normalize_verified_interaction(
    data: Any,
    fallback: Dict[str, Any],
    context_segments: Iterable[Any],
) -> Optional[Dict[str, Any]]:
    if not isinstance(data, dict) or data.get("is_interaction") is not True:
        return None
    interaction_type = str(data.get("type") or fallback.get("type") or "").strip()
    if interaction_type not in INTERACTION_TYPES:
        return None
    summary = str(data.get("summary") or fallback.get("summary") or "").strip()
    evidence = str(data.get("evidence") or fallback.get("evidence") or "").strip()
    if not summary or not evidence:
        return None
    time_range = locate_evidence_time_range(evidence, context_segments)
    if time_range is None or time_range.get("start") >= time_range.get("end"):
        return None
    return {
        "type": interaction_type,
        "time_range": time_range,
        "summary": summary,
        "evidence": evidence,
    }


def merge_interactions(interactions: Iterable[Any], *, merge_gap_seconds: int) -> List[Dict[str, Any]]:
    items = [_sanitize_interaction(item) for item in interactions or []]
    items = [item for item in items if item is not None]
    items.sort(key=lambda item: (item["time_range"]["start"], item["time_range"]["end"]))
    merged: List[Dict[str, Any]] = []
    gap = max(0, int(merge_gap_seconds or 0))

    for item in items:
        if not merged:
            merged.append(item)
            continue
        previous = merged[-1]
        if (
            previous["type"] == item["type"]
            and item["time_range"]["start"] <= previous["time_range"]["end"] + gap
        ):
            previous["time_range"] = {
                "start": round(min(previous["time_range"]["start"], item["time_range"]["start"]), 2),
                "end": round(max(previous["time_range"]["end"], item["time_range"]["end"]), 2),
            }
            previous["summary"] = _merge_text(previous["summary"], item["summary"], max_chars=120)
        else:
            merged.append(item)
    return merged


async def _call_recall_with_retry(
    chunk: Dict[str, Any],
    *,
    call: RecallCall,
    retry_attempts: int,
) -> Tuple[Dict[str, Any], Optional[UsageInfo | Dict[str, int]]]:
    return await _call_with_retry(chunk, call=call, retry_attempts=retry_attempts, expected_key="interactions")


async def _call_verify_with_retry(
    payload: Dict[str, Any],
    *,
    call: VerifyCall,
    retry_attempts: int,
) -> Tuple[Dict[str, Any], Optional[UsageInfo | Dict[str, int]]]:
    return await _call_with_retry(payload, call=call, retry_attempts=retry_attempts, expected_key="is_interaction")


async def _call_with_retry(
    payload: Dict[str, Any],
    *,
    call: Callable[[Dict[str, Any]], Awaitable[Tuple[Dict[str, Any], Optional[UsageInfo | Dict[str, int]]]]],
    retry_attempts: int,
    expected_key: str,
) -> Tuple[Dict[str, Any], Optional[UsageInfo | Dict[str, int]]]:
    last_exc: Optional[Exception] = None
    for attempt in range(1, max(1, int(retry_attempts or 1)) + 1):
        try:
            result, usage = await call(payload)
            if not isinstance(result, dict) or expected_key not in result:
                raise ValueError(f"LLM结果缺少字段：{expected_key}")
            return result, usage
        except Exception as exc:
            last_exc = exc
            if attempt < max(1, int(retry_attempts or 1)):
                await asyncio.sleep(min(2 ** (attempt - 1), 4))
    raise last_exc or RuntimeError("LLM调用失败")


def _default_recall_call(model: str) -> RecallCall:
    prompt = load_prompt(RECALL_PROMPT_FILE)

    async def call(chunk: Dict[str, Any]):
        payload = {
            "chunk_id": chunk.get("chunk_id"),
            "time_range": chunk.get("time_range"),
            "text": chunk.get("text"),
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


def _default_verify_call(model: str) -> VerifyCall:
    prompt = load_prompt(VERIFY_PROMPT_FILE)

    async def call(payload: Dict[str, Any]):
        llm_payload = {
            "candidate": {
                "type": payload.get("candidate", {}).get("type"),
                "summary": payload.get("candidate", {}).get("summary"),
                "evidence": payload.get("candidate", {}).get("evidence"),
                "time_range": payload.get("candidate", {}).get("time_range"),
            },
            "context_time_range": payload.get("time_range"),
            "text": payload.get("text"),
        }
        content, usage = await chat_raw(
            system_prompt=prompt,
            user_prompt=json.dumps(llm_payload, ensure_ascii=False),
            model=model,
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        repaired = llm_json_response_repair(content)
        return json.loads(repaired), usage

    return call


def _filter_interactions_in_effective_range(
    interactions: Iterable[Dict[str, Any]],
    course_start: float,
    course_end: float,
    breaks: Iterable[Any],
) -> List[Dict[str, Any]]:
    break_ranges = [_coerce_break(item) for item in breaks or []]
    break_ranges = [item for item in break_ranges if item is not None and item["start"] < item["end"]]
    result: List[Dict[str, Any]] = []
    for item in interactions or []:
        clean = _sanitize_interaction(item)
        if clean is None:
            continue
        start = clean["time_range"]["start"]
        end = clean["time_range"]["end"]
        if start < float(course_start) or end > float(course_end):
            continue
        if any(_overlaps(start, end, br["start"], br["end"]) for br in break_ranges):
            continue
        result.append(clean)
    return result


def _sanitize_interaction(item: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    interaction_type = str(item.get("type") or "").strip()
    if interaction_type not in INTERACTION_TYPES:
        return None
    time_range = item.get("time_range")
    if not isinstance(time_range, dict):
        return None
    try:
        start = round(float(time_range["start"]), 2)
        end = round(float(time_range["end"]), 2)
    except (KeyError, TypeError, ValueError):
        return None
    summary = str(item.get("summary") or "").strip()
    evidence = str(item.get("evidence") or "").strip()
    if start >= end or not summary or not evidence:
        return None
    return {
        "type": interaction_type,
        "time_range": {"start": start, "end": end},
        "summary": summary,
        "evidence": evidence,
    }


def _segments_to_text(segments: Iterable[Any]) -> str:
    return "".join(clean_segment_text(seg.text) for seg in normalize_text_segments(segments))


def _merge_text(left: str, right: str, *, max_chars: int) -> str:
    left = str(left or "").strip()
    right = str(right or "").strip()
    if not left:
        return right[:max_chars]
    if not right or right in left:
        return left[:max_chars]
    if left in right:
        return right[:max_chars]
    return f"{left}{right}"[:max_chars]


def _coerce_break(item: Any) -> Optional[Dict[str, float]]:
    if isinstance(item, dict):
        start = item.get("start")
        end = item.get("end")
    else:
        start = getattr(item, "start", None)
        end = getattr(item, "end", None)
    try:
        return {"start": float(start), "end": float(end)}
    except (TypeError, ValueError):
        return None


def _overlaps(left_start: float, left_end: float, right_start: float, right_end: float) -> bool:
    return left_start < right_end and left_end > right_start
