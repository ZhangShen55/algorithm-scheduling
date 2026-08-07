from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.screen import ScreenBox
from app.schemas.tilt import TiltResultData


class InspectRequest(BaseModel):
    image: str = Field(..., min_length=1, description="单张图片 Base64")
    tilt_threshold: float | None = None
    conf: float | None = None
    iou: float | None = None


class InspectTiltPart(BaseModel):
    code: int
    msg: str
    cost_ms: float
    result: TiltResultData | None = None


class InspectScreenPart(BaseModel):
    code: int
    msg: str
    cost_ms: float
    primary: ScreenBox | None = None
    detections: list[ScreenBox] = Field(default_factory=list)


class InspectResponse(BaseModel):
    code: int = 200
    start_time: str
    end_time: str
    msg: str
    tilt_threshold: float
    conf: float
    iou: float
    tilt: InspectTiltPart
    screen: InspectScreenPart
