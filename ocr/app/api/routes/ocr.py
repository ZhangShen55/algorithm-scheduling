from fastapi import APIRouter, Depends

from app.api.dependencies import get_ocr_service
from app.core.exceptions import OCRServiceError
from app.schemas.ocr import OCRRequest, OCRResponse
from app.services.ocr_service import OCRService


router = APIRouter()


@router.post("/ocr/prediction", response_model=OCRResponse)
def prediction(
    request: OCRRequest,
    service: OCRService = Depends(get_ocr_service),
) -> OCRResponse:
    try:
        return service.predict(request)
    except OCRServiceError as error:
        return OCRResponse.error(error.err_no, error.public_message)
