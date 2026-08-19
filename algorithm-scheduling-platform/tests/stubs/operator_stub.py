from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field


class StubExecutionRequest(BaseModel):
    task_id: str
    task_type: str
    node_code: str
    request_payload: dict[str, Any] = Field(default_factory=dict)
    effective_params: dict[str, Any] | None = None


received_calls: list[dict[str, Any]] = []
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
    received_calls.append(request.model_dump(mode="json"))
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


@app.get("/fixtures/course.mp4")
async def course_video() -> FileResponse:
    path = Path(os.environ.get("MILESTONE_2A_STUB_VIDEO_PATH", ""))
    if not path.is_file():
        raise HTTPException(status_code=404, detail="测试课程视频不存在")
    return FileResponse(path, media_type="video/mp4", filename="course.mp4")


@app.post("/v1.1.8/seacraft_asr")
async def seacraft_asr(request: Request) -> dict[str, object]:
    body = await request.body()
    if not body or "multipart/form-data" not in request.headers.get("content-type", ""):
        raise HTTPException(status_code=422, detail="ASR 请求必须是非空 multipart 表单")
    received_calls.append(
        {
            "node_code": "ASR_TRANSCRIPTION",
            "route": "/v1.1.8/seacraft_asr",
            "request_size": len(body),
        }
    )
    await _delay()
    return {
        "code": 0,
        "msg": "success",
        "language": "zh",
        "segments": [
            {
                "segment_text": "课堂内容",
                "bg": 0.0,
                "ed": 1.0,
            }
        ],
        "text": "课堂内容",
        "speed_info": {"duration": 1.0},
        "load_audio_time_ms": 1,
        "gpu_time_ms": 1,
    }


@app.post("/v1/course_overviews")
async def course_overviews(request: Request) -> dict[str, object]:
    payload = await request.json()
    received_calls.append(
        {
            "node_code": "COURSE_OVERVIEW",
            "route": "/v1/course_overviews",
            "request_payload": payload,
        }
    )
    await _delay()
    return {
        "model": "milestone-2a-stub",
        "id": "course-overview-stub",
        "result": {
            "overview": {"title": "课程"},
            "finished_time": "2026-08-19T00:00:00Z",
            "process_time_ms": 1,
            "finished_reason": "stop",
        },
        "usage": {},
    }


async def _delay() -> None:
    delay_seconds = float(os.environ.get("MILESTONE_2A_STUB_DELAY_SECONDS", "0"))
    if delay_seconds > 0:
        await asyncio.sleep(delay_seconds)


@app.get("/ops/calls")
async def list_calls() -> list[dict[str, Any]]:
    return list(received_calls)


@app.delete("/ops/calls")
async def clear_calls() -> dict[str, int]:
    received_calls.clear()
    return {"count": 0}
