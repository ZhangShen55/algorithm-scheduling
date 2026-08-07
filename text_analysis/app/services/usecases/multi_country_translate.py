import asyncio
import time
import re
from typing import List, Dict
from fastapi import HTTPException
from app.core.config import settings
from app.models.entities import (
    TranslateRequestObject,
    TranslateItem,
    TranslateCompletionResponseChoice,
    languages_code_map,
)
from app.models.api_response import api_response
from app.services.llm_executor import chat_raw_mt

try:
    from app.core.logging import get_logger
    log = get_logger(__name__)
except Exception:
    log = logging.getLogger("app.services.usecases.multi_country_translate")

def _normalize_texts(text) -> List[str]:
    if isinstance(text, str):
        return [text]
    if isinstance(text, list):
        return [str(t) for t in text]
    return []


def _chunk_texts(texts: List[str], segment_size: int) -> List[List[str]]:
    if segment_size <= 0 or segment_size >= len(texts):
        return [texts]
    chunks: List[List[str]] = []
    for i in range(0, len(texts), segment_size):
        chunks.append(texts[i:i + segment_size])
    return chunks


def _build_prompt(language: str, texts: List[str]) -> str:
    language_name = languages_code_map.get(language, language)
    numbered = "\n".join([f"{i + 1}:{t}" for i, t in enumerate(texts)])
    return f"Translate the following segments into {language_name}, without additional explanation.\n\n{numbered}"

def _resolve_model(request: TranslateRequestObject) -> str:
    if request.model and request.model != "seacraft-mt":
        return request.model
    if settings.MT_MODEL:
        return settings.MT_MODEL
    return "hy-mt"

def _parse_numbered_lines(content: str) -> List[str]:
    lines = [l.strip() for l in content.splitlines() if l.strip()]
    if not lines:
        return []
    results: List[str] = []
    for line in lines:
        m = re.match(r"^\s*\d+\s*[:：.．]\s*(.*)$", line)
        if m:
            results.append(m.group(1).strip())
        else:
            results.append(line.strip())
    return results

def _fallback_split(content: str, sep: str) -> List[str]:
    return [p.strip() for p in content.split(sep) if p.strip()]


async def _translate_chunk(
    *,
    texts: List[str],
    language: str,
    request: TranslateRequestObject,
    max_retries: int = 3,
) -> List[str]:
    last_reason = "unknown"
    for attempt in range(1, max_retries + 1):
        content, _ = await chat_raw_mt(
            user_prompt=_build_prompt(language, texts),
            model=settings.MT_MODEL,
            temperature=request.temperature,
            top_p=request.top_p,
            top_k=request.top_k,
            presence_penalty=request.repetition_penalty,
        )
        # print(f"translate_chunk attempt={attempt} content=\n\n{content}")
        parts = _parse_numbered_lines(content)
        last_reason = f"count_mismatch={len(parts)} expected={len(texts)}"
        if len(parts) == len(texts):
            return parts
        else:
            log.warning(f"translate_chunk attempt={attempt} {last_reason}")
        if attempt < max_retries:
            await asyncio.sleep(min(2 ** (attempt - 1), 4))
    raise ValueError(last_reason)


# 限流器
_mt_semaphore = asyncio.Semaphore(settings.MT_MAX_CONCURRENCY)

async def _translate_chunk_with_limit(
    *,
    texts: List[str],
    language: str,
    request: TranslateRequestObject,
) -> List[str]:
    # 排队 + 执行
    try:
        # Python 3.10：使用 asyncio.wait_for 3.10 以上使用 asyncio.timeout
        async def _acquire():
            async with _mt_semaphore:
                return await _translate_chunk(texts=texts, language=language, request=request)
        
        return await asyncio.wait_for(_acquire(), timeout=settings.MT_QUEUE_TIMEOUT)

    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="翻译服务繁忙，请稍后重试 (Queue Timeout)")
    except Exception as e:
        raise e


async def _translate_language(
    *,
    language: str,
    texts: List[str],
    request: TranslateRequestObject,
    segment_size: int,
    max_retries: int = 3,
) -> TranslateItem:
    last_reason = "unknown"
    chunks = _chunk_texts(texts, segment_size)
    for attempt in range(1, max_retries + 1):
        try:
            tasks = [
                asyncio.create_task(
                    _translate_chunk_with_limit(texts=chunk, language=language, request=request)
                )
                for chunk in chunks
            ]
            results = await asyncio.gather(*tasks)
            merged: List[str] = []
            for r in results:
                merged.extend(r)
            if len(merged) == len(texts):
                return TranslateItem(content=merged, language=language)
            last_reason = f"merged_count_mismatch={len(merged)} expected={len(texts)}"
        except HTTPException:
            raise  # 503 等直接抛出
        except Exception as e:
            last_reason = str(e)
        if attempt < max_retries:
            await asyncio.sleep(min(2 ** (attempt - 1), 4))
    raise HTTPException(status_code=500, detail=f"翻译失败({language}): {last_reason}")


async def translate_multi_country(
    request: TranslateRequestObject,
) -> TranslateCompletionResponseChoice:
    texts = _normalize_texts(request.text)
    if not texts:
        raise HTTPException(status_code=400, detail="无效请求：文本为空")
    if not request.language:
        raise HTTPException(status_code=400, detail="无效请求：language 为空")
    segment_size = request.segment_size or settings.MT_SEGMENT_SIZE
    if segment_size <= 0:
        segment_size = len(texts)

    start_time = time.time()
    tasks = [
        asyncio.create_task(
            _translate_language(
                language=lang,
                texts=texts,
                request=request,
                segment_size=segment_size,
            )
        )
        for lang in request.language
    ]
    items = await asyncio.gather(*tasks)
    process_time_ms = int((time.time() - start_time) * 1000)
    return TranslateCompletionResponseChoice(
        contents=items,
        process_time_ms=process_time_ms,
        finished_reason="finished",
    )
