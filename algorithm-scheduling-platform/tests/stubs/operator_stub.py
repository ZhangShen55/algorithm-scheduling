from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field


class StubExecutionRequest(BaseModel):
    task_id: str
    task_type: str
    node_code: str
    request_payload: dict[str, Any] = Field(default_factory=dict)
    effective_params: dict[str, Any] | None = None


received_calls: list[StubExecutionRequest] = []
app = FastAPI(title="milestone-2a-operator-stub")


@app.get("/health")
@app.get("/ops/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ops/metadata")
async def metadata() -> dict[str, object]:
    capabilities = json.loads(
        os.environ.get("MILESTONE_2A_STUB_CAPABILITIES", "[]")
    )
    if not isinstance(capabilities, list) or not all(
        isinstance(capability, str) for capability in capabilities
    ):
        raise RuntimeError("MILESTONE_2A_STUB_CAPABILITIES must be a JSON string list")
    return {
        "instance_id": os.environ.get("MILESTONE_2A_STUB_INSTANCE_ID"),
        "operator_code": os.environ.get("MILESTONE_2A_STUB_OPERATOR_CODE"),
        "capabilities": capabilities,
        "model_version": os.environ.get("MILESTONE_2A_STUB_MODEL_VERSION"),
        "api_version": os.environ.get("MILESTONE_2A_STUB_API_VERSION"),
    }


@app.post("/execute")
async def execute(request: StubExecutionRequest) -> dict[str, object]:
    received_calls.append(request)
    delay_seconds = float(os.environ.get("MILESTONE_2A_STUB_DELAY_SECONDS", "0"))
    if delay_seconds > 0:
        await asyncio.sleep(delay_seconds)
    return {
        "result": {
            "stub": True,
            "task_id": request.task_id,
            "task_type": request.task_type,
            "node_code": request.node_code,
            "request_payload": request.request_payload,
        },
        "effective_params": request.effective_params,
        "progress": {"completed_count": 1, "total_count": 1},
    }


@app.get("/ops/calls")
async def list_calls() -> list[dict[str, Any]]:
    return [call.model_dump(mode="json") for call in received_calls]


@app.delete("/ops/calls")
async def clear_calls() -> dict[str, int]:
    received_calls.clear()
    return {"count": 0}
