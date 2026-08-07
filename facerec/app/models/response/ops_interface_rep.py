# app/models/response/ops_interface_rep.py
"""
运维接口响应模型
"""
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from datetime import datetime


class APICallLogResponse(BaseModel):
    """单次 API 调用日志"""
    request_id: str
    timestamp: str
    method: str
    path: str
    status_code: int
    duration_ms: float
    client_ip: str
    success: bool
    error_message: Optional[str] = None


class APIStatsHourlyResponse(BaseModel):
    """按小时聚合的 API 统计"""
    date: str
    hour: int
    endpoint: str
    method: str
    total_requests: int
    success_count: int
    error_count: int
    success_rate: float
    avg_response_time_ms: float
    min_response_time_ms: float
    max_response_time_ms: float


class APIStatsSummaryResponse(BaseModel):
    """API 统计汇总"""
    total_requests: int
    total_success: int
    total_errors: int
    success_rate: float
    avg_response_time_ms: float
    top_endpoints: List[Dict[str, Any]]
    hourly_distribution: List[Dict[str, Any]]


class HealthCheckResponse(BaseModel):
    """健康检查响应"""
    status: str  # healthy, degraded, unhealthy
    timestamp: str
    components: Dict[str, Dict[str, Any]]


class SystemMetricsResponse(BaseModel):
    """系统指标响应"""
    system: Dict[str, float]
    application: Dict[str, Any]
