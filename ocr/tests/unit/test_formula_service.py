from types import SimpleNamespace

from PIL import Image

from app.services.formula_service import FormulaService


class RecordingFormulaEngine:
    def __init__(self, outcomes=None):
        self.outcomes = list(outcomes or [])
        self.calls = 0
        self.closed = False
        self.close_calls = 0

    def predict(self, image):
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def close(self):
        self.closed = True
        self.close_calls += 1


def formula(latex="E=mc^{2}", confidence=0.96):
    return SimpleNamespace(
        latex=latex,
        formula_region=[[10, 20], [100, 20], [100, 50], [10, 50]],
        detection_confidence=confidence,
    )


def images(count):
    return [Image.new("RGB", (20, 20), "white") for _ in range(count)]


def test_formula_service_skips_inference_when_request_is_disabled():
    engine = RecordingFormulaEngine([[formula()]])
    service = FormulaService(configured_enabled=True, engine=engine)

    result = service.predict(False, ["图片-001"], images(1))

    assert result == []
    assert engine.calls == 0


def test_formula_service_reports_disabled_for_each_image():
    service = FormulaService(configured_enabled=False)

    result = service.predict(True, ["图片-001", "图片-002"], images(2))

    assert result == [
        {
            "image_id": "图片-001",
            "status": "disabled",
            "message": "服务端未启用公式识别功能",
            "formulas": [],
        },
        {
            "image_id": "图片-002",
            "status": "disabled",
            "message": "服务端未启用公式识别功能",
            "formulas": [],
        },
    ]


def test_formula_service_returns_grouped_results_in_image_order():
    engine = RecordingFormulaEngine([[formula()], []])
    service = FormulaService(configured_enabled=True, engine=engine)

    result = service.predict(True, ["图片-001", "图片-002"], images(2))

    assert result == [
        {
            "image_id": "图片-001",
            "status": "success",
            "message": "",
            "formulas": [
                {
                    "latex": "E=mc^{2}",
                    "formula_region": [
                        [10, 20],
                        [100, 20],
                        [100, 50],
                        [10, 50],
                    ],
                    "detection_confidence": 0.96,
                }
            ],
        },
        {
            "image_id": "图片-002",
            "status": "success",
            "message": "",
            "formulas": [],
        },
    ]
    assert engine.calls == 2


def test_formula_service_isolates_each_image_failure():
    engine = RecordingFormulaEngine(
        [[formula("a+b")], RuntimeError("secret model path"), [formula("c+d")]]
    )
    service = FormulaService(configured_enabled=True, engine=engine)

    result = service.predict(
        True,
        ["图片-001", "图片-002", "图片-003"],
        images(3),
    )

    assert [item["status"] for item in result] == ["success", "error", "success"]
    assert result[1] == {
        "image_id": "图片-002",
        "status": "error",
        "message": "公式识别失败",
        "formulas": [],
    }
    assert "secret" not in str(result)
    assert engine.calls == 3


def test_formula_service_closes_configured_engine():
    engine = RecordingFormulaEngine([])
    service = FormulaService(configured_enabled=True, engine=engine)

    service.close()

    assert engine.closed is True


def test_formula_service_close_is_idempotent():
    engine = RecordingFormulaEngine([])
    service = FormulaService(configured_enabled=True, engine=engine)

    service.close()
    service.close()

    assert engine.close_calls == 1
