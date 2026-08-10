"""
Pydantic Schemas
请求和响应模型
"""
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TASK_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"


class VideoPPTCutRequest(BaseModel):
    """视频PPT切片请求"""

    model_config = ConfigDict(extra="forbid")

    video_path: str = Field(..., description="远程视频URL或绝对本地视频路径")
    task_id: str = Field(..., pattern=TASK_ID_PATTERN, description="平台任务ID")
    operator_task_id: str = Field(..., pattern=TASK_ID_PATTERN, description="算子任务ID")
    result_callback_uri: str = Field(..., description="终态结果回调URI")
    threshold: float = Field(0.98, ge=0, le=1, description="相似度阈值")

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_uri(cls, value):
        if not isinstance(value, dict) or "uri" not in value:
            return value
        normalized = dict(value)
        if "video_path" in normalized:
            raise ValueError("video_path 与兼容字段 uri 不能同时提供")
        normalized["video_path"] = normalized.pop("uri")
        return normalized

    @field_validator("video_path")
    @classmethod
    def validate_video_path(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("video_path 不能为空")
        parsed = urlsplit(value)
        if parsed.scheme:
            if not parsed.netloc:
                raise ValueError("video_path 远程URL缺少主机")
            return value
        if not Path(value).is_absolute():
            raise ValueError("video_path 本地路径必须是绝对路径")
        return value

    @field_validator("task_id", "operator_task_id")
    @classmethod
    def reject_dot_identifiers(cls, value: str) -> str:
        if value in {".", ".."}:
            raise ValueError("任务 ID 不能为 . 或 ..")
        return value


class TaskAcceptedResponse(BaseModel):
    """任务受理响应。"""

    task_id: str
    operator_task_id: str
    status: int = Field(description="50=处理中，70=拒绝")
    reason: str = ""


class DynamicSegmentSchema(BaseModel):
    """疑似持续动态内容的半开时间区间。"""

    type: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    confidence: float = Field(ge=0, le=1)
    reason: str

    @model_validator(mode="after")
    def validate_interval(self):
        if self.start_ms >= self.end_ms:
            raise ValueError("动态区间必须满足 start_ms < end_ms")
        return self


class TerminalResultCallback(BaseModel):
    """一次性终态回调元数据。"""

    task_id: str
    operator_task_id: str
    status: int = Field(description="60=完成，70=失败")
    path: str
    manifest_path: str
    count: int = Field(ge=0)
    reason: str = ""
    dynamic_segments: list[DynamicSegmentSchema] = Field(default_factory=list)


class TaskStatusResponse(BaseModel):
    """任务状态响应"""

    status: str = Field(..., description="状态：success/error")
    message: dict = Field(..., description="消息内容")


class VersionResponse(BaseModel):
    """版本信息响应"""

    status: str = Field(..., description="状态：success")
    app_version: str = Field(..., alias="AppVersion", description="应用版本")
    app_start_time: str = Field(..., alias="AppStartTime", description="启动时间")
    now_time: str = Field(..., alias="NowTime", description="当前时间")
    run_time: str = Field(..., alias="RunTime", description="运行时长")
    total_fail_tasks: int = Field(..., alias="Total_Fail_Tasks", description="失败任务总数")
    total_processing_tasks: int = Field(..., alias="Total_Processing_Tasks", description="处理中任务数")
    total_have_done_process_tasks: int = Field(..., alias="Total_HaveDoneProcess_Tasks", description="已完成任务总数")

    class Config:
        populate_by_name = True


class ProcessVideoRequest(BaseModel):
    """处理视频请求"""

    video_id: str = Field(..., description="视频ID")
