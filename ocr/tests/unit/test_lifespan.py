from fastapi.testclient import TestClient
import pytest

from app.core.settings import load_settings
from app.main import create_app


def test_lifespan_creates_one_engine_and_reuses_it(
    monkeypatch, settings_file, image_base64
):
    from app.engines import paddleocr_v6

    created = []

    class RecordingEngine:
        def __init__(self, settings, *, require_gpu=False):
            del settings, require_gpu
            self.calls = 0
            self.close_calls = 0
            created.append(self)

        def predict(self, image):
            self.calls += 1
            return []

        def close(self):
            self.close_calls += 1

    monkeypatch.setattr(paddleocr_v6, "PaddleOCRV6Engine", RecordingEngine)
    app = create_app(settings=load_settings(settings_file))

    with TestClient(app) as client:
        for _ in range(2):
            response = client.post(
                "/ocr/prediction",
                json={"key": ["image"], "value": [image_base64]},
            )
            assert response.json()["err_no"] == 0

    assert len(created) == 1
    assert created[0].calls == 2
    assert created[0].close_calls == 1


def test_lifespan_initializes_enabled_formula_engine_once(
    monkeypatch, settings_file, image_base64
):
    from app.engines import paddle_formula, paddleocr_v6

    (settings_file.parent / "models" / "PP-DocLayout_plus-L").mkdir()
    (settings_file.parent / "models" / "PP-FormulaNet_plus-M").mkdir()
    content = settings_file.read_text(encoding="utf-8")
    settings_file.write_text(
        content.replace("enabled = false", "enabled = true"),
        encoding="utf-8",
    )
    formula_engines = []

    class TextEngine:
        def __init__(self, settings, *, require_gpu=False):
            del settings, require_gpu
            self.close_calls = 0

        def predict(self, image):
            return []

        def close(self):
            self.close_calls += 1

    class FormulaEngine:
        def __init__(self, formula_settings, ocr_settings):
            self.calls = 0
            self.close_calls = 0
            formula_engines.append(self)

        def predict(self, image):
            self.calls += 1
            return []

        def close(self):
            self.close_calls += 1

    monkeypatch.setattr(paddleocr_v6, "PaddleOCRV6Engine", TextEngine)
    monkeypatch.setattr(paddle_formula, "PaddleFormulaEngine", FormulaEngine)
    app = create_app(settings=load_settings(settings_file))

    with TestClient(app) as client:
        for _ in range(2):
            response = client.post(
                "/ocr/prediction",
                json={
                    "key": ["image"],
                    "value": [image_base64],
                    "enable_formula": True,
                },
            )
            assert response.json()["formula_results"][0]["status"] == "success"

    assert len(formula_engines) == 1
    assert formula_engines[0].calls == 2
    assert formula_engines[0].close_calls == 1


def test_lifespan_closes_ocr_engine_when_formula_initialization_fails(
    monkeypatch,
    settings_file,
):
    from app.engines import paddle_formula, paddleocr_v6

    (settings_file.parent / "models" / "PP-DocLayout_plus-L").mkdir()
    (settings_file.parent / "models" / "PP-FormulaNet_plus-M").mkdir()
    settings_file.write_text(
        settings_file.read_text(encoding="utf-8").replace(
            "enabled = false",
            "enabled = true",
        ),
        encoding="utf-8",
    )
    created = []

    class TextEngine:
        def __init__(self, settings, *, require_gpu=False):
            del settings, require_gpu
            self.close_calls = 0
            created.append(self)

        def predict(self, image):
            return []

        def close(self):
            self.close_calls += 1

    class FailingFormulaEngine:
        def __init__(self, formula_settings, ocr_settings):
            raise RuntimeError("formula startup failed")

    monkeypatch.setattr(paddleocr_v6, "PaddleOCRV6Engine", TextEngine)
    monkeypatch.setattr(
        paddle_formula,
        "PaddleFormulaEngine",
        FailingFormulaEngine,
    )
    app = create_app(settings=load_settings(settings_file))

    with pytest.raises(RuntimeError, match="formula startup failed"):
        with TestClient(app):
            pass

    assert len(created) == 1
    assert created[0].close_calls == 1


def test_lifespan_closes_both_engines_when_service_initialization_fails(
    monkeypatch,
    settings_file,
):
    import app.main as main_module

    (settings_file.parent / "models" / "PP-DocLayout_plus-L").mkdir()
    (settings_file.parent / "models" / "PP-FormulaNet_plus-M").mkdir()
    settings_file.write_text(
        settings_file.read_text(encoding="utf-8").replace(
            "enabled = false",
            "enabled = true",
        ),
        encoding="utf-8",
    )

    class Engine:
        def __init__(self):
            self.close_calls = 0

        def predict(self, image):
            return []

        def close(self):
            self.close_calls += 1

    text_engine = Engine()
    formula_engine = Engine()

    def fail_service_initialization(**kwargs):
        raise RuntimeError("service startup failed")

    monkeypatch.setattr(main_module, "OCRService", fail_service_initialization)
    app = create_app(
        settings=load_settings(settings_file),
        engine=text_engine,
        formula_engine=formula_engine,
    )

    with pytest.raises(RuntimeError, match="service startup failed"):
        with TestClient(app):
            pass

    assert text_engine.close_calls == 1
    assert formula_engine.close_calls == 1
