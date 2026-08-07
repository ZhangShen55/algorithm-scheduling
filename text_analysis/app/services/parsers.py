import json
from typing import Type, TypeVar
from pydantic import ValidationError
from app.utils import llm_json_response_repair

T = TypeVar("T")

def to_json(data_str: str) -> dict:
    fixed = llm_json_response_repair(data_str or "")
    return json.loads(fixed)

def to_model(data_str: str, model: Type[T]) -> T:
    try:
        obj = to_json(data_str)
        return model(**obj)
    except (json.JSONDecodeError, ValidationError, AssertionError):
        # 让 tenacity 捕获并重试
        raise
