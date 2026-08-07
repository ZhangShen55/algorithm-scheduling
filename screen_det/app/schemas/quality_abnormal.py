from __future__ import annotations

from pydantic import BaseModel, Field


class QualityAbnormalRequest(BaseModel):
    image: str = Field(..., min_length=1, description="单张图片 Base64")


class QualityAbnormalItem(BaseModel):
    type: int = Field(..., ge=1, le=4, description="1=虚焦 2=偏色 3=雪花噪点 4=花屏")
    score: float = Field(..., ge=0, le=1)
    message: str


class QualityAbnormalResponse(BaseModel):
    code: int = 200
    msg: str
    is_abnormal: bool
    abnormal_types: list[int] = Field(default_factory=list)
    results: list[QualityAbnormalItem] = Field(default_factory=list)
    message: str


class QualityAbnormalErrorResponse(BaseModel):
    code: int
    msg: str
