import base64
import math
from pathlib import Path

import pytest

from app.core.settings import load_settings
from app.engines.paddle_formula import PaddleFormulaEngine
from app.engines.paddleocr_v6 import PaddleOCRV6Engine
from app.schemas.ocr import OCRRequest
from app.services.formula_service import FormulaService
from app.services.ocr_service import OCRService


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.cpu
def test_formula_pipeline_cpu_end_to_end():
    settings = load_settings(PROJECT_ROOT / "config.toml.example")
    formula_settings = settings.formula.model_copy(update={"enabled": True})
    image_base64 = base64.b64encode(
        (PROJECT_ROOT / "tests" / "fixtures" / "formula-document.png").read_bytes()
    ).decode("ascii")
    text_engine = PaddleOCRV6Engine(settings.ocr)
    formula_engine = PaddleFormulaEngine(formula_settings, settings.ocr)
    service = OCRService(
        engine=text_engine,
        image_max_bytes=settings.ocr.image_max_bytes,
        max_concurrency=settings.ocr.max_concurrency,
        formula_service=FormulaService(
            configured_enabled=True,
            engine=formula_engine,
        ),
    )

    try:
        response = service.predict(
            OCRRequest(
                key=["公式文档-001"],
                value=[image_base64],
                enable_formula=True,
            )
        )
    finally:
        service.close()

    assert response.err_no == 0
    assert response.key == ["公式文档-001"]
    formula_group = response.formula_results[0]
    assert formula_group.image_id == "公式文档-001"
    assert formula_group.status == "success"
    assert len(formula_group.formulas) >= 5
    for formula in formula_group.formulas:
        assert formula.latex.strip()
        assert len(formula.formula_region) == 4
        for point in formula.formula_region:
            assert len(point) == 2
            assert all(
                isinstance(coordinate, int)
                and not isinstance(coordinate, bool)
                for coordinate in point
            )
        assert math.isfinite(formula.detection_confidence)
        assert 0.0 <= formula.detection_confidence <= 1.0
