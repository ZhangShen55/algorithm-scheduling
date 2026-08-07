from fastapi import APIRouter
from app.models.entities import TranslateCompletionResponseChoice, TranslateRequestObject, GenericResponse
from app.models.api_response import api_response
from app.services.usecases.multi_country_translate import translate_multi_country
from app.core.config import settings


router = APIRouter(tags=["multi_country_translate"])

@router.post("/v1/translate", response_model=GenericResponse)
async def translate(request: TranslateRequestObject):
    if not request or not request.text:
        return api_response.error(
                # 400
                message="无效请求：文本为空"
            )
    if "zh" not in request.language:
        request.language.append("zh")
    
    result = await translate_multi_country(request)
    return GenericResponse[TranslateCompletionResponseChoice](
        result=result,
    )
