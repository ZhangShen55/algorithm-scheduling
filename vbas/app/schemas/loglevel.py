from typing import List, Optional
from pydantic import BaseModel
from .response import Response

class LogLevelInfo(BaseModel):
    Level: str
    AllLevel: Optional[List[str]]

class LogLevelResponse(BaseModel):
    Response: Response
    LogLevel: LogLevelInfo

class LogLevelUpdate(BaseModel):
    Level: str
