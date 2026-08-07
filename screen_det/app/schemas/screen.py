from __future__ import annotations

from pydantic import BaseModel, Field


class ScreenBox(BaseModel):
    label: int = Field(..., ge=0, le=3, description="0=blue 1=black 2=white 3=normal")
    confidence: float
    box: list[float] = Field(..., min_length=4, max_length=4, description="[x1,y1,x2,y2] 左上+右下")


class ScreenImageResult(BaseModel):
    index: int
    cost_ms: float
    primary: ScreenBox | None = None
    detections: list[ScreenBox] = Field(default_factory=list)


class ScreenDetectResponse(BaseModel):
    code: int
    start_time: str
    end_time: str
    msg: str
    conf: float
    iou: float
    total: int
    results: list[ScreenImageResult]
