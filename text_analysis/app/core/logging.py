# app/core/logging.py
import logging
import os
import sys
import uuid
from logging.config import dictConfig
from contextvars import ContextVar
from app.core.config import settings

# 每个请求的 request_id（中间件里设置）
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # 日志里统一带 request_id
        record.request_id = request_id_ctx.get("-")
        return True

class HttpxMtFilter_MT(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if record.levelno <= logging.INFO and settings.MT_BASE_URL and settings.MT_BASE_URL in msg:
            return False
        return True

def setup_logging() -> None:
    """主进程初始化日志（只需要调用一次）"""
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "request_id": {
                "()": RequestIdFilter
            },
            "httpx_mt": {
                "()": HttpxMtFilter_MT
            }
        },
        "formatters": {
            "default": {
                "format": "%(asctime)s | %(levelname)s | %(name)s | req=%(request_id)s | %(message)s"
            },
            "access": {
                "format": "%(asctime)s | %(levelname)s | uvicorn.access | req=%(request_id)s | %(message)s"
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "stream": sys.stdout,
                "filters": ["request_id"],
                "formatter": "default",
                "level": level,
            },
            "access": {
                "class": "logging.StreamHandler",
                "stream": sys.stdout,
                "filters": ["request_id"],
                "formatter": "access",
                "level": level,
            }
        },
        "loggers": {
            # 你自己的应用日志
            "app": {"handlers": ["console"], "level": level, "propagate": False},
            # 统一接管 uvicorn 日志
            "uvicorn": {"handlers": ["console"], "level": level, "propagate": False},
            "uvicorn.error": {"handlers": ["console"], "level": level, "propagate": False},
            "uvicorn.access": {"handlers": ["access"], "level": level, "propagate": False},
            "httpx": {"handlers": ["console"], "level": level, "propagate": False, "filters": ["httpx_mt"]},
        },
        "root": {"handlers": ["console"], "level": level},
    })

def get_logger(name: str) -> logging.Logger:
    # 使用 app.* 命名空间，便于筛选
    if not name.startswith("app"):
        name = f"app.{name}"
    return logging.getLogger(name)

def new_request_id() -> str:
    return uuid.uuid4().hex[:16]
