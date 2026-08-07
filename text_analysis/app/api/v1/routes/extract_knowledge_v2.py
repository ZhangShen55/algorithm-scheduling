from fastapi import APIRouter, HTTPException

from app.models.entities import ExtractKeywordsCoverRequestObject, GenericResponse
from app.services.usecases.extract_knowledge_v2 import extract_knowledge_v2_with_retry

try:
    from app.core.logging import get_logger
    log = get_logger(__name__)
    log.setLevel(logging.INFO)
except Exception:
    import logging
    log = logging.getLogger(__name__)

router = APIRouter(tags=["extract_knowledge_v2"])


@router.post("/v1/extract_knowledge", response_model=GenericResponse)
async def extract_knowledge_v2(request: ExtractKeywordsCoverRequestObject):
    if not request or not request.text or not request.course_name:
        raise HTTPException(status_code=400, detail="无效请求：缺少 text 或 course_name")

    data, usage_sum = await extract_knowledge_v2_with_retry(
        request,
        model=request.model,
        shuffle=True,
        seed=None,
        shuffle_modules=False,
    )

    return GenericResponse(
        model=request.model,
        result=data,
        usage=usage_sum,
    )
