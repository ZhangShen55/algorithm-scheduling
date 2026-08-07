import asyncio
import json
import re
from dataclasses import dataclass, replace
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.models.entities import UsageInfo
from app.services.llm_executor import chat_raw
from app.services.prompts import load_prompt
from app.utils import coerce_usage, llm_json_response_repair, sum_usage

try:
    from app.core.logging import get_logger
    log = get_logger(__name__)
except Exception:
    import logging
    log = logging.getLogger(__name__)


BOUNDARY_PROMPT_FILE = "课程时间边界判定.md"
BOUNDARY_TYPES = {"course_start", "course_end", "break_start", "break_end", "scan_window"}


@dataclass
class BoundaryCandidate:
    event_type: str
    time: float
    segment_time: str
    evidence: str
    score: float
    index: int
    source: str
    matched_pattern: str
    window_start: float
    window_end: float
    confidence: float = 0.0
    reason: str = ""
    keyword_strength: str = "strong"
    position_prior_score: float = 0.0
    stage: str = "candidate"
    state_before: str = ""
    state_after: str = ""


@dataclass
class _Segment:
    text: str
    bg: float
    ed: float
    index: int


_PATTERNS: List[Tuple[str, re.Pattern, float, str, str]] = [
    ("course_start", re.compile(r"各位同学(上午|下午|晚上)?好"), 9.0, "start", "strong"),
    ("course_start", re.compile(r"各位.{0,8}开始上课"), 9.0, "start", "strong"),
    ("course_start", re.compile(r"下面.{0,10}开始.{0,12}(学习|课程|上课)"), 8.0, "start", "strong"),
    ("course_start", re.compile(r"今天.{0,12}(一起)?学习.{0,10}(题目|内容)"), 7.5, "start", "strong"),
    ("course_start", re.compile(r"很高兴.{0,24}(授课|讲课)"), 6.5, "start", "strong"),
    ("course_start", re.compile(r"根据.{0,10}课程安排"), 6.0, "start", "strong"),
    ("course_start", re.compile(r"欢迎.{0,20}(老师|教授)"), 4.0, "start", "strong"),
    (
        "break_start",
        re.compile(r"(咱们|我们|大家)?(先)?休息(一会|一下|会儿|十分钟|十五分钟|[0-9一二三四五六七八九十]+分钟)"),
        10.0,
        "end",
        "strong",
    ),
    (
        "break_start",
        re.compile(r"(现在|先|咱们|我们|大家|那么).{0,12}课间休息|课间休息.{0,12}(现在|休息|分钟|一会|一下)"),
        10.0,
        "end",
        "strong",
    ),
    ("break_start", re.compile(r"歇一会|暂停一下|下课休息|下节课"), 10.0, "end", "strong"),
    ("break_start", re.compile(r"上了.{0,8}(分钟|小时)"), 5.0, "end", "weak"),
    ("break_end", re.compile(r"接着继续来上课|继续来上课"), 12.0, "start", "strong"),
    ("break_end", re.compile(r"继续上课|接着上课|准备上课|开始上课|上课了"), 10.0, "start", "strong"),
    ("break_end", re.compile(r"(我们|咱们|大家).{0,8}(继续|接着).{0,10}(讲|学习|看)"), 9.0, "start", "strong"),
    ("break_end", re.compile(r"言归正传|刚刚课间"), 8.0, "start", "strong"),
    ("break_end", re.compile(r"那么接下来.{0,10}(讲|学习)|接下来.{0,10}给大家讲"), 6.0, "start", "strong"),
    ("break_end", re.compile(r"上课|继续|接着|回来|开始|讲|看|刚才|下面"), 2.0, "start", "weak"),
    ("course_end", re.compile(r"今天.{0,8}课.{0,8}(上到|到).{0,12}(这|这里|这一块)"), 10.0, "end", "strong"),
    ("course_end", re.compile(r"(本节课|这节课|课程).{0,8}(结束|上到这里|就这样|到这里)"), 9.0, "end", "strong"),
    ("course_end", re.compile(r"下课|谢谢大家"), 8.0, "end", "strong"),
]


def _value(seg: Any, key: str, default: Any = None) -> Any:
    if isinstance(seg, dict):
        return seg.get(key, default)
    return getattr(seg, key, default)


def _segments(text_segments: Iterable[Any]) -> List[_Segment]:
    items: List[_Segment] = []
    for idx, seg in enumerate(text_segments or []):
        text = str(_value(seg, "text", "") or "")
        try:
            bg = float(_value(seg, "bg", 0.0))
            ed = float(_value(seg, "ed", bg))
        except (TypeError, ValueError):
            bg = ed = 0.0
        items.append(_Segment(text=text, bg=bg, ed=ed, index=idx))
    return items


def _fmt_time(bg: float, ed: float) -> str:
    return f"{round(bg, 2)}-{round(ed, 2)}"


def _join_text(segs: List[_Segment], start: int, end: int) -> str:
    return "".join(s.text for s in segs[max(0, start):min(len(segs), end)])


def _context_text(segs: List[_Segment], idx: int, radius: int = 1) -> str:
    return _join_text(segs, idx - radius, idx + radius + 1).strip()


def _context_bounds(segs: List[_Segment], idx: int, before_sec: int, after_sec: int) -> Tuple[float, float]:
    seg = segs[idx]
    return max(segs[0].bg, seg.bg - before_sec), min(segs[-1].ed, seg.ed + after_sec)


def _break_start_time(segs: List[_Segment], idx: int) -> float:
    end = segs[idx].ed
    for nxt in segs[idx + 1:idx + 3]:
        if nxt.bg - end > 5:
            break
        if re.search(r"好不好|好吗|行不行|可以吧", nxt.text):
            end = nxt.ed
            break
    return end


def _score_candidate(event_type: str, score: float, evidence: str) -> float:
    if event_type == "course_start":
        if "各位同学" in evidence and ("开始" in evidence or "学习" in evidence):
            score += 2.0
        if "欢迎" in evidence and ("老师" in evidence or "教授" in evidence):
            score -= 1.0
    if event_type == "break_start" and "休息" in evidence and ("分钟" in evidence or "上了" in evidence):
        score += 1.0
    if event_type == "break_start" and ("下节课" in evidence or "课间休息" in evidence):
        score += 1.0
    if event_type == "break_end" and ("继续上课" in evidence or "继续来上课" in evidence):
        score += 1.0
    if event_type == "course_end" and ("今天的课" in evidence or "上到" in evidence):
        score += 1.0
    return score


def _position_prior(event_type: str, segs: List[_Segment], time_value: float) -> float:
    if not segs:
        return 0.0
    start = segs[0].bg
    end = segs[-1].ed
    if event_type == "course_start":
        elapsed = max(0.0, time_value - start)
        if elapsed <= 300:
            return 2.0
        if elapsed <= 900:
            return 1.4
        if elapsed <= 1800:
            return 0.7
        return 0.2
    if event_type == "course_end":
        remaining = max(0.0, end - time_value)
        if remaining <= 300:
            return 2.0
        if remaining <= 900:
            return 1.4
        if remaining <= 1800:
            return 0.7
        return 0.2
    return 0.5


def _make_candidate(
    segs: List[_Segment],
    idx: int,
    event_type: str,
    pattern: re.Pattern,
    score: float,
    anchor: str,
    keyword_strength: str,
    *,
    context_before_sec: int,
    context_after_sec: int,
) -> BoundaryCandidate:
    seg = segs[idx]
    evidence = _context_text(segs, idx, radius=1)
    if event_type == "break_start":
        event_time = _break_start_time(segs, idx)
    elif anchor == "end":
        event_time = seg.ed
    else:
        event_time = seg.bg
    window_start, window_end = _context_bounds(segs, idx, context_before_sec, context_after_sec)
    position_prior = _position_prior(event_type, segs, event_time)
    final_score = _score_candidate(event_type, score, evidence) + position_prior
    return BoundaryCandidate(
        event_type=event_type,
        time=round(event_time, 2),
        segment_time=_fmt_time(seg.bg, seg.ed),
        evidence=evidence,
        score=final_score,
        index=idx,
        source="hard_match",
        matched_pattern=pattern.pattern,
        window_start=round(window_start, 2),
        window_end=round(window_end, 2),
        confidence=min(0.99, final_score / 12.0),
        keyword_strength=keyword_strength,
        position_prior_score=round(position_prior, 2),
        stage="keyword",
    )


def _fallback_candidate(
    segs: List[_Segment],
    *,
    event_type: str,
    time_value: float,
    segment_time: str,
    evidence: str,
    index: int,
    window_start: float,
    window_end: float,
    reason: str,
) -> BoundaryCandidate:
    position_prior = _position_prior(event_type, segs, time_value)
    return BoundaryCandidate(
        event_type=event_type,
        time=round(time_value, 2),
        segment_time=segment_time,
        evidence=evidence,
        score=1.0,
        index=index,
        source="fallback",
        matched_pattern="",
        window_start=round(window_start, 2),
        window_end=round(window_end, 2),
        confidence=0.2,
        reason=reason,
        keyword_strength="fallback",
        position_prior_score=round(position_prior, 2),
        stage="fallback",
    )


def _add_fallback_scan_windows(
    candidates: List[BoundaryCandidate],
    segs: List[_Segment],
    *,
    fallback_window_sec: int,
    max_fallback_windows: int,
) -> None:
    if not segs or max_fallback_windows <= 0:
        return
    start, end = segs[0].bg, segs[-1].ed
    duration = max(1.0, end - start)
    count = min(max_fallback_windows, max(1, int(duration // max(1, fallback_window_sec)) + 1))
    step = duration / count
    for n in range(count):
        ws = start + n * step
        we = min(end, ws + fallback_window_sec)
        near_idx = min(range(len(segs)), key=lambda i: abs(segs[i].bg - ws))
        evidence = _window_lines(segs, ws, we, max_chars=300).replace("\n", "")
        candidates.append(
            _fallback_candidate(
                segs,
                event_type="scan_window",
                time_value=segs[near_idx].bg,
                segment_time=_fmt_time(ws, we),
                evidence=evidence,
                index=near_idx,
                window_start=ws,
                window_end=we,
                reason="hard_match_missing_for_break_or_boundary",
            )
        )


def collect_boundary_candidates(
    text_segments: Iterable[Any],
    *,
    candidate_context_before_sec: int = 120,
    candidate_context_after_sec: int = 120,
    fallback_window_sec: int = 300,
    max_fallback_windows: int = 12,
) -> List[BoundaryCandidate]:
    segs = _segments(text_segments)
    if not segs:
        return []

    candidates: List[BoundaryCandidate] = []
    for idx, seg in enumerate(segs):
        for event_type, pattern, score, anchor, keyword_strength in _PATTERNS:
            if pattern.search(seg.text):
                candidates.append(
                    _make_candidate(
                        segs,
                        idx,
                        event_type,
                        pattern,
                        score,
                        anchor,
                        keyword_strength,
                        context_before_sec=candidate_context_before_sec,
                        context_after_sec=candidate_context_after_sec,
                    )
                )

    hard_types = {c.event_type for c in candidates if c.source == "hard_match"}
    if "course_start" not in hard_types:
        first = segs[0]
        candidates.append(
            _fallback_candidate(
                segs,
                event_type="course_start",
                time_value=first.bg,
                segment_time=_fmt_time(first.bg, first.ed),
                evidence=first.text,
                index=0,
                window_start=first.bg,
                window_end=min(segs[-1].ed, first.bg + fallback_window_sec),
                reason="no_hard_course_start_candidate",
            )
        )
    if "course_end" not in hard_types:
        last = segs[-1]
        candidates.append(
            _fallback_candidate(
                segs,
                event_type="course_end",
                time_value=last.ed,
                segment_time=_fmt_time(last.bg, last.ed),
                evidence=last.text,
                index=len(segs) - 1,
                window_start=max(segs[0].bg, last.ed - fallback_window_sec),
                window_end=last.ed,
                reason="no_hard_course_end_candidate",
            )
        )
    if "break_start" not in hard_types or "break_end" not in hard_types:
        _add_fallback_scan_windows(
            candidates,
            segs,
            fallback_window_sec=fallback_window_sec,
            max_fallback_windows=max_fallback_windows,
        )

    return _dedupe_candidates(candidates)


def _dedupe_candidates(candidates: List[BoundaryCandidate]) -> List[BoundaryCandidate]:
    best: Dict[Tuple[str, int], BoundaryCandidate] = {}
    for c in candidates:
        bucket = int(c.time // 5)
        key = (c.event_type, bucket)
        old = best.get(key)
        if old is None or c.score > old.score:
            best[key] = c
    return sorted(best.values(), key=lambda c: (c.time, -c.score))


def _best_candidate(candidates: List[BoundaryCandidate], event_type: str) -> Optional[BoundaryCandidate]:
    items = [c for c in candidates if c.event_type == event_type]
    if not items:
        return None
    return sorted(items, key=lambda c: (-c.score, c.time))[0]


def _best_course_end(candidates: List[BoundaryCandidate]) -> Optional[BoundaryCandidate]:
    items = [c for c in candidates if c.event_type == "course_end"]
    if not items:
        return None
    return sorted(items, key=lambda c: (-c.position_prior_score, -c.score, -c.time))[0]


def _ensure_tail_course_end(
    candidates: List[BoundaryCandidate],
    segs: List[_Segment],
    *,
    fallback_window_sec: int,
) -> None:
    if not segs:
        return
    current = _best_course_end(candidates)
    tail = segs[-1]
    if current and current.position_prior_score >= 0.7:
        return
    candidates.append(
        _fallback_candidate(
            segs,
            event_type="course_end",
            time_value=tail.ed,
            segment_time=_fmt_time(tail.bg, tail.ed),
            evidence=tail.text,
            index=tail.index,
            window_start=max(segs[0].bg, tail.ed - fallback_window_sec),
            window_end=tail.ed,
            reason="tail_fallback_no_reliable_course_end",
        )
    )


def _ensure_head_course_start(
    candidates: List[BoundaryCandidate],
    segs: List[_Segment],
    *,
    fallback_window_sec: int,
) -> None:
    if not segs or any(c.event_type == "course_start" for c in candidates):
        return
    first = segs[0]
    candidates.append(
        _fallback_candidate(
            segs,
            event_type="course_start",
            time_value=first.bg,
            segment_time=_fmt_time(first.bg, first.ed),
            evidence=first.text,
            index=first.index,
            window_start=first.bg,
            window_end=min(segs[-1].ed, first.bg + fallback_window_sec),
            reason="head_fallback_no_reliable_course_start",
        )
    )


def _break_end_rank(start: BoundaryCandidate, end: BoundaryCandidate) -> Tuple[int, float, float]:
    evidence = end.evidence
    strong_phrase = int(
        any(
            phrase in evidence
            for phrase in (
                "继续上课",
                "接着上课",
                "准备上课",
                "接着继续来上课",
                "开始上课",
                "刚刚课间",
                "言归正传",
                "我们继续",
                "咱们继续",
            )
        )
    )
    return (strong_phrase, end.score, -abs((end.time - start.time) - 1200))


def _pair_breaks(
    candidates: List[BoundaryCandidate],
    *,
    course_start: Optional[BoundaryCandidate],
    course_end: Optional[BoundaryCandidate],
    min_break_duration_sec: int,
    max_break_duration_sec: int,
) -> List[Dict[str, Any]]:
    starts = sorted([c for c in candidates if c.event_type == "break_start"], key=lambda c: c.time)
    ends = sorted([c for c in candidates if c.event_type == "break_end"], key=lambda c: c.time)
    paired: List[Dict[str, Any]] = []
    used_end_idx: set[int] = set()
    lower = course_start.time if course_start else float("-inf")
    upper = course_end.time if course_end else float("inf")

    for start in starts:
        if not (lower < start.time < upper):
            continue
        matches: List[Tuple[int, BoundaryCandidate]] = []
        for idx, end in enumerate(ends):
            if idx in used_end_idx:
                continue
            if end.time <= start.time or end.time >= upper:
                continue
            duration = end.time - start.time
            if duration < min_break_duration_sec or duration > max_break_duration_sec:
                continue
            matches.append((idx, end))
        if not matches:
            continue
        match_idx, match = sorted(matches, key=lambda item: _break_end_rank(start, item[1]), reverse=True)[0]
        used_end_idx.add(match_idx)
        confidence = round(min(start.confidence, match.confidence), 2)
        paired.append(
            {
                "start": start.time,
                "end": match.time,
                "duration_sec": round(match.time - start.time, 2),
                "confidence": confidence,
                "start_segment_time": start.segment_time,
                "end_segment_time": match.segment_time,
                "start_evidence": start.evidence,
                "end_evidence": match.evidence,
                "source": _join_sources(start.source, match.source),
            }
        )
    return paired


def _join_sources(left: str, right: str) -> str:
    return left if left == right else f"{left},{right}"


def _boundary_to_dict(candidate: Optional[BoundaryCandidate]) -> Optional[Dict[str, Any]]:
    if candidate is None:
        return None
    return {
        "time": candidate.time,
        "segment_time": candidate.segment_time,
        "confidence": round(candidate.confidence, 2),
        "evidence": candidate.evidence,
        "source": candidate.source,
        "reason": candidate.reason,
    }


def analyze_course_time_by_rules(
    text_segments: Iterable[Any],
    *,
    min_break_duration_sec: int = 120,
    max_break_duration_sec: int = 2400,
    candidate_context_before_sec: int = 120,
    candidate_context_after_sec: int = 120,
    fallback_window_sec: int = 300,
    max_fallback_windows: int = 12,
    candidates: Optional[List[BoundaryCandidate]] = None,
) -> Dict[str, Any]:
    segs = _segments(text_segments)
    if not segs:
        return {
            "course_start": None,
            "course_end": None,
            "breaks": [],
        }

    candidate_list = candidates or collect_boundary_candidates(
        segs,
        candidate_context_before_sec=candidate_context_before_sec,
        candidate_context_after_sec=candidate_context_after_sec,
        fallback_window_sec=fallback_window_sec,
        max_fallback_windows=max_fallback_windows,
    )
    _ensure_head_course_start(candidate_list, segs, fallback_window_sec=fallback_window_sec)
    _ensure_tail_course_end(candidate_list, segs, fallback_window_sec=fallback_window_sec)

    course_start = _best_candidate(candidate_list, "course_start")
    course_end = _best_course_end(candidate_list)
    breaks = _pair_breaks(
        candidate_list,
        course_start=course_start,
        course_end=course_end,
        min_break_duration_sec=min_break_duration_sec,
        max_break_duration_sec=max_break_duration_sec,
    )

    return {
        "course_start": _boundary_to_dict(course_start),
        "course_end": _boundary_to_dict(course_end),
        "breaks": breaks,
    }


def _window_lines(segs: List[_Segment], start: float, end: float, *, max_chars: int = 1600) -> str:
    lines: List[str] = []
    char_count = 0
    for seg in segs:
        if seg.ed < start:
            continue
        if seg.bg > end:
            break
        line = f"{round(seg.bg, 2)}-{round(seg.ed, 2)}:{seg.text}"
        lines.append(line)
        char_count += len(line)
        if char_count >= max_chars:
            break
    return "\n".join(lines)


def _build_llm_prompt(candidate: BoundaryCandidate, context: str) -> str:
    payload = {
        "candidate": {
            "event_type": candidate.event_type,
            "time": candidate.time,
            "segment_time": candidate.segment_time,
            "evidence": candidate.evidence,
            "source": candidate.source,
            "keyword_strength": candidate.keyword_strength,
            "position_prior_score": candidate.position_prior_score,
        },
        "context": context,
        "allowed_event_types": ["course_start", "course_end", "break_start", "break_end", "none"],
    }
    return json.dumps(payload, ensure_ascii=False)


def _parse_llm_candidate_result(content: str) -> Dict[str, Any]:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return json.loads(llm_json_response_repair(content))


def _refine_candidate(candidate: BoundaryCandidate, data: Dict[str, Any]) -> Optional[BoundaryCandidate]:
    if not isinstance(data, dict) or not data.get("is_boundary"):
        if candidate.source == "fallback":
            return None
        return replace(candidate, score=candidate.score * 0.7, confidence=round(candidate.confidence * 0.8, 2))
    event_type = str(data.get("event_type") or candidate.event_type)
    if event_type not in BOUNDARY_TYPES or event_type == "none":
        return None
    try:
        confidence = float(data.get("confidence", candidate.confidence))
    except (TypeError, ValueError):
        confidence = candidate.confidence
    confidence = min(0.99, max(0.0, confidence))
    time_value = candidate.time
    try:
        proposed_time = float(data.get("time", candidate.time))
        if candidate.window_start <= proposed_time <= candidate.window_end:
            time_value = proposed_time
    except (TypeError, ValueError):
        pass
    evidence = str(data.get("evidence") or candidate.evidence).strip()
    return replace(
        candidate,
        event_type=event_type,
        time=round(time_value, 2),
        evidence=evidence,
        score=max(candidate.score, confidence * 12.0),
        confidence=confidence,
        source=f"{candidate.source}+llm",
        reason=str(data.get("reason") or candidate.reason or "").strip(),
        state_before=str(data.get("state_before") or candidate.state_before or "").strip(),
        state_after=str(data.get("state_after") or candidate.state_after or "").strip(),
    )


async def _validate_candidate_with_llm(
    candidate: BoundaryCandidate,
    segs: List[_Segment],
    *,
    model: Optional[str],
    retry_attempts: int,
) -> Tuple[Optional[BoundaryCandidate], Optional[UsageInfo]]:
    system_prompt = load_prompt(BOUNDARY_PROMPT_FILE)
    context = _window_lines(segs, candidate.window_start, candidate.window_end)
    user_prompt = _build_llm_prompt(candidate, context)
    last_usage: Optional[UsageInfo] = None
    for attempt in range(1, max(1, retry_attempts) + 1):
        try:
            content, usage = await chat_raw(
                user_prompt=user_prompt,
                system_prompt=system_prompt,
                model=model,
                max_tokens=512,
                temperature=0.1,
                top_p=0.8,
                presence_penalty=1.0,
                response_format={"type": "json_object"},
                extra_body={"top_k": 10, "chat_template_kwargs": {"enable_thinking": False}},
            )
            last_usage = coerce_usage(usage)
            data = _parse_llm_candidate_result(content)
            return _refine_candidate(candidate, data), last_usage
        except Exception as exc:
            log.warning(
                f"[course_time_analysis] LLM候选校验失败 attempt={attempt}/{retry_attempts} "
                f"type={candidate.event_type} time={candidate.time} reason={exc}"
            )
            if attempt < retry_attempts:
                await asyncio.sleep(min(2 ** (attempt - 1), 4))
    return candidate if candidate.source != "fallback" else None, last_usage


async def refine_candidates_with_llm(
    text_segments: Iterable[Any],
    candidates: List[BoundaryCandidate],
    *,
    model: Optional[str],
    concurrency: int,
    retry_attempts: int,
    max_llm_candidates: int,
    course_start_candidate_budget: int = 8,
    course_end_candidate_budget: int = 8,
    break_start_candidate_budget: int = 12,
    break_end_candidate_budget: int = 12,
    weak_candidate_budget: int = 8,
) -> Tuple[List[BoundaryCandidate], UsageInfo]:
    segs = _segments(text_segments)
    if not segs or not candidates:
        return candidates, UsageInfo()

    selected = _select_llm_candidates(
        candidates,
        max_llm_candidates=max_llm_candidates,
        course_start_candidate_budget=course_start_candidate_budget,
        course_end_candidate_budget=course_end_candidate_budget,
        break_start_candidate_budget=break_start_candidate_budget,
        break_end_candidate_budget=break_end_candidate_budget,
        weak_candidate_budget=weak_candidate_budget,
    )
    untouched = [c for c in candidates if c not in selected and c.source != "fallback"]
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def run(candidate: BoundaryCandidate):
        async with semaphore:
            return await _validate_candidate_with_llm(candidate, segs, model=model, retry_attempts=retry_attempts)

    results = await asyncio.gather(*(run(c) for c in selected))
    refined: List[BoundaryCandidate] = []
    usages: List[Optional[UsageInfo]] = []
    for candidate, usage in results:
        if candidate is not None:
            refined.append(candidate)
        if usage:
            usages.append(usage)
    refined.extend(untouched)
    return _dedupe_candidates(refined), sum_usage(usages)


def _select_llm_candidates(
    candidates: List[BoundaryCandidate],
    *,
    max_llm_candidates: int,
    course_start_candidate_budget: int,
    course_end_candidate_budget: int,
    break_start_candidate_budget: int,
    break_end_candidate_budget: int,
    weak_candidate_budget: int,
) -> List[BoundaryCandidate]:
    if not candidates:
        return []

    def ranked(items: List[BoundaryCandidate]) -> List[BoundaryCandidate]:
        return sorted(items, key=lambda c: (-c.score, -c.position_prior_score, c.time))

    budget_by_type = {
        "course_start": max(0, course_start_candidate_budget),
        "course_end": max(0, course_end_candidate_budget),
        "break_start": max(0, break_start_candidate_budget),
        "break_end": max(0, break_end_candidate_budget),
    }
    selected: List[BoundaryCandidate] = []

    def add(candidate: BoundaryCandidate) -> None:
        if candidate not in selected:
            selected.append(candidate)

    for event_type, budget in budget_by_type.items():
        if budget <= 0:
            continue
        strong_items = [
            c for c in candidates
            if c.event_type == event_type and c.keyword_strength != "weak"
        ]
        for candidate in ranked(strong_items)[:budget]:
            add(candidate)

    weak_items = [c for c in candidates if c.keyword_strength == "weak"]
    for candidate in ranked(weak_items)[:max(0, weak_candidate_budget)]:
        add(candidate)

    remaining = [c for c in ranked(candidates) if c not in selected]
    for candidate in remaining:
        add(candidate)
        if len(selected) >= max(1, max_llm_candidates):
            break

    if len(selected) <= max(1, max_llm_candidates):
        return selected

    weak_selected = [c for c in selected if c.keyword_strength == "weak"]
    strong_selected = [c for c in selected if c.keyword_strength != "weak"]
    weak_keep = ranked(weak_selected)[:max(0, weak_candidate_budget)]
    remaining_slots = max(0, max(1, max_llm_candidates) - len(weak_keep))
    return ranked(strong_selected)[:remaining_slots] + weak_keep


async def analyze_course_time(
    text_segments: Iterable[Any],
    *,
    model: Optional[str],
    enable_llm_validation: bool,
    llm_concurrency: int,
    llm_retry_attempts: int,
    max_llm_candidates: int,
    min_break_duration_sec: int,
    max_break_duration_sec: int = 2400,
    course_start_candidate_budget: int = 8,
    course_end_candidate_budget: int = 8,
    break_start_candidate_budget: int = 12,
    break_end_candidate_budget: int = 12,
    weak_candidate_budget: int = 8,
    candidate_context_before_sec: int,
    candidate_context_after_sec: int,
    fallback_window_sec: int,
    max_fallback_windows: int,
) -> Tuple[Dict[str, Any], UsageInfo]:
    candidates = collect_boundary_candidates(
        text_segments,
        candidate_context_before_sec=candidate_context_before_sec,
        candidate_context_after_sec=candidate_context_after_sec,
        fallback_window_sec=fallback_window_sec,
        max_fallback_windows=max_fallback_windows,
    )
    usage = UsageInfo()
    if enable_llm_validation:
        candidates, usage = await refine_candidates_with_llm(
            text_segments,
            candidates,
            model=model,
            concurrency=llm_concurrency,
            retry_attempts=llm_retry_attempts,
            max_llm_candidates=max_llm_candidates,
            course_start_candidate_budget=course_start_candidate_budget,
            course_end_candidate_budget=course_end_candidate_budget,
            break_start_candidate_budget=break_start_candidate_budget,
            break_end_candidate_budget=break_end_candidate_budget,
            weak_candidate_budget=weak_candidate_budget,
        )
    result = analyze_course_time_by_rules(
        text_segments,
        min_break_duration_sec=min_break_duration_sec,
        max_break_duration_sec=max_break_duration_sec,
        candidate_context_before_sec=candidate_context_before_sec,
        candidate_context_after_sec=candidate_context_after_sec,
        fallback_window_sec=fallback_window_sec,
        max_fallback_windows=max_fallback_windows,
        candidates=candidates,
    )
    result["llm_validation_enabled"] = enable_llm_validation
    return result, usage
