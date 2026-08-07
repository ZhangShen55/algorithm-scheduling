# app/router/ops.py
"""
运维管理接口路由
提供系统监控、统计查询等功能
"""
from typing import List
from fastapi import APIRouter, Query, HTTPException
from datetime import datetime
import psutil

from app.core.database import db
from app.core.logger import get_logger
from app.services import ops_stats
from app.models.response.ops_interface_rep import (
    APICallLogResponse,
    APIStatsHourlyResponse,
    APIStatsSummaryResponse,
    HealthCheckResponse,
    SystemMetricsResponse
)

logger = get_logger(__name__)

router = APIRouter(prefix="/ops", tags=["Operations & Monitoring"])


@router.get("/health", response_model=HealthCheckResponse)
async def health_check():
    """
    健康检查接口
    检查系统各组件的健康状态
    """
    components = {}

    # 1. 检查数据库连接
    try:
        await db.command("ping")
        db_latency_start = datetime.now()
        await db["persons"].find_one()
        db_latency = (datetime.now() - db_latency_start).total_seconds() * 1000
        components["database"] = {
            "status": "up",
            "latency_ms": round(db_latency, 2)
        }
    except Exception as e:
        logger.error(f"[Health] Database check failed: {e}")
        components["database"] = {
            "status": "down",
            "error": str(e)
        }

    # 2. 检查存储空间
    try:
        disk = psutil.disk_usage('/')
        components["storage"] = {
            "status": "up",
            "disk_usage_percent": round(disk.percent, 2),
            "disk_free_gb": round(disk.free / (1024**3), 2)
        }
    except Exception as e:
        logger.error(f"[Health] Storage check failed: {e}")
        components["storage"] = {
            "status": "unknown",
            "error": str(e)
        }

    # 判断整体状态
    all_up = all(c.get("status") == "up" for c in components.values())
    overall_status = "healthy" if all_up else "degraded"

    return {
        "status": overall_status,
        "timestamp": datetime.now().isoformat(),
        "components": components
    }


@router.get("/metrics", response_model=SystemMetricsResponse)
async def get_system_metrics():
    """
    获取系统运行指标
    """
    try:
        # 系统指标
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')

        system_metrics = {
            "cpu_percent": round(cpu_percent, 2),
            "memory_percent": round(memory.percent, 2),
            "memory_used_gb": round(memory.used / (1024**3), 2),
            "memory_total_gb": round(memory.total / (1024**3), 2),
            "disk_usage_percent": round(disk.percent, 2),
            "disk_free_gb": round(disk.free / (1024**3), 2)
        }

        # 应用指标
        db_stats = await ops_stats.get_database_stats(db)

        return {
            "system": system_metrics,
            "application": db_stats
        }
    except Exception as e:
        logger.error(f"[Metrics] Failed to get metrics: {e}")
        raise HTTPException(status_code=500, detail=f"获取指标失败: {str(e)}")


@router.get("/stats/api-calls", response_model=List[APICallLogResponse])
async def get_api_call_logs(
    start_date: str = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: str = Query(None, description="结束日期 YYYY-MM-DD"),
    endpoint: str = Query(None, description="具体接口路径"),
    method: str = Query(None, description="HTTP 方法"),
    limit: int = Query(100, description="返回数量", ge=1, le=1000),
    offset: int = Query(0, description="跳过数量", ge=0)
):
    """
    获取 API 调用详细日志

    示例：
    - GET /ops/stats/api-calls?start_date=2026-01-01&end_date=2026-01-07
    - GET /ops/stats/api-calls?endpoint=/persons&method=POST
    """
    try:
        logs = await ops_stats.get_api_call_logs(
            db=db,
            start_date=start_date,
            end_date=end_date,
            endpoint=endpoint,
            method=method,
            limit=limit,
            offset=offset
        )
        return logs
    except Exception as e:
        logger.error(f"[Stats] Failed to get API call logs: {e}")
        raise HTTPException(status_code=500, detail=f"查询失败: {str(e)}")


@router.get("/stats/hourly", response_model=List[APIStatsHourlyResponse])
async def get_hourly_stats(
    start_date: str = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: str = Query(None, description="结束日期 YYYY-MM-DD"),
    endpoint: str = Query(None, description="具体接口路径"),
    method: str = Query(None, description="HTTP 方法"),
    limit: int = Query(100, description="返回数量", ge=1, le=1000)
):
    """
    获取按小时聚合的 API 统计数据

    示例：
    - GET /ops/stats/hourly?start_date=2026-01-07
    - GET /ops/stats/hourly?endpoint=/persons/search
    """
    try:
        stats = await ops_stats.get_hourly_stats(
            db=db,
            start_date=start_date,
            end_date=end_date,
            endpoint=endpoint,
            method=method,
            limit=limit
        )
        return stats
    except Exception as e:
        logger.error(f"[Stats] Failed to get hourly stats: {e}")
        raise HTTPException(status_code=500, detail=f"查询失败: {str(e)}")


@router.get("/stats/summary", response_model=APIStatsSummaryResponse)
async def get_stats_summary(
    start_date: str = Query(None, description="开始日期 YYYY-MM-DD，默认7天前"),
    end_date: str = Query(None, description="结束日期 YYYY-MM-DD，默认今天")
):
    """
    获取 API 统计汇总信息

    包括：
    - 总请求数、成功数、失败数、成功率
    - 平均响应时间
    - Top 10 访问量最大的接口
    - 按小时的请求分布

    示例：
    - GET /ops/stats/summary
    - GET /ops/stats/summary?start_date=2026-01-01&end_date=2026-01-07
    """
    try:
        summary = await ops_stats.get_stats_summary(
            db=db,
            start_date=start_date,
            end_date=end_date
        )
        return summary
    except Exception as e:
        logger.error(f"[Stats] Failed to get summary: {e}")
        raise HTTPException(status_code=500, detail=f"查询失败: {str(e)}")
