# app/schemas/geometry.py
# 4.1.2 PolygonArea, 4.1.8 ROI, 4.1.9 Point, 4.1.10 ImageResolution
from pydantic import BaseModel, Field
from typing import List, Optional,Union

class Point(BaseModel):
    X: int
    Y: int


class PolygonArea(BaseModel):
    Enable: Optional[bool] = Field(
        default=None,
        description="True 表示该规则多边形区域有效，False 表示该规则多边形区域无效"
    )

    Label: Union[str, int]
    Points: List[Point]

class ROI(BaseModel):
    LeftTopX: int
    LeftTopY: int
    RightBtmX: int
    RightBtmY: int


class ImageResolution(BaseModel):
    ImageWidth: int
    ImageHeight: int
