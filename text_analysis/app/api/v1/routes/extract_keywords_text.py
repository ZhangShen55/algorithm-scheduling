from fastapi import APIRouter, HTTPException
from app.models.entities import (
    GenericResponse, ContentRequestObject, ChatMessage
)
from app.services.llm_client import get_async_client, get_model_name
from app.services.prompts import load_prompt
from app.utils import process_response, coerce_usage

try:
    from app.core.logging import get_logger
    log = get_logger(__name__)
    log.setLevel(logging.INFO)
except Exception:
    import logging
    log = logging.getLogger(__name__)
    log.setLevel(logging.INFO)

router = APIRouter(tags=["keywords-text"])
model = get_model_name()

@router.post("/v1/extract_keywords", response_model=GenericResponse)
async def extract_keywords_v1(request: ContentRequestObject):
    if not request or not request.text:
        log.error("无效请求：文本为空")
        raise HTTPException(status_code=400, detail="无效请求：文本为空")

    messages = [
            ChatMessage(role="system", content=load_prompt("关键词提取.md")),
            ChatMessage(role="user", content=str(request.text))
        ]

    client = get_async_client()
    
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=512,
            temperature=0.7,
            top_p=0.8,
            extra_body={"top_k": 20, "chat_template_kwargs": {"enable_thinking": False}}
        )
    except Exception as e:
        log.exception("模型调用失败(v1), 模型不通, 请检查模型服务配置是否正常")
        raise HTTPException(status_code=500, detail=f"模型调用失败: {e}")

    result_data = process_response(response, "extract_keywords", remove_json_tag=True)
    result_data_obj = {"keywords":result_data}
    log.debug(f"关键词提取结果: {result_data}")
    return GenericResponse(
        model=request.model,
        result=result_data_obj,
        usage=coerce_usage(getattr(response, "usage", None))
    )
