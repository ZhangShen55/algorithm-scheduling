"""
Pydantic Schemas
请求和响应模型
"""
from pydantic import BaseModel, ConfigDict, Field, field_validator

TASK_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"


class VideoPPTCutRequest(BaseModel):
    """视频PPT切片请求"""

    model_config = ConfigDict(extra="forbid")

    uri: str = Field(..., description="视频流URI")
    task_id: str = Field(..., pattern=TASK_ID_PATTERN, description="平台任务ID")
    operator_task_id: str = Field(..., pattern=TASK_ID_PATTERN, description="算子任务ID")
    result_callback_uri: str = Field(..., description="终态结果回调URI")
    threshold: float = Field(0.98, ge=0, le=1, description="相似度阈值")

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


class TerminalResultCallback(BaseModel):
    """一次性终态回调元数据。"""

    task_id: str
    operator_task_id: str
    status: int = Field(description="60=完成，70=失败")
    path: str
    manifest_path: str
    count: int = Field(ge=0)
    reason: str = ""


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
