from __future__ import annotations

from pydantic import BaseModel, Field


class TiltDetectRequest(BaseModel):
    images: str = Field(..., min_length=1, description="Base64 encoded image")


class TiltResultData(BaseModel):
    is_tilted: bool
    angle: float
    cost_ms: float


class TiltResponse(BaseModel):
    code: int
    start_time: str
    end_time: str
    msg: str
    tilt_threshold: float
    result: TiltResultData


class ErrorResponse(BaseModel):
    code: int
    msg: str
