import pytest
from fastapi.testclient import TestClient

from app.core.settings import load_settings
from app.main import create_app


class EmptyEngine:
    def predict(self, image):
        return []

    def close(self):
        return None


@pytest.mark.contract
def test_version_api_keeps_legacy_fields(settings_file):
    settings = load_settings(settings_file)
    with TestClient(create_app(settings=settings, engine=EmptyEngine())) as client:
        response = client.get("/ocr/getVersion")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["AppVersion"] == "OCR_TEST"
    assert body["AppStartTime"]
    assert body["NowTime"]
    assert body["RunTime"]
    assert body["Memory usage"].endswith(" MB")
    assert "GPU usage" in body
    assert body["Total_RegProcess_Tasks"] == 0
    assert body["Total_DetectProcess_Tasks"] == 0


@pytest.mark.contract
def test_version_api_shares_prediction_port(settings_file, image_base64):
    settings = load_settings(settings_file)
    with TestClient(create_app(settings=settings, engine=EmptyEngine())) as client:
        prediction = client.post(
            "/ocr/prediction",
            json={"key": ["image"], "value": [image_base64]},
        )
        version = client.get("/ocr/getVersion")

    assert prediction.status_code == 200
    assert version.status_code == 200
    assert version.json()["Total_DetectProcess_Tasks"] == 1
    assert version.json()["Total_RegProcess_Tasks"] == 1
