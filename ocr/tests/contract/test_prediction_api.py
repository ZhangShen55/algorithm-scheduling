import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.settings import load_settings
from app.engines.base import EngineResult
from app.main import create_app


class StubEngine:
    def __init__(self, results=None, error: Exception | None = None):
        self.results = results or []
        self.error = error
        self.closed = False

    def predict(self, image):
        if self.error:
            raise self.error
        return self.results

    def close(self):
        self.closed = True


class StubFormulaEngine:
    def __init__(self, outcomes=None):
        self.outcomes = list(outcomes or [])
        self.calls = 0
        self.closed = False

    def predict(self, image):
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def close(self):
        self.closed = True


def formula_result(latex=r"\frac{a}{b}"):
    return SimpleNamespace(
        latex=latex,
        formula_region=[[10, 20], [100, 20], [100, 50], [10, 50]],
        detection_confidence=0.96,
    )


def enable_formula(settings_file):
    (settings_file.parent / "models" / "PP-DocLayout_plus-L").mkdir()
    (settings_file.parent / "models" / "PP-FormulaNet_plus-M").mkdir()
    content = settings_file.read_text(encoding="utf-8")
    settings_file.write_text(
        content.replace("enabled = false", "enabled = true"),
        encoding="utf-8",
    )


def build_client(
    settings_file,
    results=None,
    error=None,
    formula_engine=None,
):
    settings = load_settings(settings_file)
    engine = StubEngine(results=results, error=error)
    return TestClient(
        create_app(
            settings=settings,
            engine=engine,
            formula_engine=formula_engine,
        )
    )


@pytest.mark.contract
def test_prediction_returns_results_for_the_requested_image_id(
    settings_file, image_base64
):
    results = [
        EngineResult(
            text="示例",
            confidence=0.99,
            text_region=[[0, 0], [100, 0], [100, 30], [0, 30]],
        )
    ]
    with build_client(settings_file, results=results) as client:
        response = client.post(
            "/ocr/prediction",
            json={"key": ["业务图片-001"], "value": [image_base64]},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["err_no"] == 0
    assert body["err_msg"] == ""
    assert body["key"] == ["业务图片-001"]
    assert isinstance(body["value"], list)
    assert len(body["value"]) == 1
    assert isinstance(body["value"][0], str)
    assert body["formula_results"] == []

    payload = json.loads(body["value"][0])
    assert payload == [
        {
            "text": "示例",
            "confidence": 0.99,
            "text_region": [[0, 0], [100, 0], [100, 30], [0, 30]],
        }
    ]
    assert isinstance(payload[0]["confidence"], float)
    assert all(isinstance(value, int) for point in payload[0]["text_region"] for value in point)


@pytest.mark.contract
@pytest.mark.parametrize(
    ("enable_formula", "expected_status"),
    [(None, None), (False, None), (True, "disabled")],
)
def test_formula_fields_are_backward_compatible(
    settings_file, image_base64, enable_formula, expected_status
):
    request = {"key": ["image"], "value": [image_base64]}
    if enable_formula is not None:
        request["enable_formula"] = enable_formula

    with build_client(settings_file) as client:
        response = client.post("/ocr/prediction", json=request)

    assert response.status_code == 200
    if expected_status is None:
        assert response.json()["formula_results"] == []
    else:
        assert response.json()["formula_results"] == [
            {
                "image_id": "image",
                "status": expected_status,
                "message": "服务端未启用公式识别功能",
                "formulas": [],
            }
        ]
    assert response.json()["value"] == ["[]"]


@pytest.mark.contract
def test_prediction_returns_formula_results_without_changing_ocr_contract(
    settings_file, image_base64
):
    enable_formula(settings_file)
    formula_engine = StubFormulaEngine([[formula_result()]])

    with build_client(settings_file, formula_engine=formula_engine) as client:
        response = client.post(
            "/ocr/prediction",
            json={
                "key": ["公式图片-001"],
                "value": [image_base64],
                "enable_formula": True,
            },
        )

    assert response.json() == {
        "err_no": 0,
        "err_msg": "",
        "key": ["公式图片-001"],
        "value": ["[]"],
        "formula_results": [
            {
                "image_id": "公式图片-001",
                "status": "success",
                "message": "",
                "formulas": [
                    {
                        "latex": r"\frac{a}{b}",
                        "formula_region": [
                            [10, 20],
                            [100, 20],
                            [100, 50],
                            [10, 50],
                        ],
                        "detection_confidence": 0.96,
                    }
                ],
            }
        ],
    }


@pytest.mark.contract
def test_formula_failure_is_isolated_from_ocr_and_other_images(
    settings_file, image_base64
):
    enable_formula(settings_file)
    formula_engine = StubFormulaEngine(
        [[formula_result("a+b")], RuntimeError("secret /model/path")]
    )

    with build_client(settings_file, formula_engine=formula_engine) as client:
        response = client.post(
            "/ocr/prediction",
            json={
                "key": ["图片-001", "图片-002"],
                "value": [image_base64, image_base64],
                "enable_formula": True,
            },
        )

    body = response.json()
    assert body["err_no"] == 0
    assert body["key"] == ["图片-001", "图片-002"]
    assert body["value"] == ["[]", "[]"]
    assert [item["status"] for item in body["formula_results"]] == [
        "success",
        "error",
    ]
    assert body["formula_results"][1]["message"] == "公式识别失败"
    assert "secret" not in response.text
    assert "/model/path" not in response.text


@pytest.mark.contract
def test_prediction_accepts_multiple_ids_and_data_url(settings_file, image_base64):
    with build_client(settings_file) as client:
        response = client.post(
            "/ocr/prediction",
            json={
                "key": ["图片-001", "图片-002"],
                "value": [
                    image_base64,
                    f"data:image/png;base64,{image_base64}",
                ],
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "err_no": 0,
        "err_msg": "",
        "key": ["图片-001", "图片-002"],
        "value": ["[]", "[]"],
        "formula_results": [],
    }


@pytest.mark.contract
def test_prediction_returns_empty_json_string_when_no_text(settings_file, image_base64):
    with build_client(settings_file) as client:
        response = client.post(
            "/ocr/prediction",
            json={"key": ["image"], "value": [image_base64]},
        )

    assert response.json() == {
        "err_no": 0,
        "err_msg": "",
        "key": ["image"],
        "value": ["[]"],
        "formula_results": [],
    }


@pytest.mark.contract
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"key": [], "value": []},
        {"key": ["image"], "value": []},
        {"key": ["image"], "value": ["value", "value"]},
        {"key": [""], "value": ["value"]},
    ],
)
def test_invalid_request_uses_legacy_error_shape(settings_file, payload):
    with build_client(settings_file) as client:
        response = client.post("/ocr/prediction", json=payload)

    body = response.json()
    assert response.status_code == 200
    assert body["err_no"] != 0
    assert body["err_msg"]
    assert body["key"] == ["results"]
    assert body["value"] == ["[]"]
    assert body["formula_results"] == []
    assert "detail" not in body


@pytest.mark.contract
def test_prediction_rejects_string_formula_flag(settings_file, image_base64):
    with build_client(settings_file) as client:
        response = client.post(
            "/ocr/prediction",
            json={
                "key": ["image"],
                "value": [image_base64],
                "enable_formula": "true",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "err_no": 4000,
        "err_msg": "请求格式错误",
        "key": ["results"],
        "value": ["[]"],
        "formula_results": [],
    }


@pytest.mark.contract
def test_invalid_base64_uses_legacy_error_shape(settings_file):
    with build_client(settings_file) as client:
        response = client.post(
            "/ocr/prediction",
            json={"key": ["image"], "value": ["not-base64"]},
        )

    body = response.json()
    assert response.status_code == 200
    assert body["err_no"] != 0
    assert "图片" in body["err_msg"]
    assert body["value"] == ["[]"]


@pytest.mark.contract
def test_engine_error_does_not_expose_internal_details(settings_file, image_base64):
    with build_client(
        settings_file,
        error=RuntimeError("secret /private/model/path traceback"),
    ) as client:
        response = client.post(
            "/ocr/prediction",
            json={"key": ["image"], "value": [image_base64]},
        )

    body = response.json()
    assert response.status_code == 200
    assert body["err_no"] != 0
    assert body["err_msg"] == "OCR 推理失败"
    assert "secret" not in response.text
    assert "/private/model/path" not in response.text
