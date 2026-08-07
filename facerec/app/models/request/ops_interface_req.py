# app/models/request/ops_interface_req.py
"""
运维接口请求模型
"""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import date


class APIStatsQueryRequest(BaseModel):
    """API 统计查询请求"""
    start_date: Optional[str] = Field(None, description="开始日期 YYYY-MM-DD")
    end_date: Optional[str] = Field(None, description="结束日期 YYYY-MM-DD")
    endpoint: Optional[str] = Field(None, description="具体接口路径")
    method: Optional[str] = Field(None, description="HTTP 方法")
    limit: int = Field(100, description="返回数量限制", ge=1, le=1000)
    offset: int = Field(0, description="跳过数量", ge=0)
