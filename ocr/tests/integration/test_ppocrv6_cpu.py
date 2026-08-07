import base64
import json
from pathlib import Path

import pytest

from app.core.settings import load_settings
from app.engines.paddleocr_v6 import PaddleOCRV6Engine
from app.schemas.ocr import OCRRequest
from app.services.ocr_service import OCRService


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.cpu
def test_ppocrv6_medium_cpu_end_to_end():
    settings = load_settings(PROJECT_ROOT / "config.toml.example")
    image_base64 = base64.b64encode(
        (PROJECT_ROOT / "tests" / "fixtures" / "ocr-test.jpg").read_bytes()
    ).decode("ascii")
    engine = PaddleOCRV6Engine(settings.ocr)
    service = OCRService(
        engine=engine,
        image_max_bytes=settings.ocr.image_max_bytes,
        max_concurrency=settings.ocr.max_concurrency,
    )

    try:
        response = service.predict(
            OCRRequest(key=["image"], value=[image_base64])
        )
    finally:
        service.close()

    assert response.err_no == 0
    assert response.err_msg == ""
    assert response.key == ["image"]
    assert response.formula_results == []
    results = json.loads(response.value[0])
    assert results
    for item in results:
        assert isinstance(item["text"], str) and item["text"]
        assert isinstance(item["confidence"], float)
        assert len(item["text_region"]) == 4
        assert all(
            len(point) == 2 and all(isinstance(value, int) for value in point)
            for point in item["text_region"]
        )
