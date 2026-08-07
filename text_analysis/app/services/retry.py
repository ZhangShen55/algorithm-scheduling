import json
from pydantic import ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_random_exponential

# 重试装饰器
json_retry = retry(
    retry=retry_if_exception_type((json.JSONDecodeError, ValidationError, AssertionError)),
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(min=1, max=4),
    reraise=True,
)
