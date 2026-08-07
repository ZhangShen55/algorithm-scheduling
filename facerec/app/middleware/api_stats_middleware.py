# app/middleware/api_stats_middleware.py
"""
API 统计中间件
通过拦截所有 HTTP 请求来确保统计数据的准确性

数据保留策略：
- 详细日志(api_call_logs): 保留 N 天（由 config.toml 配置）
- 按小时聚合(api_stats_hourly): 保留 M 天（由 config.toml 配置）
- 使用 MongoDB TTL 索引自动清理过期数据
"""
import time
import uuid
from datetime import datetime, timedelta
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from app.core.database import db
from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger(__name__)


class APIStatsMiddleware(BaseHTTPMiddleware):
    """
    API 统计中间件

    功能：
    1. 记录每个 API 请求的详细信息
    2. 使用 MongoDB 原子操作确保并发安全
    3. 异步写入避免阻塞请求响应
    4. 排除静态资源和健康检查接口
    5. 自动清理过期数据（TTL 索引）
    """

    # 排除统计的路径前缀
    EXCLUDED_PATHS = [
        "/static/",
        "/media/",
        "/favicon.ico",
        "/docs",
        "/redoc",
        "/openapi.json"
    ]

    # 索引是否已创建
    _indexes_created = False

    async def dispatch(self, request: Request, call_next):
        """
        拦截并处理每个请求
        """
        # 确保索引已创建（只在第一次请求时创建）
        if not self._indexes_created:
            await self._ensure_indexes()
            self._indexes_created = True

        # 生成请求 ID
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        # 检查是否需要统计
        if self._should_exclude(request.url.path):
            return await call_next(request)

        # 记录请求开始时间
        start_time = time.time()

        # 提取请求信息
        method = request.method
        path = request.url.path
        client_ip = self._get_client_ip(request)

        # 执行请求
        response: Response = None
        status_code = 500
        error_message = None

        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception as e:
            logger.error(f"[APIStats] Request failed: {e}")
            error_message = str(e)
            raise
        finally:
            # 计算响应时间
            duration_ms = (time.time() - start_time) * 1000

            # 异步记录统计数据（不阻塞响应）
            try:
                await self._record_stats(
                    request_id=request_id,
                    method=method,
                    path=path,
                    status_code=status_code,
                    duration_ms=duration_ms,
                    client_ip=client_ip,
                    error_message=error_message
                )
            except Exception as e:
                # 统计记录失败不应影响正常请求
                logger.error(f"[APIStats] Failed to record stats: {e}")

        return response

    async def _ensure_indexes(self):
        """
        确保必要的索引已创建

        重要：使用 TTL 索引自动清理过期数据
        """
        try:
            # 1. api_call_logs: 按 timestamp 创建 TTL 索引
            retention_seconds = settings.stats.retention_days * 24 * 3600
            await db["api_call_logs"].create_index(
                "timestamp",
                expireAfterSeconds=retention_seconds,
                background=True
            )
            logger.info(f"[APIStats] TTL index created for api_call_logs (expire after {settings.stats.retention_days} days)")

            # 2. api_stats_hourly: 按 last_updated 创建 TTL 索引
            hourly_retention_seconds = settings.stats.hourly_retention_days * 24 * 3600
            await db["api_stats_hourly"].create_index(
                "last_updated",
                expireAfterSeconds=hourly_retention_seconds,
                background=True
            )
            logger.info(f"[APIStats] TTL index created for api_stats_hourly (expire after {settings.stats.hourly_retention_days} days)")

            # 3. 创建查询优化索引
            await db["api_call_logs"].create_index([("date", -1), ("hour", -1)], background=True)
            await db["api_stats_hourly"].create_index([("date", -1), ("hour", -1), ("endpoint", 1)], background=True)

            logger.info("[APIStats] All indexes created successfully")
        except Exception as e:
            logger.error(f"[APIStats] Failed to create indexes: {e}")

    def _should_exclude(self, path: str) -> bool:
        """
        判断路径是否应该排除统计
        """
        for excluded in self.EXCLUDED_PATHS:
            if path.startswith(excluded):
                return True
        return False

    def _get_client_ip(self, request: Request) -> str:
        """
        获取客户端真实 IP
        """
        # 优先从 X-Forwarded-For 获取
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()

        # 其次从 X-Real-IP 获取
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip

        # 最后使用直连 IP
        if request.client:
            return request.client.host

        return "unknown"

    async def _record_stats(
        self,
        request_id: str,
        method: str,
        path: str,
        status_code: int,
        duration_ms: float,
        client_ip: str,
        error_message: str = None
    ):
        """
        记录统计数据到 MongoDB

        采用两种存储策略：
        1. 详细日志（api_call_logs）：记录每次请求的完整信息（带 TTL）
        2. 聚合统计（api_stats_hourly）：按小时聚合的统计数据（带 TTL）
        """
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        hour = now.hour

        # 1. 记录详细日志（会被 TTL 索引自动清理）
        await db["api_call_logs"].insert_one({
            "request_id": request_id,
            "timestamp": now,  # TTL 索引字段
            "date": date_str,
            "hour": hour,
            "method": method,
            "path": path,
            "status_code": status_code,
            "duration_ms": round(duration_ms, 2),
            "client_ip": client_ip,
            "success": 200 <= status_code < 400,
            "error_message": error_message
        })

        # 2. 使用原子操作更新聚合统计（会被 TTL 索引自动清理）
        is_success = 200 <= status_code < 400

        await db["api_stats_hourly"].update_one(
            {
                "date": date_str,
                "hour": hour,
                "endpoint": path,
                "method": method
            },
            {
                "$inc": {
                    "total_requests": 1,
                    "success_count": 1 if is_success else 0,
                    "error_count": 0 if is_success else 1
                },
                "$push": {
                    "response_times": {
                        "$each": [round(duration_ms, 2)],
                        "$slice": -1000  # 只保留最近 1000 个响应时间
                    }
                },
                "$min": {"min_response_time_ms": round(duration_ms, 2)},
                "$max": {"max_response_time_ms": round(duration_ms, 2)},
                "$set": {
                    "last_updated": now  # TTL 索引字段
                }
            },
            upsert=True  # 如果不存在则创建
        )

        logger.debug(f"[APIStats] Recorded: {method} {path} - {status_code} - {duration_ms:.2f}ms")

