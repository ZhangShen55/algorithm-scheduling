import json
from pathlib import Path

import httpx
import pytest
from orchestrator_service.app.domain.ppt_work import PptImageWork
from orchestrator_service.app.infrastructure.ppt_text import OcrAdapter, PptTextAdapterError


@pytest.mark.asyncio
async def test_ocr_adapter_preserves_real_ocr_contract(tmp_path: Path) -> None:
    image_path = tmp_path / "ppt-001.jpg"
    image_path.write_bytes(b"jpeg-content")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "err_no": 0,
                "err_msg": "",
                "key": ["ppt-001"],
                "value": [json.dumps([{"text": "第一章", "confidence": 0.99}])],
                "formula_results": [],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await OcrAdapter(client).recognize(
            "http://ocr:8000",
            PptImageWork("ppt-001", image_path, 0),
        )

    assert captured["path"] == "/ocr/prediction"
    assert captured["body"]["key"] == ["ppt-001"]
    assert len(captured["body"]["value"]) == 1
    assert result["ppt_image_id"] == "ppt-001"
    assert result["text"] == "第一章"
    assert result["ocr_response"]["err_no"] == 0


@pytest.mark.asyncio
async def test_ocr_adapter_retries_one_transient_network_error(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "ppt-001.jpg"
    image_path.write_bytes(b"jpeg-content")
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadError("", request=request)
        return httpx.Response(
            200,
            json={
                "err_no": 0,
                "err_msg": "",
                "key": ["ppt-001"],
                "value": ["[]"],
                "formula_results": [],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await OcrAdapter(
            client,
            transport_max_attempts=2,
            transport_retry_delay_seconds=0,
        ).recognize(
            "http://ocr:8000",
            PptImageWork("ppt-001", image_path, 0),
        )

    assert attempts == 2
    assert result["ppt_image_id"] == "ppt-001"


@pytest.mark.asyncio
async def test_ocr_adapter_does_not_retry_business_error(tmp_path: Path) -> None:
    image_path = tmp_path / "ppt-001.jpg"
    image_path.write_bytes(b"jpeg-content")
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            200,
            json={
                "err_no": 5001,
                "err_msg": "OCR 推理失败",
                "key": [],
                "value": [],
                "formula_results": [],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PptTextAdapterError, match="OCR 推理失败"):
            await OcrAdapter(
                client,
                transport_max_attempts=2,
                transport_retry_delay_seconds=0,
            ).recognize(
                "http://ocr:8000",
                PptImageWork("ppt-001", image_path, 0),
            )

    assert attempts == 1


@pytest.mark.asyncio
async def test_ocr_adapter_exhausted_network_error_has_non_empty_reason(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "ppt-001.jpg"
    image_path.write_bytes(b"jpeg-content")
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadError("", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(
            PptTextAdapterError,
            match="OCR 网络调用失败（ReadError）",
        ):
            await OcrAdapter(
                client,
                transport_max_attempts=2,
                transport_retry_delay_seconds=0,
            ).recognize(
                "http://ocr:8000",
                PptImageWork("ppt-001", image_path, 0),
            )

    assert attempts == 2
