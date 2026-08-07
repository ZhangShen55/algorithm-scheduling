from pydantic import BaseModel, Field
from .response import Response

class VersionInfo(BaseModel):
    AppID: str
    AppVer: str
    AdpVer: str
    AlgVer: str
    AppStartTime: str
    NowTime: str
    RunTime: str
    TotalTasks: str

class VersionResponse(BaseModel):
    Response: Response
    Version: VersionInfo
