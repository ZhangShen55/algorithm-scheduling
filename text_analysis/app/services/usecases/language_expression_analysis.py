import asyncio
import json
import math
import re
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

from fastapi import HTTPException

from app.core.config import settings
from app.models.entities import LanguageExpressionAnalysisRequestObject, TextSegment, UsageInfo
from app.services.llm_executor import chat_raw
from app.services.prompts import load_prompt
from app.services.usecases.course_time_analysis import analyze_course_time
from app.utils import coerce_usage, llm_json_response_repair, sum_usage

try:
    from app.core.logging import get_logger
    log = get_logger(__name__)
except Exception:
    import logging
    log = logging.getLogger(__name__)


DIMENSION_LABELS = {
    "expression_coherence": "语言表达连贯性",
    "expression_ability": "语言表达能力",
    "contextual_understanding": "语境理解",
    "semantic_accuracy": "语义准确性",
}
DIMENSIONS = list(DIMENSION_LABELS.keys())
DIMENSION_ALIASES = {key: key for key in DIMENSION_LABELS}
DIMENSION_ALIASES.update({label: key for key, label in DIMENSION_LABELS.items()})
CHUNK_PROMPT_FILE = "语言表达分析.md"
FINAL_PROMPT_FILE = "语言表达分析汇总.md"

ChunkCall = Callable[[Dict[str, Any]], Awaitable[Tuple[Dict[str, Any], Optional[UsageInfo | Dict[str, int]]]]]
FinalPolishCall = Callable[[Dict[str, Any]], Awaitable[Tuple[Dict[str, Any], Optional[UsageInfo | Dict[str, int]]]]]
AutoTimeAnalyzer = Callable[[Iterable[Any]], Awaitable[Tuple[Dict[str, Any], Optional[UsageInfo | Dict[str, int]]]]]


def normalize_text_segments(text_segments: Iterable[Any]) -> List[TextSegment]:
    normalized: List[TextSegment] = []
    for item in text_segments or []:
        if isinstance(item, TextSegment):
            text = item.text
            bg = item.bg
            ed = item.ed
        elif isinstance(item, dict):
            text = item.get("text", "")
            bg = item.get("bg", 0.0)
            ed = item.get("ed", bg)
        else:
            text = getattr(item, "text", "")
            bg = getattr(item, "bg", 0.0)
            ed = getattr(item, "ed", bg)
        try:
            bg_value = float(bg)
            ed_value = float(ed)
        except (TypeError, ValueError):
            bg_value = ed_value = 0.0
        if ed_value < bg_value:
            bg_value, ed_value = ed_value, bg_value
        normalized.append(TextSegment(text=str(text or ""), bg=bg_value, ed=ed_value))
    return normalized


def filter_effective_segments(
    text_segments: Iterable[Any],
    *,
    course_start: Optional[float],
    course_end: Optional[float],
    breaks: Iterable[Any],
) -> Tuple[List[TextSegment], Dict[str, Any]]:
    segments = normalize_text_segments(text_segments)
    if not segments:
        return [], {
            "course_start": course_start,
            "course_end": course_end,
            "breaks": [],
            "input_segments": 0,
            "used_segments": 0,
            "removed_segments": 0,
            "removed_break_segments": 0,
            "effective_chars": 0,
        }

    start = float(course_start if course_start is not None else segments[0].bg)
    end = float(course_end if course_end is not None else segments[-1].ed)
    if start >= end:
        raise ValueError("course_start 必须小于 course_end")

    break_ranges = [_coerce_time_range(item) for item in (breaks or [])]
    break_ranges = [item for item in break_ranges if item is not None and item["start"] < item["end"]]

    filtered: List[TextSegment] = []
    removed_break_segments = 0
    for seg in segments:
        if seg.ed < start or seg.bg > end:
            continue
        if any(_overlaps(seg.bg, seg.ed, item["start"], item["end"]) for item in break_ranges):
            removed_break_segments += 1
            continue
        filtered.append(seg)

    return filtered, {
        "course_start": round(start, 2),
        "course_end": round(end, 2),
        "breaks": break_ranges,
        "input_segments": len(segments),
        "used_segments": len(filtered),
        "removed_segments": len(segments) - len(filtered),
        "removed_break_segments": removed_break_segments,
        "effective_chars": sum(len(seg.text or "") for seg in filtered),
    }


def split_segments_into_chunks(
    text_segments: Iterable[Any],
    *,
    chunk_chars: int,
    overlap_chars: int,
    max_chunks: int,
) -> List[Dict[str, Any]]:
    segments = [seg for seg in normalize_text_segments(text_segments) if clean_segment_text(seg.text)]
    if not segments:
        return []

    chunk_chars = max(1, int(chunk_chars or 1))
    overlap_chars = max(0, int(overlap_chars or 0))
    max_chunks = max(1, int(max_chunks or 1))
    total_chars = sum(len(clean_segment_text(seg.text)) for seg in segments)
    target_chars = max(chunk_chars, math.ceil(total_chars / max_chunks))
    chunks = _pack_segments(segments, target_chars)
    if len(chunks) > max_chunks:
        target_chars = max(chunk_chars, math.ceil(total_chars / max_chunks))
        chunks = _pack_segments(segments, target_chars)

    result: List[Dict[str, Any]] = []
    previous_text = ""
    for idx, chunk_segments in enumerate(chunks[:max_chunks], start=1):
        chunk_text = "".join(clean_segment_text(seg.text) for seg in chunk_segments)
        if idx > 1 and overlap_chars > 0:
            chunk_text = previous_text[-overlap_chars:] + chunk_text
        previous_text = "".join(clean_segment_text(seg.text) for seg in chunk_segments)
        result.append(
            {
                "chunk_id": idx,
                "text": chunk_text,
                "segments": chunk_segments,
                "char_count": len(chunk_text),
                "time_range": {"start": chunk_segments[0].bg, "end": chunk_segments[-1].ed},
            }
        )
    return result


def clean_segment_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def locate_evidence_time_range(evidence: str, text_segments: Iterable[Any]) -> Optional[Dict[str, float]]:
    evidence = clean_segment_text(evidence)
    if not evidence:
        return None

    segments = normalize_text_segments(text_segments)
    combined = ""
    char_segment_indexes: List[int] = []
    for idx, seg in enumerate(segments):
        text = clean_segment_text(seg.text)
        combined += text
        char_segment_indexes.extend([idx] * len(text))

    match = _find_match_span(combined, evidence)
    if match is None:
        normalized_combined, normalized_map = _normalize_with_mapping(combined)
        normalized_evidence, _ = _normalize_with_mapping(evidence)
        pos = normalized_combined.find(normalized_evidence)
        if pos < 0:
            return None
        start_char = normalized_map[pos]
        end_char = normalized_map[pos + len(normalized_evidence) - 1] + 1
    else:
        start_char, end_char = match

    if start_char >= len(char_segment_indexes) or end_char <= 0:
        return None
    start_seg = segments[char_segment_indexes[start_char]]
    end_seg = segments[char_segment_indexes[min(end_char - 1, len(char_segment_indexes) - 1)]]
    return {"start": round(start_seg.bg, 2), "end": round(end_seg.ed, 2)}


def aggregate_language_results(
    chunk_results: List[Dict[str, Any]],
    *,
    overall_score_min: int,
    overall_score_max: int,
    max_items_per_dimension: int,
    min_advantages_per_dimension: int,
    min_problems_per_dimension: int,
) -> Dict[str, Any]:
    dimensions: Dict[str, Any] = {}
    scored_values: List[float] = []

    for dimension in DIMENSIONS:
        weighted_sum = 0.0
        weight_sum = 0.0
        advantages: List[Dict[str, Any]] = []
        problems: List[Dict[str, Any]] = []
        for result in chunk_results:
            dim = _dimension_payload(result.get("dimensions") or {}, dimension)
            if not isinstance(dim, dict):
                continue
            weight = max(1, int(result.get("char_count") or 1))
            if isinstance(dim.get("score"), (int, float)):
                weighted_sum += _clamp(float(dim["score"]), 0, 100) * weight
                weight_sum += weight
            advantages.extend(_valid_items(dim.get("advantages") or []))
            problems.extend(_valid_items(dim.get("problems") or []))

        if weight_sum > 0:
            score = int(round(weighted_sum / weight_sum))
            scored_values.append(score)
            confidence = "high" if advantages or problems else "medium"
        else:
            score = 60
            confidence = "low"

        advantages = _limit_items(_dedupe_items(advantages), max_items_per_dimension)
        problems = _limit_items(_dedupe_items(problems), max_items_per_dimension)
        dimensions[dimension] = {
            "score": score,
            "confidence": confidence,
            "advantages": advantages,
            "problems": problems,
        }

    if scored_values:
        raw_overall = int(round(sum(scored_values) / len(scored_values)))
    else:
        raw_overall = 60
    lower = min(overall_score_min, overall_score_max)
    upper = max(overall_score_min, overall_score_max)
    return {
        "overall_score": int(_clamp(raw_overall, lower, upper)),
        "dimensions": dimensions,
    }


async def analyze_language_expression(
    req: LanguageExpressionAnalysisRequestObject,
    *,
    chunk_call: Optional[ChunkCall] = None,
    final_polish_call: Optional[FinalPolishCall] = None,
    auto_time_analyzer: Optional[AutoTimeAnalyzer] = None,
    enable_final_polish: Optional[bool] = None,
) -> Tuple[Dict[str, Any], UsageInfo, str]:
    if not req.textSegments:
        raise HTTPException(status_code=400, detail="无效请求：文本为空")

    used_model = req.model or settings.OPENAI_MODEL
    temperature = (
        req.temperature
        if req.temperature is not None
        else settings.LANGUAGE_EXPRESSION_ANALYSIS_DEFAULT_TEMPERATURE
    )
    usage_items: List[Any] = []
    time_filter_fallback_used = False

    course_start = req.course_start
    course_end = req.course_end
    breaks = req.breaks or []
    if (course_start is None or course_end is None) and settings.LANGUAGE_EXPRESSION_ANALYSIS_ENABLE_AUTO_COURSE_TIME_ANALYSIS:
        try:
            analyzer = auto_time_analyzer or _default_auto_time_analyzer(used_model)
            time_result, time_usage = await analyzer(req.textSegments)
            usage_items.append(time_usage)
            course_start = _nested_time(time_result.get("course_start"))
            course_end = _nested_time(time_result.get("course_end"))
            breaks = time_result.get("breaks") or []
        except Exception as exc:
            time_filter_fallback_used = True
            log.warning(f"[language_expression_analysis] 课程时间自动分析失败，退化为全文：{exc}")

    if course_start is None or course_end is None:
        normalized = normalize_text_segments(req.textSegments)
        course_start = normalized[0].bg
        course_end = normalized[-1].ed
        breaks = []
        time_filter_fallback_used = True

    try:
        effective_segments, filtering = filter_effective_segments(
            req.textSegments,
            course_start=course_start,
            course_end=course_end,
            breaks=breaks,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    chunks = split_segments_into_chunks(
        effective_segments,
        chunk_chars=settings.LANGUAGE_EXPRESSION_ANALYSIS_CHUNK_CHARS,
        overlap_chars=settings.LANGUAGE_EXPRESSION_ANALYSIS_CHUNK_OVERLAP_CHARS,
        max_chunks=settings.LANGUAGE_EXPRESSION_ANALYSIS_MAX_CHUNKS,
    )

    sem = asyncio.Semaphore(max(1, settings.LANGUAGE_EXPRESSION_ANALYSIS_CHUNK_CONCURRENCY))
    chunk_results: List[Dict[str, Any]] = []
    failed_chunks = 0

    async def run_chunk(chunk: Dict[str, Any]):
        async with sem:
            return await _call_chunk_with_retry(
                chunk,
                call=chunk_call or _default_chunk_call(used_model, temperature),
                retry_attempts=settings.LANGUAGE_EXPRESSION_ANALYSIS_CHUNK_RETRY_ATTEMPTS,
            )

    tasks = [asyncio.create_task(run_chunk(chunk)) for chunk in chunks]
    for chunk, task in zip(chunks, tasks):
        try:
            result, usage = await task
            normalized_result = _normalize_chunk_result(result, chunk)
            if normalized_result is None:
                failed_chunks += 1
                continue
            chunk_results.append(normalized_result)
            usage_items.append(usage)
        except Exception as exc:
            failed_chunks += 1
            log.warning(f"[language_expression_analysis] chunk={chunk['chunk_id']} 处理失败：{exc}")

    aggregate = aggregate_language_results(
        chunk_results,
        overall_score_min=settings.LANGUAGE_EXPRESSION_ANALYSIS_OVERALL_SCORE_MIN,
        overall_score_max=settings.LANGUAGE_EXPRESSION_ANALYSIS_OVERALL_SCORE_MAX,
        max_items_per_dimension=settings.LANGUAGE_EXPRESSION_ANALYSIS_MAX_ITEMS_PER_DIMENSION,
        min_advantages_per_dimension=settings.LANGUAGE_EXPRESSION_ANALYSIS_MIN_ADVANTAGES_PER_DIMENSION,
        min_problems_per_dimension=settings.LANGUAGE_EXPRESSION_ANALYSIS_MIN_PROBLEMS_PER_DIMENSION,
    )

    final_polish_fallback_used = False
    should_polish = settings.LANGUAGE_EXPRESSION_ANALYSIS_ENABLE_FINAL_LLM_POLISH if enable_final_polish is None else enable_final_polish
    if should_polish:
        try:
            polished, usage = await _call_final_polish_with_retry(
                aggregate,
                call=final_polish_call or _default_final_polish_call(used_model, temperature),
                retry_attempts=settings.LANGUAGE_EXPRESSION_ANALYSIS_FINAL_RETRY_ATTEMPTS,
            )
            sanitized = _sanitize_final_result(
                polished,
                aggregate,
                effective_segments,
                overall_score_min=settings.LANGUAGE_EXPRESSION_ANALYSIS_OVERALL_SCORE_MIN,
                overall_score_max=settings.LANGUAGE_EXPRESSION_ANALYSIS_OVERALL_SCORE_MAX,
                max_items_per_dimension=settings.LANGUAGE_EXPRESSION_ANALYSIS_MAX_ITEMS_PER_DIMENSION,
                min_advantages_per_dimension=settings.LANGUAGE_EXPRESSION_ANALYSIS_MIN_ADVANTAGES_PER_DIMENSION,
                min_problems_per_dimension=settings.LANGUAGE_EXPRESSION_ANALYSIS_MIN_PROBLEMS_PER_DIMENSION,
            )
            if sanitized is not None:
                aggregate, sanitized_fallback_used = sanitized
                final_polish_fallback_used = sanitized_fallback_used
                usage_items.append(usage)
            else:
                final_polish_fallback_used = True
        except Exception as exc:
            final_polish_fallback_used = True
            log.warning(f"[language_expression_analysis] 最终润色失败，使用聚合结果：{exc}")

    aggregate.pop("warnings", None)
    aggregate["filtering"] = filtering
    aggregate["execution"] = {
        "model": used_model,
        "temperature": temperature,
        "chunk_count": len(chunks),
        "succeeded_chunks": len(chunk_results),
        "failed_chunks": failed_chunks,
        "fallback_used": len(chunk_results) == 0 or final_polish_fallback_used or time_filter_fallback_used,
        "time_filter_fallback_used": time_filter_fallback_used,
        "final_polish_fallback_used": final_polish_fallback_used,
    }
    return aggregate, sum_usage(usage_items), used_model


def _pack_segments(segments: List[TextSegment], target_chars: int) -> List[List[TextSegment]]:
    chunks: List[List[TextSegment]] = []
    current: List[TextSegment] = []
    current_chars = 0
    for seg in segments:
        text_len = len(clean_segment_text(seg.text))
        if current and current_chars + text_len > target_chars:
            chunks.append(current)
            current = [seg]
            current_chars = text_len
        else:
            current.append(seg)
            current_chars += text_len
    if current:
        chunks.append(current)
    return chunks


def _coerce_time_range(item: Any) -> Optional[Dict[str, float]]:
    if item is None:
        return None
    if isinstance(item, dict):
        start = item.get("start")
        end = item.get("end")
    else:
        start = getattr(item, "start", None)
        end = getattr(item, "end", None)
    try:
        return {"start": round(float(start), 2), "end": round(float(end), 2)}
    except (TypeError, ValueError):
        return None


def _overlaps(left_start: float, left_end: float, right_start: float, right_end: float) -> bool:
    return left_start < right_end and left_end > right_start


def _find_match_span(combined: str, evidence: str) -> Optional[Tuple[int, int]]:
    pos = combined.find(evidence)
    if pos >= 0:
        return pos, pos + len(evidence)
    return None


def _normalize_with_mapping(text: str) -> Tuple[str, List[int]]:
    normalized = ""
    mapping: List[int] = []
    for idx, ch in enumerate(text):
        if ch.isspace() or ch in "，。！？；：、,.!?;:":
            continue
        normalized += ch
        mapping.append(idx)
    return normalized, mapping


def _valid_items(items: Iterable[Any]) -> List[Dict[str, Any]]:
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if _is_grounded_item(item):
            result.append(item)
    return result


def _is_grounded_item(item: Dict[str, Any]) -> bool:
    summary = str(item.get("summary") or "").strip()
    detail = str(item.get("detail") or "").strip()
    evidence = str(item.get("evidence") or "").strip()
    related_content = item.get("related_content")
    time_range = item.get("time_range")
    if not summary or not detail or not evidence or not isinstance(time_range, dict):
        return False
    if len(detail) < 10:
        return False
    if not isinstance(related_content, list) or not related_content:
        return False
    if summary in {"表达清晰", "逻辑较好", "语言流畅", "表达一般"}:
        return False
    return True


def _dedupe_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for item in items:
        key = (item.get("summary"), item.get("evidence"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _limit_items(items: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    return items[: max(0, int(limit or 0))]


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _nested_time(value: Any) -> Optional[float]:
    if isinstance(value, dict):
        value = value.get("time")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dimension_label(dimension: str) -> str:
    return DIMENSION_LABELS.get(dimension, dimension)


def _dimension_payload(dimensions: Any, dimension: str) -> Dict[str, Any]:
    if not isinstance(dimensions, dict):
        return {}
    if isinstance(dimensions.get(dimension), dict):
        return dimensions[dimension]
    label = _dimension_label(dimension)
    if isinstance(dimensions.get(label), dict):
        return dimensions[label]
    return {}


def _normalize_chunk_result(data: Dict[str, Any], chunk: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(data, dict):
        return None
    dimensions = data.get("dimensions")
    if not isinstance(dimensions, dict):
        return None

    normalized_dimensions: Dict[str, Any] = {}
    any_item = False
    for dimension in DIMENSIONS:
        raw_dim = _dimension_payload(dimensions, dimension)
        normalized_dim = {
            "score": _coerce_score(raw_dim.get("score"), 60),
            "advantages": [],
            "problems": [],
        }
        for key in ("advantages", "problems"):
            for item in raw_dim.get(key) or []:
                fixed = _normalize_grounded_item(item, chunk["segments"])
                if fixed is not None:
                    normalized_dim[key].append(fixed)
                    any_item = True
        normalized_dimensions[dimension] = normalized_dim

    if not any_item and not any(isinstance(_dimension_payload(dimensions, d).get("score"), (int, float)) for d in DIMENSIONS):
        return None
    return {
        "chunk_id": chunk["chunk_id"],
        "char_count": chunk["char_count"],
        "main_topics": data.get("main_topics") or [],
        "dimensions": normalized_dimensions,
    }


def _normalize_grounded_item(item: Any, segments: Iterable[Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    evidence = str(item.get("evidence") or "").strip()
    time_range = locate_evidence_time_range(evidence, segments)
    if time_range is None:
        return None
    fixed = {
        "summary": str(item.get("summary") or "").strip(),
        "detail": str(item.get("detail") or "").strip(),
        "evidence": evidence,
        "time_range": time_range,
        "related_content": item.get("related_content") if isinstance(item.get("related_content"), list) else [],
    }
    return fixed if _is_grounded_item(fixed) else None


def _coerce_score(value: Any, default: int) -> int:
    try:
        return int(round(_clamp(float(value), 0, 100)))
    except (TypeError, ValueError):
        return default


def _sanitize_final_result(
    data: Dict[str, Any],
    fallback: Dict[str, Any],
    segments: Iterable[Any],
    *,
    overall_score_min: int,
    overall_score_max: int,
    max_items_per_dimension: int,
    min_advantages_per_dimension: int,
    min_problems_per_dimension: int,
) -> Optional[Tuple[Dict[str, Any], bool]]:
    if not _has_all_dimensions(data):
        return None

    fallback_dimensions = fallback.get("dimensions") or {}
    lower = min(overall_score_min, overall_score_max)
    upper = max(overall_score_min, overall_score_max)
    sanitized: Dict[str, Any] = {
        "overall_score": int(_clamp(_coerce_score(data.get("overall_score"), fallback.get("overall_score", 60)), lower, upper)),
        "dimensions": {},
    }
    fallback_used = False

    for dimension in DIMENSIONS:
        raw_dim = _dimension_payload(data["dimensions"], dimension)
        fallback_dim = _dimension_payload(fallback_dimensions, dimension)
        confidence = raw_dim.get("confidence")
        if confidence not in {"high", "medium", "low"}:
            confidence = fallback_dim.get("confidence") if fallback_dim.get("confidence") in {"high", "medium", "low"} else "low"
            fallback_used = True

        sanitized_dim = {
            "score": _coerce_score(raw_dim.get("score"), fallback_dim.get("score", 60)),
            "confidence": confidence,
            "advantages": _sanitize_items(
                raw_dim.get("advantages") or [],
                fallback_dim.get("advantages") or [],
                segments,
                max_items_per_dimension,
                min_advantages_per_dimension,
                _dimension_label(dimension),
                "advantage",
            ),
            "problems": _sanitize_items(
                raw_dim.get("problems") or [],
                fallback_dim.get("problems") or [],
                segments,
                max_items_per_dimension,
                min_problems_per_dimension,
                _dimension_label(dimension),
                "problem",
            ),
        }
        if len(sanitized_dim["advantages"]) < len(raw_dim.get("advantages") or []) or len(sanitized_dim["problems"]) < len(raw_dim.get("problems") or []):
            fallback_used = True
        sanitized["dimensions"][dimension] = sanitized_dim

    sanitized.pop("warnings", None)
    return sanitized, fallback_used


def _sanitize_items(
    polished_items: Iterable[Any],
    fallback_items: Iterable[Any],
    segments: Iterable[Any],
    limit: int,
    minimum: int,
    dimension: str,
    item_type: str,
) -> List[Dict[str, Any]]:
    grounded: List[Dict[str, Any]] = []
    for item in polished_items:
        fixed = _normalize_grounded_item(item, segments)
        if fixed is not None:
            grounded.append(fixed)

    for item in fallback_items:
        if not isinstance(item, dict):
            continue
        if str(item.get("evidence") or "").strip():
            fixed = _normalize_grounded_item(item, segments)
            if fixed is not None:
                grounded.append(fixed)

    return _limit_items(_dedupe_items(grounded), limit)


async def _call_chunk_with_retry(
    chunk: Dict[str, Any],
    *,
    call: ChunkCall,
    retry_attempts: int,
) -> Tuple[Dict[str, Any], Optional[UsageInfo | Dict[str, int]]]:
    last_exc: Optional[Exception] = None
    for attempt in range(1, max(1, retry_attempts) + 1):
        try:
            return await call(chunk)
        except Exception as exc:
            last_exc = exc
            if attempt < retry_attempts:
                await asyncio.sleep(min(2 ** (attempt - 1), 4))
    raise last_exc or RuntimeError("chunk analysis failed")


async def _call_final_polish_with_retry(
    aggregate: Dict[str, Any],
    *,
    call: FinalPolishCall,
    retry_attempts: int,
) -> Tuple[Dict[str, Any], Optional[UsageInfo | Dict[str, int]]]:
    last_exc: Optional[Exception] = None
    for attempt in range(1, max(1, retry_attempts) + 1):
        try:
            return await call(aggregate)
        except Exception as exc:
            last_exc = exc
            if attempt < retry_attempts:
                await asyncio.sleep(min(2 ** (attempt - 1), 4))
    raise last_exc or RuntimeError("final polish failed")


def _default_chunk_call(model: str, temperature: float) -> ChunkCall:
    async def call(chunk: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[UsageInfo]]:
        system_prompt = load_prompt(CHUNK_PROMPT_FILE)
        user_prompt = json.dumps(
            {
                "chunk_id": chunk["chunk_id"],
                "time_range": chunk["time_range"],
                "text": chunk["text"],
                "dimensions": [{"key": key, "label": label} for key, label in DIMENSION_LABELS.items()],
            },
            ensure_ascii=False,
        )
        content, usage = await chat_raw(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            model=model,
            max_tokens=2048,
            temperature=temperature,
            top_p=0.8,
            presence_penalty=1.0,
            response_format={"type": "json_object"},
            extra_body={"top_k": 20, "chat_template_kwargs": {"enable_thinking": False}},
        )
        return _parse_json_content(content), coerce_usage(usage)

    return call


def _default_final_polish_call(model: str, temperature: float) -> FinalPolishCall:
    async def call(aggregate: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[UsageInfo]]:
        system_prompt = load_prompt(FINAL_PROMPT_FILE)
        content, usage = await chat_raw(
            user_prompt=json.dumps(aggregate, ensure_ascii=False),
            system_prompt=system_prompt,
            model=model,
            max_tokens=2048,
            temperature=temperature,
            top_p=0.8,
            presence_penalty=1.0,
            response_format={"type": "json_object"},
            extra_body={"top_k": 20, "chat_template_kwargs": {"enable_thinking": False}},
        )
        return _parse_json_content(content), coerce_usage(usage)

    return call


def _default_auto_time_analyzer(model: str) -> AutoTimeAnalyzer:
    async def call(text_segments: Iterable[Any]) -> Tuple[Dict[str, Any], Optional[UsageInfo]]:
        return await analyze_course_time(
            text_segments,
            model=model,
            enable_llm_validation=settings.COURSE_TIME_ANALYSIS_ENABLE_LLM_VALIDATION,
            llm_concurrency=settings.COURSE_TIME_ANALYSIS_LLM_CONCURRENCY,
            llm_retry_attempts=settings.COURSE_TIME_ANALYSIS_LLM_RETRY_ATTEMPTS,
            max_llm_candidates=settings.COURSE_TIME_ANALYSIS_MAX_LLM_CANDIDATES,
            min_break_duration_sec=settings.COURSE_TIME_ANALYSIS_MIN_BREAK_DURATION_SEC,
            max_break_duration_sec=settings.COURSE_TIME_ANALYSIS_MAX_BREAK_DURATION_SEC,
            course_start_candidate_budget=settings.COURSE_TIME_ANALYSIS_COURSE_START_CANDIDATE_BUDGET,
            course_end_candidate_budget=settings.COURSE_TIME_ANALYSIS_COURSE_END_CANDIDATE_BUDGET,
            break_start_candidate_budget=settings.COURSE_TIME_ANALYSIS_BREAK_START_CANDIDATE_BUDGET,
            break_end_candidate_budget=settings.COURSE_TIME_ANALYSIS_BREAK_END_CANDIDATE_BUDGET,
            weak_candidate_budget=settings.COURSE_TIME_ANALYSIS_WEAK_CANDIDATE_BUDGET,
            candidate_context_before_sec=settings.COURSE_TIME_ANALYSIS_CANDIDATE_CONTEXT_BEFORE_SEC,
            candidate_context_after_sec=settings.COURSE_TIME_ANALYSIS_CANDIDATE_CONTEXT_AFTER_SEC,
            fallback_window_sec=settings.COURSE_TIME_ANALYSIS_FALLBACK_WINDOW_SEC,
            max_fallback_windows=settings.COURSE_TIME_ANALYSIS_MAX_FALLBACK_WINDOWS,
        )

    return call


def _parse_json_content(content: str) -> Dict[str, Any]:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return json.loads(llm_json_response_repair(content))


def _has_all_dimensions(data: Dict[str, Any]) -> bool:
    if not isinstance(data, dict) or not isinstance(data.get("dimensions"), dict):
        return False
    return all(_dimension_payload(data["dimensions"], dimension) for dimension in DIMENSIONS)
