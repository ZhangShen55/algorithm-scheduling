import re
import time
from pydantic import BaseModel, Field, validator
from typing import List, Dict, Any
from typing import Optional, Literal



time_re = re.compile(r"^(\d+)-(\d+)$")

class Node(BaseModel):
    id: str
    label: str
    time: str
    children: List[Any] | None = None  # 孙节点没有 children

class SegmentResult(BaseModel):
    key_points: str                      # 10–20 字
    document_skims: Dict[str, str]       # time / overview / content
    nodes: Node                          # 顶层父节点对象

    @validator("document_skims")
    def _check_time(cls, v):
        assert time_re.match(v["time"]), "document_skims.time 格式非法"
        return v

class LessonOverview(BaseModel):
    overview: Dict[str, Any]
    # overview = {
    #   "key_points": List[str],
    #   "document_skims": List[...],
    #   "mindmap": {...},
    #   "full_overview": str
    # }
    finished_time: Optional[int] = Field(default_factory=lambda: int(time.time()))
    process_time_ms: Optional[int] = None
    finished_reason: Literal["stop", "too_long"]