from __future__ import annotations

import base64

import pytest
from app.api.routes import create_online_gateway_app
from fastapi.testclient import TestClient


class LeaseClientMustNotBeUsed:
    def acquire(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("非法图片不得申请算子容量租约")


def _payload(path: str, encoded: str) -> dict[str, object]:
    if path.startswith("/online/vbas/"):
        return {"ImageList": [{"ImageId": "image-1", "StoragePath": encoded}]}
    if path == "/api/online/face/recognize":
        return {"photo": encoded}
    return {"image": encoded}


@pytest.mark.parametrize(
    "path",
    (
        "/online/vbas/teacher",
        "/online/vbas/student",
        "/online/vbas/person-count",
        "/api/online/face/recognize",
        "/api/online/image-quality/detect",
        "/api/online/ocr/recognize",
    ),
)
@pytest.mark.parametrize(
    "encoded",
    (
        "not-base64!",
        "data:text/plain;base64,QQ==",
        base64.b64encode(b"not-an-image").decode(),
        base64.b64encode(b"\x89PNG\r\n\x1a\ntruncated").decode(),
    ),
)
def test_invalid_online_images_are_rejected_before_capacity_lease(
    path: str,
    encoded: str,
) -> None:
    app = create_online_gateway_app()
    app.state.online_lease_client = LeaseClientMustNotBeUsed()

    with TestClient(app) as client:
        response = client.post(path, json=_payload(path, encoded))

    assert response.status_code == 200
    assert response.json()["code"] == 40001
