import json
from pathlib import Path

import httpx
import pytest
from orchestrator_service.app.domain.ppt_work import PptImageWork
from orchestrator_service.app.infrastructure.ppt_text import OcrAdapter


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
