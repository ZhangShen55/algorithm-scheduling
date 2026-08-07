import asyncio
import json
import math
import random
import re
from copy import deepcopy
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from fastapi import HTTPException

from app.core.config import settings
from app.models.entities import ExtractKeywordsCoverRequestObject, UsageInfo
from app.services.llm_executor import chat_raw
from app.services.usecases.extract_knowledge import (
    _validate_extraction_result,
    extract_knowledge_with_retry,
)
from app.utils import (
    coerce_usage,
    llm_json_response_repair,
    shuffle_knowledge_modules,
    strip_think_blocks,
    sum_usage,
)

try:
    from app.core.logging import get_logger
    log = get_logger(__name__)
except Exception:
    import logging
    log = logging.getLogger(__name__)


SKELETON_SYSTEM_PROMPT = """你是高校课程设计专家。
任务：只根据课程名称生成标准教学大纲知识点骨架，不判断课堂是否覆盖。
输出严格 JSON，字段为英文：
{"course_name":"课程名称","knowledge":{"模块标题":{"知识点":false}}}
要求：
1. 至少 4 个模块；
2. 每个模块至少 5 个知识点；
3. 模块和知识点用中文，禁止数字前缀；
4. 单个知识点不超过 15 个汉字；
5. 所有知识点值必须为 false；
6. 只输出 JSON。"""

SEGMENT_SYSTEM_PROMPT = """你是课堂内容覆盖判断助手。
任务：根据本段课堂文本，判断给定知识点骨架中哪些知识点被讲到。
要求：
1. 必须沿用输入 skeleton 的模块和知识点；
2. 不得新增、删除、改名模块或知识点；
3. 本段明确讲到的知识点标 true，否则标 false；
4. 只根据本段文本判断，不要臆测；
5. 只输出 JSON，格式为 {"course_name":"课程名称","knowledge":{"模块标题":{"知识点":true}}}。"""

SentenceCall = Callable[[str], Awaitable[Tuple[Dict[str, Any], Optional[UsageInfo]]]]


def split_text_for_knowledge(
    text: str,
    *,
    segment_chars: int,
    overlap_chars: int,
    max_segments: int,
) -> List[str]:
    text = text or ""
    if not text:
        return []

    segment_chars = max(1, int(segment_chars or 1))
    overlap_chars = max(0, int(overlap_chars or 0))
    max_segments = max(1, int(max_segments or 1))

    target_chars = max(segment_chars, math.ceil(len(text) / max_segments))
    units = _split_sentence_units(text)
    parts = _pack_units(units, target_chars)

    if len(parts) > max_segments:
        target_chars = math.ceil(len(text) / max_segments)
        parts = _pack_units(units, max(target_chars, segment_chars))

    if len(parts) <= 1 or overlap_chars <= 0:
        return parts

    overlapped = [parts[0]]
    for idx in range(1, len(parts)):
        overlap = parts[idx - 1][-overlap_chars:]
        overlapped.append(overlap + parts[idx])
    return overlapped


def merge_knowledge_results(
    course_name: str,
    skeleton: Dict[str, Any],
    segment_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    merged = {
        "course_name": course_name or skeleton.get("course_name", ""),
        "knowledge": deepcopy(skeleton.get("knowledge", {})),
    }

    for module_name, points in merged["knowledge"].items():
        merged["knowledge"][module_name] = {point_name: bool(value) for point_name, value in points.items()}

    for result in segment_results:
        knowledge = result.get("knowledge", {}) if isinstance(result, dict) else {}
        if not isinstance(knowledge, dict):
            continue
        for module_name, points in knowledge.items():
            if module_name not in merged["knowledge"] or not isinstance(points, dict):
                continue
            for point_name, covered in points.items():
                if point_name in merged["knowledge"][module_name] and covered is True:
                    merged["knowledge"][module_name][point_name] = True

    return merged


def ensure_min_true_per_module(data: Dict[str, Any], *, min_true_per_module: int) -> Dict[str, Any]:
    target = max(0, int(min_true_per_module or 0))
    if target <= 0:
        return data

    adjusted = deepcopy(data)
    knowledge = adjusted.get("knowledge", {})
    if not isinstance(knowledge, dict):
        return adjusted

    for points in knowledge.values():
        if not isinstance(points, dict):
            continue
        true_count = sum(1 for covered in points.values() if covered is True)
        if true_count >= target:
            continue
        for point_name, covered in points.items():
            if covered is False:
                points[point_name] = True
                true_count += 1
                if true_count >= target:
                    break
    return adjusted


async def call_segment_with_fallback(
    segment_text: str,
    *,
    call_once: SentenceCall,
    retry_attempts: int,
    fallback_split: bool,
) -> Tuple[List[Dict[str, Any]], List[Optional[UsageInfo]]]:
    try:
        return await _call_segment_with_retry(segment_text, call_once=call_once, retry_attempts=retry_attempts)
    except Exception:
        if not fallback_split or len(segment_text) <= 1:
            raise

    left, right = _split_in_half(segment_text)
    results: List[Dict[str, Any]] = []
    usages: List[Optional[UsageInfo]] = []
    for part in (left, right):
        part_results, part_usages = await _call_segment_with_retry(part, call_once=call_once, retry_attempts=retry_attempts)
        results.extend(part_results)
        usages.extend(part_usages)
    return results, usages


async def extract_knowledge_v2_with_retry(
    req: ExtractKeywordsCoverRequestObject,
    *,
    model: Optional[str] = None,
    shuffle: bool = False,
    seed: Optional[int] = None,
    shuffle_modules: bool = False,
) -> Tuple[Dict[str, Any], UsageInfo]:
    if req.messages is not None or not settings.EXTRACT_KNOWLEDGE_V2_ENABLE_SEGMENTATION:
        return await extract_knowledge_with_retry(
            req,
            model=model or req.model,
            retry_attempts=settings.EXTRACT_KNOWLEDGE_V2_RETRY_ATTEMPTS,
            shuffle=shuffle,
            seed=seed,
            shuffle_modules=shuffle_modules,
        )

    text = req.text or ""
    if len(text) <= settings.EXTRACT_KNOWLEDGE_V2_MAX_TEXT_CHARS:
        return await extract_knowledge_with_retry(
            req,
            model=model or req.model,
            retry_attempts=settings.EXTRACT_KNOWLEDGE_V2_RETRY_ATTEMPTS,
            shuffle=shuffle,
            seed=seed,
            shuffle_modules=shuffle_modules,
        )

    usages: List[Optional[UsageInfo]] = []
    skeleton, skeleton_usage = await _build_skeleton_with_retry(
        course_name=req.course_name,
        model=model or req.model,
        temperature=req.temperature or 0.6,
        retry_attempts=settings.EXTRACT_KNOWLEDGE_V2_RETRY_ATTEMPTS,
    )
    usages.append(skeleton_usage)

    segments = split_text_for_knowledge(
        text,
        segment_chars=settings.EXTRACT_KNOWLEDGE_V2_SEGMENT_CHARS,
        overlap_chars=settings.EXTRACT_KNOWLEDGE_V2_SEGMENT_OVERLAP_CHARS,
        max_segments=settings.EXTRACT_KNOWLEDGE_V2_MAX_SEGMENTS,
    )
    if not segments:
        raise HTTPException(status_code=400, detail="无效请求：文本为空")

    sem = asyncio.Semaphore(max(1, settings.EXTRACT_KNOWLEDGE_V2_CONCURRENCY))

    async def call_once(segment_text: str) -> Tuple[Dict[str, Any], Optional[UsageInfo]]:
        async with sem:
            return await _judge_segment_once(
                course_name=req.course_name,
                skeleton=skeleton,
                segment_text=segment_text,
                model=model or req.model,
                temperature=req.temperature or 0.6,
            )

    tasks = [
        asyncio.create_task(
            call_segment_with_fallback(
                segment,
                call_once=call_once,
                retry_attempts=settings.EXTRACT_KNOWLEDGE_V2_SEGMENT_RETRY_ATTEMPTS,
                fallback_split=settings.EXTRACT_KNOWLEDGE_V2_FALLBACK_SPLIT,
            )
        )
        for segment in segments
    ]

    segment_results: List[Dict[str, Any]] = []
    for idx, task in enumerate(tasks):
        try:
            results, result_usages = await task
        except Exception as exc:
            log.error(f"[extract_knowledge_v2] 分段 {idx + 1}/{len(segments)} 处理失败：{exc}")
            raise HTTPException(status_code=400, detail=f"分段知识点覆盖判断失败：{exc}") from exc
        segment_results.extend(results)
        usages.extend(result_usages)

    data = merge_knowledge_results(req.course_name, skeleton, segment_results)
    data = ensure_min_true_per_module(
        data,
        min_true_per_module=settings.EXTRACT_KNOWLEDGE_V2_MIN_TRUE_PER_MODULE,
    )
    ok, reason = _validate_extraction_result(data)
    if not ok:
        raise HTTPException(status_code=400, detail=f"模型返回不符合知识结构要求：{reason}")

    if shuffle:
        data = shuffle_knowledge_modules(
            data,
            seed=seed,
            shuffle_modules=shuffle_modules,
            shuffle_points=True,
        )
    return data, sum_usage(usages)


def _split_sentence_units(text: str) -> List[str]:
    units = [unit for unit in re.split(r"(?<=[。！？!?；;\n])", text) if unit]
    return units or [text]


def _pack_units(units: List[str], target_chars: int) -> List[str]:
    parts: List[str] = []
    current = ""
    for unit in units:
        if current and len(current) + len(unit) > target_chars:
            parts.append(current)
            current = unit
        else:
            current += unit
    if current:
        parts.append(current)
    return parts


def _split_in_half(text: str) -> Tuple[str, str]:
    mid = max(1, len(text) // 2)
    for idx in range(mid, len(text)):
        if text[idx] in "。！？!?；;\n":
            return text[: idx + 1], text[idx + 1 :]
    return text[:mid], text[mid:]


async def _call_segment_with_retry(
    segment_text: str,
    *,
    call_once: SentenceCall,
    retry_attempts: int,
) -> Tuple[List[Dict[str, Any]], List[Optional[UsageInfo]]]:
    last_exc: Optional[Exception] = None
    for attempt in range(1, max(1, retry_attempts) + 1):
        try:
            data, usage = await call_once(segment_text)
            return [data], [usage]
        except Exception as exc:
            last_exc = exc
            if attempt < retry_attempts:
                await asyncio.sleep(min(2 ** (attempt - 1), 4) + random.uniform(0, 0.2))
    raise last_exc or RuntimeError("segment retry failed")


async def _build_skeleton_with_retry(
    *,
    course_name: str,
    model: Optional[str],
    temperature: float,
    retry_attempts: int,
) -> Tuple[Dict[str, Any], Optional[UsageInfo]]:
    last_reason = "unknown"
    usages: List[Optional[UsageInfo]] = []
    for attempt in range(1, max(1, retry_attempts) + 1):
        data, usage = await _llm_json_once(
            messages=[
                {"role": "system", "content": SKELETON_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({"course_name": course_name}, ensure_ascii=False)},
            ],
            model=model,
            temperature=temperature,
        )
        usages.append(usage)
        ok, reason = _validate_knowledge_shape(data, allow_all_false=True)
        if ok:
            return _force_skeleton_false(course_name, data), sum_usage(usages)
        last_reason = reason
        log.warning(f"[extract_knowledge_v2] 骨架生成校验不通过，尝试 {attempt}/{retry_attempts}：{last_reason}")
    raise HTTPException(status_code=400, detail=f"知识点骨架生成失败：{last_reason}")


async def _judge_segment_once(
    *,
    course_name: str,
    skeleton: Dict[str, Any],
    segment_text: str,
    model: Optional[str],
    temperature: float,
) -> Tuple[Dict[str, Any], Optional[UsageInfo]]:
    data, usage = await _llm_json_once(
        messages=[
            {"role": "system", "content": SEGMENT_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "course_name": course_name,
                        "skeleton": skeleton,
                        "text": segment_text,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        model=model,
        temperature=temperature,
    )
    normalized = merge_knowledge_results(course_name, skeleton, [data])
    ok, reason = _validate_knowledge_shape(normalized, allow_all_false=True)
    if not ok:
        raise ValueError(reason)
    return normalized, usage


async def _llm_json_once(
    *,
    messages: List[dict],
    model: Optional[str],
    temperature: float,
) -> Tuple[Dict[str, Any], Optional[UsageInfo]]:
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


def _validate_knowledge_shape(data: Dict[str, Any], *, allow_all_false: bool) -> Tuple[bool, str]:
    if not allow_all_false:
        return _validate_extraction_result(data)
    if not isinstance(data, dict):
        return False, "顶层不是对象"
    if "course_name" not in data or not isinstance(data["course_name"], str) or not data["course_name"].strip():
        return False, "缺少或非法的 course_name"
    knowledge = data.get("knowledge")
    if not isinstance(knowledge, dict) or not knowledge:
        return False, "缺少或非法的 knowledge"
    for module_title, points in knowledge.items():
        if not isinstance(module_title, str) or not module_title.strip():
            return False, "存在空的模块标题"
        if not isinstance(points, dict) or not points:
            return False, f"模块 `{module_title}` 的值不是非空对象"
        for point_name, covered in points.items():
            if not isinstance(point_name, str) or not point_name.strip():
                return False, f"模块 `{module_title}` 中存在空的知识点名"
            if not isinstance(covered, bool):
                return False, f"模块 `{module_title}` 的知识点 `{point_name}` 取值不是布尔"
    return True, "ok"


def _force_skeleton_false(course_name: str, data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "course_name": course_name or data.get("course_name", ""),
        "knowledge": {
            module_name: {point_name: False for point_name in points.keys()}
            for module_name, points in data.get("knowledge", {}).items()
            if isinstance(points, dict)
        },
    }
