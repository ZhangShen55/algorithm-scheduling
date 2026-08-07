import logging
from typing import Optional, TypeVar, Dict, Any, List
from app.services.parsers import to_model
from openai import AsyncOpenAI
from app.core.config import settings
from functools import lru_cache
from app.utils import strip_think_blocks


try:
    from app.core.logging import get_logger
    log = get_logger(__name__)
    log.setLevel(logging.INFO)
except Exception:
    log = logging.getLogger("app.services.llm_executor")

T = TypeVar("T")


@lru_cache
def get_async_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_BASE_URL or None,
        timeout=60 * 60,
    )

def get_model_name() -> str:
    return settings.OPENAI_MODEL

@lru_cache
def get_mt_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=settings.MT_API_KEY,
        base_url=settings.MT_BASE_URL or None,
        timeout=60 * 60,
    )

def get_mt_model_name() -> str:
    return settings.MT_MODEL or "hy-mt"

def _coerce_usage(u: Any) -> Dict[str, int]:
    """把各种库返回的 usage 结构兜成统一 dict，缺省为 0。"""
    d = {}
    if u is None:
        d = {}
    elif isinstance(u, dict):
        d = u
    else:
        # openai==x.y 用对象；vLLM 可能也是对象
        d = getattr(u, "model_dump", getattr(u, "dict", lambda: {}))()
    return {
        "prompt_tokens": int(d.get("prompt_tokens") or 0),
        "completion_tokens": int(d.get("completion_tokens") or 0),
        "total_tokens": int(d.get("total_tokens") or 0),
    }

async def chat_raw(
    *,
    user_prompt: str = "",
    system_prompt: Optional[str] = None,
    messages: Optional[List[dict]] = None,
    model: Optional[str] = None,
    max_tokens: int = 2048,
    temperature: float = 0.7,
    top_p: float = 0.8,
    presence_penalty: float = 1.5,
    response_format: Optional[dict] = None,
    extra_body: Optional[Dict[str, Any]] = None,
) -> tuple[str, Dict[str, int]]:
    """返回 (content_str, usage_dict)，即使后续解析失败，usage 也能拿到。"""
    client = get_async_client()
    model_name = model or get_model_name()

    if messages is None:
        msgs: List[dict] = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.append({"role": "user", "content": user_prompt})
    else:
        msgs = messages

    resp = await client.chat.completions.create(
        model=model_name,
        messages=msgs,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        presence_penalty=presence_penalty,
        response_format= response_format,  # {"type": "json_object"} 设置成这个直接回返回JSON格式 但是默认就会关闭思考
        extra_body=extra_body
    )
    content = strip_think_blocks(resp.choices[0].message.content) or ""
    # 日志打印
    log.debug(f"chat_raw_content: {content}")
    usage = _coerce_usage(getattr(resp, "usage", None))
    return content, usage


async def chat__raw_ex(
    *,
    user_prompt: str = "",
    system_prompt: Optional[str] = None,
    messages: Optional[List[dict]] = None,
    model: Optional[str] = None,
    max_tokens: int = 2048,
    temperature: float = 0.7,
    top_p: float = 0.8,
    presence_penalty: float = 1.1,
    response_format: Optional[dict] = None,
    extra_body: Optional[Dict[str, Any]] = None,
) -> tuple[str, Dict[str, int]]:
    """返回 (content_str, usage_dict)，即使后续解析失败，usage 也能拿到。"""
    client = get_async_client()
    model_name = model or get_model_name()

    if messages is None:
        msgs: List[dict] = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.append({"role": "user", "content": user_prompt})
    else:
        msgs = messages

    resp = await client.chat.completions.create(
        model=model_name,
        messages=msgs,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        presence_penalty=presence_penalty,
        response_format= response_format,  # {"type": "json_object"} 设置成这个直接回返回JSON格式 但是默认就会关闭思考
        extra_body=extra_body
    )
    content = strip_think_blocks(resp.choices[0].message.content) or ""
    # 日志打印
    log.debug(f"chat_raw_ex_content: {content}")
    usage = _coerce_usage(getattr(resp, "usage", None))
    return content, usage

async def chat_raw_mt(
    *,
    user_prompt: str = "",
    model: Optional[str] = None,
    max_tokens: int = 1024,
    temperature: float = 0.7,
    top_p: float = 0.6,
    top_k: int = 20,
    presence_penalty: float = 1.05,

) -> tuple[str, Dict[str, int]]:
    """返回 (content_str, usage_dict)，即使后续解析失败，usage 也能拿到。"""
    client = get_mt_client()
    model_name = model or get_mt_model_name()

    msgs = [{"role": "user", "content": user_prompt}]

    resp = await client.chat.completions.create(
        model=model_name,
        messages=msgs,
        # max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        presence_penalty=presence_penalty,
        extra_body={
            "top_k": top_k,
            "repetition_penalty": presence_penalty,
        },
    )
    content = strip_think_blocks(resp.choices[0].message.content) or ""
    # 日志打印
    log.debug(f"chat_raw_content: {content}")
    usage = _coerce_usage(getattr(resp, "usage", None))
    return content, usage

