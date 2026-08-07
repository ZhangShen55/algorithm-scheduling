# app/schemas/task.py
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import List, Optional,Union
from .image import Image
from .analysis_rule import AnalysisRule
from .demographics import (
    PersonInfo,
    FaceInfo,
)
from .response import Response

class TaskInfo(BaseModel):
    TaskID: Optional[str] = Field(default=None, description="任务编号，由 IAS 服务生成")
    TaskType: Optional[int] = Field(default=4, description="检测+分析类型，固定值 4")
    ImageList: Optional[List[Image]] = Field(
        default=None,
        description="图像列表"
    )
    AnalysisRule: AnalysisRule
    RunAlways: Optional[bool] = Field(
        default=None,
        description="任务类型标识；false=短任务（同步），true=长任务（异步）"
    )
    RecvAddr: Optional[str] = Field(
        default=None,
        description="回调地址，异步任务时，接收结果的地址"
    )

class PolygonResult(BaseModel):
    Label: Optional[Union[str, int]]
    PersonInfo: Optional[PersonInfo]
    FaceInfo: Optional[FaceInfo]

class KedaPersonObject(BaseModel):
    Image: Optional[Image]
    PolygonResult: Optional[List[PolygonResult]]

class KedaPersonObjectWrapper(BaseModel):
    KedaPersonObject: KedaPersonObject

class SyncTasksResponse(BaseModel):
    Response: Response
    TaskID: str
    FreeCapacity: int
    TaskResult: List[KedaPersonObjectWrapper]

