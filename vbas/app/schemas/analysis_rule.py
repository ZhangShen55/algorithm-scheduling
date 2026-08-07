# app/schemas/analysis_rule.py
# 4.1.1 AnalysisRule + AlgParams

from __future__ import annotations
from pydantic import BaseModel, Field
from typing import List, Optional
from .geometry import ImageResolution, PolygonArea

class AlgParams(BaseModel):
    ImageFormat: int
    ImageResolution: ImageResolution
    ResultImageDeclare: Optional[int] = Field(default=0, description="0:不返回,1:返回")
    ResultSavePosTxt: Optional[int] = Field(default=0, description="保存文本，0:不返回,1:返回")
    AnalysisType: Optional[int]= Field(default=0, description="分析类型")
    PolygonList: Optional[List[PolygonArea]]

class AnalysisRule(BaseModel):
    AlgParams: AlgParams
