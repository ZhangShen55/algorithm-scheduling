# app/schemas/response.py
from pydantic import BaseModel, ConfigDict
from typing import Optional
from .error_codes import AppErrCode
from .demographics import (
    PersonInfo,
    FaceInfo,
)

class Response(BaseModel):
    ErrCode: AppErrCode
    Desc: str

    model_config = ConfigDict(use_enum_values=True)

class AnalyzeResponse(Response):
    PersonInfo: Optional[PersonInfo]   = None
    FaceInfo:   Optional[FaceInfo]     = None



