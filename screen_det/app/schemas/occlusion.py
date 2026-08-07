from __future__ import annotations

from pydantic import BaseModel, Field


class OcclusionRequest(BaseModel):
    image: str = Field(..., min_length=1, description="单张图片 Base64")
    threshold: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="YOLO 置信度阈值；不传则使用 config.toml 默认值",
    )
    area_ratio: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="遮挡面积占比判定阈值；不传则使用 config.toml 默认值",
    )


class OcclusionResponse(BaseModel):
    code: int = 200
    msg: str
    is_occluded: bool
    occlusion_area_ratio: float = Field(..., ge=0, le=1)
    score: float = Field(..., ge=0, le=1)
    threshold: float = Field(..., ge=0, le=1)
    area_ratio: float = Field(..., ge=0, le=1)
    message: str


class OcclusionErrorResponse(BaseModel):
    code: int
    msg: str
