import logging
import os
import sys
import uuid
from pathlib import Path
from logging.config import dictConfig
from contextvars import ContextVar
from app.core.config import settings

LEVEL = settings.logger.level
LOG_PATH = settings.logger.log_path

# 每个请求的 request_id（中间件里设置）
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get("-")
        return True

def setup_logging() -> None:
    """主进程初始化日志"""
    level = LEVEL.upper()
    filename = LOG_PATH
    if filename:
        # 初始化处理程序之前，确保日志目录存在
        Path(filename).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "request_id": {
                "()": RequestIdFilter
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
            },
            "log_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "filename": filename,
                "mode": "w",
                "maxBytes": 50 * 1024 * 1024,
                "backupCount": 5,
                "filters": ["request_id"],
                "formatter": "default",
                "level": level,
            }
        },
        "loggers": {
            # 你自己的应用日志
            "app": {"handlers": ["console", "log_file"], "level": level, "propagate": False},
            # 统一接管 uvicorn 日志
            "uvicorn": {"handlers": ["console"], "level": level, "propagate": False},
            "uvicorn.error": {"handlers": ["console"], "level": level, "propagate": False},
            "uvicorn.access": {"handlers": ["access"], "level": level, "propagate": False},
        },
        "root": {"handlers": ["console"], "level": level},
    })




_init_lock = logging._lock #复用logging模块自带RLock
_initialized = False

def _ensure_setup():
    """线程安全的单次初始化"""
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if not _initialized:
            setup_logging()
            _initialized = True

def get_logger(name: str | None = None) -> logging.Logger:
    """
    自动完成 logging 配置并返回 logger。
    usage：
        from config import get_logger
        logger = get_logger(__name__)
    """
    _ensure_setup()
    if name is None:
        name = "app"
    if not name.startswith("app"):
        name = f"app.{name}"
    return logging.getLogger(name)

def new_request_id() -> str:
    return uuid.uuid4().hex[:16]

setup_logging()
logger = get_logger(__name__)
logger.info("日志等级设置为:%s, 日志文件位置:%s", LEVEL, LOG_PATH)