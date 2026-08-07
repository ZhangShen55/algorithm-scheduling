from fastapi import APIRouter, HTTPException, Query
from app.models.entities import GenericResponse, ExtractKeywordsCoverRequestObject
from app.services.usecases.extract_knowledge import extract_knowledge_with_retry


# 获取主进程日志
try:
    from app.core.logging import get_logger
    log = get_logger(__name__)
    log.setLevel(logging.INFO)
except Exception:
    import logging
    log = logging.getLogger(__name__)

router = APIRouter(tags=["extract_knowledge"])


@router.post("/v0.5/extract_knowledge", response_model=GenericResponse)
async def extract_knowledge(
    request: ExtractKeywordsCoverRequestObject):
    if not request or not request.text or not request.course_name:
        raise HTTPException(status_code=400, detail="无效请求：缺少 text 或 course_name")

    data, usage_sum = await extract_knowledge_with_retry(
        request,
        retry_attempts=3, # 最大重试次数
        shuffle=True, # 随机重置模块内的顺序
        seed=None, # 随机种子，随便写的就写个当前时间吧
        shuffle_modules=False, # 模块不随机
    )

    return GenericResponse(
        model=request.model,
        result=data,
        usage=usage_sum,
    )
