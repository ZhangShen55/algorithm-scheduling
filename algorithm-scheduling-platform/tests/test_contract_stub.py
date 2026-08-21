from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from orchestrator_service.app.infrastructure.contract_stub import (
    ContractStubAdapter,
    NodeExecutionContext,
)

from tests.stubs.operator_stub import app, received_calls


def test_standalone_stub_exposes_real_health_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_contract_adapter_sends_full_node_context_and_reads_result() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "result": {"text": "Stub 转写结果", "segments": []},
                "artifact_path": None,
                "artifact_count": None,
                "progress": {"completed_count": 1, "total_count": 1},
                "effective_params": {"showSpk": True},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = ContractStubAdapter(client)
        result = await adapter.execute(
            "http://stub.local",
            NodeExecutionContext(
                task_id="course-001",
                task_type="ASR",
                node_code="ASR_TRANSCRIPTION",
                request_payload={"teacher_video_path": "http://media/teacher.mp4"},
                effective_params={"showSpk": True},
            ),
        )

    assert captured == {
        "task_id": "course-001",
        "task_type": "ASR",
        "node_code": "ASR_TRANSCRIPTION",
        "request_payload": {"teacher_video_path": "http://media/teacher.mp4"},
        "effective_params": {"showSpk": True},
    }
    assert result.result == {"text": "Stub 转写结果", "segments": []}
    assert result.progress == {"completed_count": 1, "total_count": 1}
    assert result.effective_params == {"showSpk": True}


def test_standalone_stub_records_complete_calls_and_returns_structured_result() -> None:
    received_calls.clear()
    with TestClient(app) as client:
        response = client.post(
            "/execute",
            json={
                "task_id": "course-002",
                "task_type": "ASR",
                "node_code": "ASR_TRANSCRIPTION",
                "request_payload": {"teacher_video_path": "http://media/teacher.mp4"},
                "effective_params": {"showEmotion": True},
            },
        )
        calls = client.get("/ops/calls")

    assert response.status_code == 200
    assert response.json()["result"]["node_code"] == "ASR_TRANSCRIPTION"
    assert response.json()["effective_params"] == {"showEmotion": True}
    assert calls.json() == [
        {
            "task_id": "course-002",
            "task_type": "ASR",
            "node_code": "ASR_TRANSCRIPTION",
            "request_payload": {"teacher_video_path": "http://media/teacher.mp4"},
            "effective_params": {"showEmotion": True},
        }
    ]


@pytest.mark.asyncio
async def test_contract_adapter_propagates_persisted_submission_id() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"result": {}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = ContractStubAdapter(client)
        await adapter.execute(
            "http://stub.local",
            NodeExecutionContext(
                task_id="course-003",
                task_type="ASR",
                node_code="ASR_TRANSCRIPTION",
                request_payload={"teacher_video_path": "http://media/teacher.mp4"},
                effective_params={},
                submission_id="submission-real-001",
            ),
        )

    assert captured["submission_id"] == "submission-real-001"
