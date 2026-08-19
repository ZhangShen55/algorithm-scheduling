from __future__ import annotations

from typing import Any

import pytest

from deploy.scripts import online_gateway_smoke as smoke


def test_online_gateway_smoke_covers_real_ocr_and_both_size_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(smoke, "DECODED_MAX_BYTES", 8)
    monkeypatch.setattr(smoke, "BODY_MAX_BYTES", 16)
    observed: list[tuple[str, int]] = []

    def fake_post(
        url: str, payload: dict[str, Any], timeout: float
    ) -> tuple[int, dict[str, Any]]:
        assert url == "http://127.0.0.1:18103/api/online/ocr/recognize"
        assert timeout == 5
        image = str(payload["image"])
        observed.append((str(payload["image_id"]), len(image)))
        if payload["image_id"] == "online-ocr-smoke":
            return 200, {"code": 0, "data": {"err_no": 0}}
        return 200, {"code": 40001, "data": None}

    monkeypatch.setattr(smoke, "_post_json", fake_post)

    results = smoke.run_smoke(b"image", "http://127.0.0.1:18103", 5)

    assert [item["case_id"] for item in results] == [
        "ONLINE-OCR-001",
        "ONLINE-OCR-002",
        "ONLINE-OCR-003",
    ]
    assert [item["business_code"] for item in results] == [0, 40001, 40001]
    assert observed[1][1] > smoke.DECODED_MAX_BYTES
    assert observed[2][1] == smoke.BODY_MAX_BYTES


def test_online_gateway_smoke_fails_closed_on_wrong_business_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        smoke,
        "_post_json",
        lambda *_args, **_kwargs: (200, {"code": 50000, "data": None}),
    )

    with pytest.raises(smoke.SmokeError, match="ONLINE-OCR-001"):
        smoke.run_smoke(b"image", "http://127.0.0.1:18103", 5)
