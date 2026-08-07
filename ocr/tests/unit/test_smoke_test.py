import pytest

from scripts.smoke_test import SmokeTestError, validate_prediction, validate_version


def test_validate_version_requires_legacy_fields():
    payload = {
        "status": "success",
        "AppVersion": "OCR_V3.0_PP-OCRv6",
        "AppStartTime": "2026-07-23 12:00:00",
        "NowTime": "2026-07-23 12:00:01",
        "RunTime": "0:00:01",
        "Memory usage": "100 MB",
        "GPU usage": "not used",
        "Total_RegProcess_Tasks": 0,
        "Total_DetectProcess_Tasks": 0,
    }

    validate_version(payload)

    payload.pop("AppVersion")
    with pytest.raises(SmokeTestError, match="AppVersion"):
        validate_version(payload)


def test_validate_prediction_checks_nested_json_string():
    validate_prediction(
        {
            "err_no": 0,
            "err_msg": "",
            "key": ["图片-001", "图片-002"],
            "value": [
                '[{"text":"测试","confidence":0.9,'
                '"text_region":[[0,0],[10,0],[10,10],[0,10]]}]',
                "[]",
            ],
            "formula_results": [],
        },
        expected_keys=["图片-001", "图片-002"],
    )

    with pytest.raises(SmokeTestError, match=r"value\[0\]"):
        validate_prediction(
            {
                "err_no": 0,
                "err_msg": "",
                "key": ["图片-001"],
                "value": [[]],
                "formula_results": [],
            },
            expected_keys=["图片-001"],
        )


def test_validate_prediction_checks_enabled_formula_status():
    payload = {
        "err_no": 0,
        "err_msg": "",
        "key": ["公式图片-001"],
        "value": ["[]"],
        "formula_results": [
            {
                "image_id": "公式图片-001",
                "status": "success",
                "message": "",
                "formulas": [],
            }
        ],
    }

    validate_prediction(
        payload,
        expected_keys=["公式图片-001"],
        require_formula=True,
    )

    payload["formula_results"][0]["status"] = "disabled"
    with pytest.raises(SmokeTestError, match="未启用"):
        validate_prediction(
            payload,
            expected_keys=["公式图片-001"],
            require_formula=True,
        )
