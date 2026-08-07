from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.quality_abnormal import QualityAbnormalItem
from app.schemas.screen import ScreenBox
from app.schemas.tilt import TiltResultData


AggregateModule = Literal["tilt", "screen", "quality_abnormal", "occlusion"]
ProblemType = Literal["tilt", "screen", "quality_abnormal", "occlusion"]
VALID_MODULES = ("tilt", "screen", "quality_abnormal", "occlusion")


class AggregateDetectRequest(BaseModel):
    image: str = Field(..., min_length=1, description="单张图片 Base64")
    tilt_threshold: float | None = Field(default=None, ge=0)
    screen_conf: float | None = Field(default=None, ge=0, le=1)
    screen_iou: float | None = Field(default=None, ge=0, le=1)
    occlusion_threshold: float | None = Field(default=None, ge=0, le=1)
    occlusion_area_ratio: float | None = Field(default=None, ge=0, le=1)
    include: list[AggregateModule] | None = None

    @field_validator("include")
    @classmethod
    def include_must_not_have_duplicates(
        cls, value: list[AggregateModule] | None
    ) -> list[AggregateModule] | None:
        if value is None:
            return None
        seen: set[str] = set()
        result: list[AggregateModule] = []
        for item in value:
            if item not in seen:
                seen.add(item)
                result.append(item)
        return result


class AggregateEffectiveParams(BaseModel):
    tilt_threshold: float
    screen_conf: float
    screen_iou: float
    occlusion_threshold: float
    occlusion_area_ratio: float
    include: list[AggregateModule]
    device: str


class AggregateTiltPart(BaseModel):
    code: int
    msg: str
    cost_ms: float
    result: TiltResultData | None = None


class AggregateScreenPart(BaseModel):
    code: int
    msg: str
    cost_ms: float
    primary: ScreenBox | None = None
    detections: list[ScreenBox] = Field(default_factory=list)


class AggregateQualityAbnormalPart(BaseModel):
    code: int = 200
    msg: str
    cost_ms: float
    is_abnormal: bool
    abnormal_types: list[int] = Field(default_factory=list)
    results: list[QualityAbnormalItem] = Field(default_factory=list)
    message: str


class AggregateOcclusionPart(BaseModel):
    code: int = 200
    msg: str
    cost_ms: float
    is_occluded: bool
    occlusion_area_ratio: float = Field(..., ge=0, le=1)
    score: float = Field(..., ge=0, le=1)
    threshold: float = Field(..., ge=0, le=1)
    area_ratio: float = Field(..., ge=0, le=1)
    message: str


class AggregateErrorPart(BaseModel):
    code: int = 500
    msg: str
    cost_ms: float


class AggregateDetectResponse(BaseModel):
    code: int = 200
    msg: str
    start_time: str
    end_time: str
    cost_ms: float
    executed_modules: list[AggregateModule]
    failed_modules: list[AggregateModule] = Field(default_factory=list)
    effective_params: AggregateEffectiveParams
    problem_types: list[ProblemType] = Field(default_factory=list)
    tilt: AggregateTiltPart | AggregateErrorPart | None = None
    screen: AggregateScreenPart | AggregateErrorPart | None = None
    quality_abnormal: AggregateQualityAbnormalPart | AggregateErrorPart | None = None
    occlusion: AggregateOcclusionPart | AggregateErrorPart | None = None


class AggregateErrorResponse(BaseModel):
    code: int
    msg: str
