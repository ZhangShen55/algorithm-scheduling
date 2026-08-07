import json
from pathlib import Path

import httpx
import pytest

from services.orchestrator_service.ppt_text import KeywordAdapter, OcrAdapter
from services.orchestrator_service.ppt_work import PptImageWork


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
async def test_keyword_adapter_uses_v1_text_endpoint_and_preserves_response() -> None:
    captured: dict[str, object] = {}
    response_body = {
        "model": "qwen",
        "id": "chatcmpl-001",
        "result": {"keywords": ["函数", "映射"]},
        "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=response_body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await KeywordAdapter(client).extract(
            "http://text-analysis:8000",
            ppt_image_id="ppt-001",
            text="第一章 函数与映射",
        )

    assert captured == {
        "path": "/v1/extract_keywords",
        "body": {"text": "第一章 函数与映射"},
    }
    assert result == {"ppt_image_id": "ppt-001", "keyword_response": response_body}
