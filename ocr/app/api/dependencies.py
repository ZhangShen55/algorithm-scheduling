from fastapi import Request

from app.services.ocr_service import OCRService


def get_ocr_service(request: Request) -> OCRService:
    return request.app.state.ocr_service
