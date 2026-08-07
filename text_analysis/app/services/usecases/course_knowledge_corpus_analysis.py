import asyncio
import json
import re
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

from fastapi import HTTPException

from app.core.config import settings
from app.models.entities import CourseKnowledgeCorpusAnalysisRequestObject, UsageInfo
from app.services.llm_executor import chat_raw
from app.services.prompts import load_prompt
from app.services.usecases.language_expression_analysis import (
    filter_effective_segments,
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


ChunkCall = Callable[[Dict[str, Any]], Awaitable[Tuple[Dict[str, Any], Optional[UsageInfo | Dict[str, int]]]]]
FinalCall = Callable[[Dict[str, Any]], Awaitable[Tuple[Dict[str, Any], Optional[UsageInfo | Dict[str, int]]]]]

CHUNK_PROMPT_FILE = "课堂知识点语料提取.md"
FINAL_PROMPT_FILE = "课堂知识点语料汇总.md"
EMPTY_RESULT = {"knowledge_points": [], "corpus": []}

GENERIC_TITLES = {
    "然后",
    "这个",
    "那个",
    "我们",
    "大家",
    "今天",
    "问题",
    "方面",
    "进行",
    "就是",
    "所以",
    "那么",
    "这里",
    "可能",
    "一个",
    "可以",
    "因为",
    "但是",
}


async def analyze_course_knowledge_corpus(
    req: CourseKnowledgeCorpusAnalysisRequestObject,
    *,
    chunk_call: Optional[ChunkCall] = None,
    final_call: Optional[FinalCall] = None,
    chunk_chars: Optional[int] = None,
    chunk_overlap_chars: Optional[int] = None,
    max_chunks: Optional[int] = None,
    chunk_concurrency: Optional[int] = None,
    chunk_retry_attempts: Optional[int] = None,
    final_retry_attempts: Optional[int] = None,
    chunk_max_knowledge_points: Optional[int] = None,
    chunk_max_corpus: Optional[int] = None,
    final_max_knowledge_points: Optional[int] = None,
    final_max_corpus: Optional[int] = None,
    max_description_chars: Optional[int] = None,
    max_corpus_content_chars: Optional[int] = None,
) -> Tuple[Dict[str, Any], UsageInfo, str]:
    if not req.textSegments:
        raise HTTPException(status_code=400, detail="无效请求：文本为空")
    if req.course_start >= req.course_end:
        raise HTTPException(status_code=400, detail="course_start 必须小于 course_end")

    used_model = req.model or settings.OPENAI_MODEL
    chunk_kp_limit = max(1, int(chunk_max_knowledge_points or settings.COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_MAX_KNOWLEDGE_POINTS))
    chunk_corpus_limit = max(1, int(chunk_max_corpus or settings.COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_MAX_CORPUS))
    final_kp_limit = max(1, int(req.max_knowledge_points or final_max_knowledge_points or settings.COURSE_KNOWLEDGE_CORPUS_ANALYSIS_FINAL_MAX_KNOWLEDGE_POINTS))
    final_corpus_limit = max(1, int(req.max_corpus or final_max_corpus or settings.COURSE_KNOWLEDGE_CORPUS_ANALYSIS_FINAL_MAX_CORPUS))
    description_limit = max(1, int(max_description_chars or settings.COURSE_KNOWLEDGE_CORPUS_ANALYSIS_MAX_DESCRIPTION_CHARS))
    corpus_content_limit = max(1, int(max_corpus_content_chars or settings.COURSE_KNOWLEDGE_CORPUS_ANALYSIS_MAX_CORPUS_CONTENT_CHARS))

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
        chunk_chars=chunk_chars or settings.COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_CHARS,
        overlap_chars=chunk_overlap_chars if chunk_overlap_chars is not None else settings.COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_OVERLAP_CHARS,
        max_chunks=max_chunks or settings.COURSE_KNOWLEDGE_CORPUS_ANALYSIS_MAX_CHUNKS,
    )
    if not chunks:
        return dict(EMPTY_RESULT), UsageInfo(), used_model

    sem = asyncio.Semaphore(max(1, int(chunk_concurrency or settings.COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_CONCURRENCY)))
    usage_items: List[Any] = []
    candidates = dict(EMPTY_RESULT)

    async def run_chunk(chunk: Dict[str, Any]):
        async with sem:
            return await _call_chunk_with_retry(
                chunk,
                call=chunk_call or _default_chunk_call(used_model, chunk_kp_limit, chunk_corpus_limit, description_limit, corpus_content_limit),
                retry_attempts=chunk_retry_attempts or settings.COURSE_KNOWLEDGE_CORPUS_ANALYSIS_CHUNK_RETRY_ATTEMPTS,
            )

    tasks = [asyncio.create_task(run_chunk(chunk)) for chunk in chunks]
    for chunk, task in zip(chunks, tasks):
        try:
            result, usage = await task
            cleaned = clean_candidate_payload(
                result,
                max_knowledge_points=chunk_kp_limit,
                max_corpus=chunk_corpus_limit,
                max_description_chars=description_limit,
                max_corpus_content_chars=corpus_content_limit,
            )
            candidates["knowledge_points"].extend(cleaned["knowledge_points"])
            candidates["corpus"].extend(cleaned["corpus"])
            usage_items.append(usage)
        except Exception as exc:
            log.warning(f"[course_knowledge_corpus_analysis] chunk={chunk['chunk_id']} 处理失败：{exc}")

    candidates = dedupe_candidates(candidates)
    candidates["knowledge_points"] = candidates["knowledge_points"][: final_kp_limit * 3]
    candidates["corpus"] = candidates["corpus"][: final_corpus_limit * 3]
    if not candidates["knowledge_points"] and not candidates["corpus"]:
        return dict(EMPTY_RESULT), sum_usage(usage_items), used_model

    payload = {
        "knowledge_points": candidates["knowledge_points"],
        "corpus": candidates["corpus"],
        "max_knowledge_points": final_kp_limit,
        "max_corpus": final_corpus_limit,
        "max_description_chars": description_limit,
        "max_corpus_content_chars": corpus_content_limit,
    }

    try:
        final_result, usage = await _call_final_with_retry(
            payload,
            call=final_call or _default_final_call(used_model),
            retry_attempts=final_retry_attempts or settings.COURSE_KNOWLEDGE_CORPUS_ANALYSIS_FINAL_RETRY_ATTEMPTS,
        )
        usage_items.append(usage)
    except Exception as exc:
        log.warning(f"[course_knowledge_corpus_analysis] 最终汇总失败，使用候选兜底：{exc}")
        final_result = candidates

    sanitized = sanitize_final_result(
        final_result,
        max_knowledge_points=final_kp_limit,
        max_corpus=final_corpus_limit,
        max_description_chars=description_limit,
        max_corpus_content_chars=corpus_content_limit,
    )
    return sanitized, sum_usage(usage_items), used_model


def clean_candidate_payload(
    data: Any,
    *,
    max_knowledge_points: int,
    max_corpus: int,
    max_description_chars: int,
    max_corpus_content_chars: int,
) -> Dict[str, List[Dict[str, str]]]:
    return sanitize_final_result(
        data,
        max_knowledge_points=max_knowledge_points,
        max_corpus=max_corpus,
        max_description_chars=max_description_chars,
        max_corpus_content_chars=max_corpus_content_chars,
    )


def dedupe_candidates(data: Dict[str, Any]) -> Dict[str, List[Dict[str, str]]]:
    knowledge_points: List[Dict[str, str]] = []
    corpus: List[Dict[str, str]] = []
    seen_titles = set()
    seen_contents = set()

    for item in data.get("knowledge_points") or []:
        if not isinstance(item, dict):
            continue
        title = _clean_inline_text(item.get("title"))
        description = _clean_inline_text(item.get("description"))
        key = _normalize_key(title)
        if not title or not description or key in seen_titles:
            continue
        seen_titles.add(key)
        knowledge_points.append({"title": title, "description": description})

    for item in data.get("corpus") or []:
        if not isinstance(item, dict):
            continue
        content = _clean_inline_text(item.get("content"))
        description = _clean_inline_text(item.get("description"))
        key = _normalize_key(content)
        if not content or not description or key in seen_contents:
            continue
        seen_contents.add(key)
        corpus.append({"content": content, "description": description})

    return {"knowledge_points": knowledge_points, "corpus": corpus}


def sanitize_final_result(
    data: Any,
    *,
    max_knowledge_points: int,
    max_corpus: int,
    max_description_chars: int,
    max_corpus_content_chars: int,
) -> Dict[str, List[Dict[str, str]]]:
    if not isinstance(data, dict):
        data = {}

    knowledge_points: List[Dict[str, str]] = []
    corpus: List[Dict[str, str]] = []
    seen_titles = set()
    seen_contents = set()

    for item in data.get("knowledge_points") or []:
        if not isinstance(item, dict):
            continue
        title = _clean_inline_text(item.get("title"))
        description = _limit_text(_clean_inline_text(item.get("description")), max_description_chars)
        key = _normalize_key(title)
        if not _is_valid_knowledge_point(title, description) or key in seen_titles:
            continue
        seen_titles.add(key)
        knowledge_points.append({"title": title, "description": description})
        if len(knowledge_points) >= max(1, int(max_knowledge_points)):
            break

    for item in data.get("corpus") or []:
        if not isinstance(item, dict):
            continue
        content = _limit_text(_clean_inline_text(item.get("content")), max_corpus_content_chars)
        description = _limit_text(_clean_inline_text(item.get("description")), max_description_chars)
        key = _normalize_key(content)
        if not _is_valid_corpus(content, description) or key in seen_contents:
            continue
        seen_contents.add(key)
        corpus.append({"content": content, "description": description})
        if len(corpus) >= max(1, int(max_corpus)):
            break

    return {"knowledge_points": knowledge_points, "corpus": corpus}


async def _call_chunk_with_retry(
    chunk: Dict[str, Any],
    *,
    call: ChunkCall,
    retry_attempts: int,
) -> Tuple[Dict[str, Any], Optional[UsageInfo | Dict[str, int]]]:
    last_exc: Optional[Exception] = None
    for attempt in range(1, max(1, retry_attempts) + 1):
        try:
            result, usage = await call(chunk)
            if not isinstance(result, dict):
                raise ValueError("知识点和语料候选结果必须是对象")
            return result, usage
        except Exception as exc:
            last_exc = exc
            if attempt < max(1, retry_attempts):
                await asyncio.sleep(min(2 ** (attempt - 1), 4))
    raise last_exc or RuntimeError("知识点和语料候选提取失败")


async def _call_final_with_retry(
    payload: Dict[str, Any],
    *,
    call: FinalCall,
    retry_attempts: int,
) -> Tuple[Dict[str, Any], Optional[UsageInfo | Dict[str, int]]]:
    last_exc: Optional[Exception] = None
    for attempt in range(1, max(1, retry_attempts) + 1):
        try:
            result, usage = await call(payload)
            if not isinstance(result, dict):
                raise ValueError("知识点和语料汇总结果必须是对象")
            return result, usage
        except Exception as exc:
            last_exc = exc
            if attempt < max(1, retry_attempts):
                await asyncio.sleep(min(2 ** (attempt - 1), 4))
    raise last_exc or RuntimeError("知识点和语料汇总失败")


def _default_chunk_call(
    model: str,
    max_knowledge_points: int,
    max_corpus: int,
    max_description_chars: int,
    max_corpus_content_chars: int,
) -> ChunkCall:
    async def call(chunk: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[UsageInfo | Dict[str, int]]]:
        payload = {
            "text": chunk["text"],
            "max_knowledge_points": max_knowledge_points,
            "max_corpus": max_corpus,
            "max_description_chars": max_description_chars,
            "max_corpus_content_chars": max_corpus_content_chars,
        }
        content, usage = await chat_raw(
            user_prompt=json.dumps(payload, ensure_ascii=False),
            system_prompt=load_prompt(CHUNK_PROMPT_FILE),
            model=model,
            max_tokens=2048,
            temperature=0.2,
            top_p=0.9,
            response_format={"type": "json_object"},
            extra_body={"top_k": 20, "chat_template_kwargs": {"enable_thinking": False}},
        )
        return _parse_json(content), usage

    return call


def _default_final_call(model: str) -> FinalCall:
    async def call(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[UsageInfo | Dict[str, int]]]:
        content, usage = await chat_raw(
            user_prompt=json.dumps(payload, ensure_ascii=False),
            system_prompt=load_prompt(FINAL_PROMPT_FILE),
            model=model,
            max_tokens=4096,
            temperature=0.2,
            top_p=0.9,
            response_format={"type": "json_object"},
            extra_body={"top_k": 20, "chat_template_kwargs": {"enable_thinking": False}},
        )
        return _parse_json(content), usage

    return call


def _parse_json(content: str) -> Dict[str, Any]:
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = json.loads(llm_json_response_repair(content))
    if not isinstance(data, dict):
        raise ValueError("模型返回 JSON 结构不符合预期")
    return data


def _is_valid_knowledge_point(title: str, description: str) -> bool:
    if not title or not description:
        return False
    key = _normalize_key(title)
    if key in {_normalize_key(item) for item in GENERIC_TITLES}:
        return False
    if len(key) < 2:
        return False
    if re.fullmatch(r"[\d\W_]+", key):
        return False
    return True


def _is_valid_corpus(content: str, description: str) -> bool:
    if not content or not description:
        return False
    key = _normalize_key(content)
    if len(key) < 8:
        return False
    if re.fullmatch(r"[\d\W_]+", key):
        return False
    return True


def _clean_inline_text(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or "").strip())


def _normalize_key(text: str) -> str:
    return re.sub(r"[\s，。！？；：、,.!?;:（）()《》\"'“”‘’\-_/]+", "", text or "").lower()


def _limit_text(text: str, limit: int) -> str:
    cleaned = _clean_inline_text(text)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit]
