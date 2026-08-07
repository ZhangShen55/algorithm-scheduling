from typing import Any

from pydantic import BaseModel, Field


class VersionResponse(BaseModel):
    status: str
    AppVersion: str
    AppStartTime: str
    NowTime: str
    RunTime: str
    memory_usage: str = Field(alias="Memory usage")
    gpu_usage: dict[str, Any] = Field(alias="GPU usage")
    Total_RegProcess_Tasks: int
    Total_DetectProcess_Tasks: int

    model_config = {"populate_by_name": True}
