# app/services/ops_stats.py
"""
运维统计服务层
提供 API 统计数据的查询和聚合功能

数据保留策略：
- 所有查询默认天数与 config.toml 中配置的保留天数一致
- 避免查询超出保留天数范围的数据（已被 TTL 清理）
"""
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from app.core.logger import get_logger
from app.core.config import settings

logger = get_logger(__name__)


async def get_api_call_logs(
    db,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    endpoint: Optional[str] = None,
    method: Optional[str] = None,
    limit: int = 100,
    offset: int = 0
) -> List[Dict]:
    """
    获取 API 调用详细日志
    """
    query = {}

    # 构建日期范围查询
    if start_date or end_date:
        date_query = {}
        if start_date:
            date_query["$gte"] = start_date
        if end_date:
            date_query["$lte"] = end_date
        query["date"] = date_query

    # 精确匹配endpoint
    if endpoint:
        query["path"] = endpoint

    # 精确匹配method
    if method:
        query["method"] = method.upper()

    # 查询并排序
    cursor = db["api_call_logs"].find(
        query,
        {"_id": 0}  # 排除 MongoDB _id 字段
    ).sort("timestamp", -1).skip(offset).limit(limit)

    logs = await cursor.to_list(length=limit)

    # 格式化时间戳
    for log in logs:
        if isinstance(log.get("timestamp"), datetime):
            log["timestamp"] = log["timestamp"].isoformat()

    return logs


async def get_hourly_stats(
    db,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    endpoint: Optional[str] = None,
    method: Optional[str] = None,
    limit: int = 100
) -> List[Dict]:
    """
    获取按小时聚合的统计数据
    """
    query = {}

    # 日期范围
    if start_date or end_date:
        date_query = {}
        if start_date:
            date_query["$gte"] = start_date
        if end_date:
            date_query["$lte"] = end_date
        query["date"] = date_query

    # endpoint 和 method
    if endpoint:
        query["endpoint"] = endpoint
    if method:
        query["method"] = method.upper()

    # 查询数据
    cursor = db["api_stats_hourly"].find(query, {"_id": 0}).sort([("date", -1), ("hour", -1)]).limit(limit)
    stats = await cursor.to_list(length=limit)

    # 计算平均响应时间和成功率
    for stat in stats:
        response_times = stat.get("response_times", [])
        if response_times:
            stat["avg_response_time_ms"] = round(sum(response_times) / len(response_times), 2)
        else:
            stat["avg_response_time_ms"] = 0

        total = stat.get("total_requests", 0)
        success = stat.get("success_count", 0)
        stat["success_rate"] = round((success / total * 100) if total > 0 else 0, 2)

        # 移除原始响应时间数组（太大）
        stat.pop("response_times", None)
        stat.pop("last_updated", None)

    return stats


async def get_stats_summary(
    db,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> Dict[str, Any]:
    """
    获取统计汇总信息

    默认查询范围：与 hourly_retention_days 配置一致
    """
    now = datetime.now()
    if not start_date:
        # 默认查询保留天数范围内的数据
        start_date = (now - timedelta(days=settings.stats.hourly_retention_days)).strftime("%Y-%m-%d")
    if not end_date:
        end_date = now.strftime("%Y-%m-%d")

    # 聚合查询
    pipeline = [
        {
            "$match": {
                "date": {"$gte": start_date, "$lte": end_date}
            }
        },
        {
            "$group": {
                "_id": None,
                "total_requests": {"$sum": "$total_requests"},
                "total_success": {"$sum": "$success_count"},
                "total_errors": {"$sum": "$error_count"},
                "all_response_times": {"$push": "$response_times"}
            }
        }
    ]

    result = await db["api_stats_hourly"].aggregate(pipeline).to_list(length=1)

    if not result:
        return {
            "total_requests": 0,
            "total_success": 0,
            "total_errors": 0,
            "success_rate": 0,
            "avg_response_time_ms": 0,
            "top_endpoints": [],
            "hourly_distribution": []
        }

    summary = result[0]

    # 计算成功率
    total = summary["total_requests"]
    success = summary["total_success"]
    summary["success_rate"] = round((success / total * 100) if total > 0 else 0, 2)

    # 计算平均响应时间
    all_times = []
    for times_list in summary.get("all_response_times", []):
        if times_list:
            all_times.extend(times_list)

    summary["avg_response_time_ms"] = round(sum(all_times) / len(all_times), 2) if all_times else 0
    summary.pop("all_response_times", None)
    summary.pop("_id", None)

    # 获取 Top 10 访问量最大的接口
    top_endpoints_pipeline = [
        {
            "$match": {
                "date": {"$gte": start_date, "$lte": end_date}
            }
        },
        {
            "$group": {
                "_id": {"endpoint": "$endpoint", "method": "$method"},
                "total_requests": {"$sum": "$total_requests"},
                "total_errors": {"$sum": "$error_count"}
            }
        },
        {
            "$sort": {"total_requests": -1}
        },
        {
            "$limit": 10
        },
        {
            "$project": {
                "_id": 0,
                "endpoint": "$_id.endpoint",
                "method": "$_id.method",
                "total_requests": 1,
                "total_errors": 1
            }
        }
    ]

    top_endpoints = await db["api_stats_hourly"].aggregate(top_endpoints_pipeline).to_list(length=10)
    summary["top_endpoints"] = top_endpoints

    # 获取按小时的请求分布
    hourly_pipeline = [
        {
            "$match": {
                "date": {"$gte": start_date, "$lte": end_date}
            }
        },
        {
            "$group": {
                "_id": "$hour",
                "total_requests": {"$sum": "$total_requests"}
            }
        },
        {
            "$sort": {"_id": 1}
        },
        {
            "$project": {
                "_id": 0,
                "hour": "$_id",
                "total_requests": 1
            }
        }
    ]

    hourly_dist = await db["api_stats_hourly"].aggregate(hourly_pipeline).to_list(length=24)
    summary["hourly_distribution"] = hourly_dist

    return summary


async def get_database_stats(db) -> Dict[str, Any]:
    """
    获取数据库统计信息
    """
    try:
        # 获取 persons 集合统计
        total_persons = await db["persons"].count_documents({})

        # 获取今天的 API 调用次数
        today = datetime.now().strftime("%Y-%m-%d")
        pipeline = [
            {"$match": {"date": today}},
            {"$group": {"_id": None, "total": {"$sum": "$total_requests"}}}
        ]
        result = await db["api_stats_hourly"].aggregate(pipeline).to_list(length=1)
        total_requests_today = result[0]["total"] if result else 0

        return {
            "total_persons": total_persons,
            "total_requests_today": total_requests_today
        }
    except Exception as e:
        logger.error(f"[OpsStats] Failed to get database stats: {e}")
        return {
            "total_persons": 0,
            "total_requests_today": 0
        }
