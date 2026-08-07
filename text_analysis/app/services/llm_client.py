from functools import lru_cache
from openai import AsyncOpenAI
from app.core.config import settings

@lru_cache
def get_async_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_BASE_URL or None,
        timeout=60 * 60,
    )

def get_model_name() -> str:
    return settings.OPENAI_MODEL