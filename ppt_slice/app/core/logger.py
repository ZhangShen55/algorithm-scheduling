"""
Logging Configuration
统一日志配置模块
"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from app.core.config import settings


class LoggerManager:
    """日志管理器"""

    _loggers = {}

    @classmethod
    def get_logger(cls, name: str = "app") -> logging.Logger:
        """
        获取日志记录器

        Args:
            name: 日志记录器名称

        Returns:
            logging.Logger: 日志记录器实例
        """
        if name in cls._loggers:
            return cls._loggers[name]

        logger = logging.getLogger(name)
        logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper()))

        # 避免重复添加处理器
        if logger.handlers:
            return logger

        # 创建日志目录
        log_dir = Path(settings.LOG_DIR)
        log_dir.mkdir(parents=True, exist_ok=True)

        # 控制台处理器
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter(
            fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt=settings.LOG_DATE_FORMAT
        )
        console_handler.setFormatter(console_formatter)

        # 文件处理器（所有日志）
        file_handler = RotatingFileHandler(
            filename=log_dir / settings.LOG_FILE,
            maxBytes=settings.LOG_MAX_BYTES,
            backupCount=settings.LOG_BACKUP_COUNT,
            encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(
            fmt=settings.LOG_FORMAT,
            datefmt=settings.LOG_DATE_FORMAT
        )
        file_handler.setFormatter(file_formatter)

        # 错误日志文件处理器
        error_handler = RotatingFileHandler(
            filename=log_dir / "error.log",
            maxBytes=settings.LOG_MAX_BYTES,
            backupCount=settings.LOG_BACKUP_COUNT,
            encoding="utf-8"
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(file_formatter)

        # 添加处理器
        logger.addHandler(console_handler)
        logger.addHandler(file_handler)
        logger.addHandler(error_handler)

        cls._loggers[name] = logger
        return logger


# 创建默认日志记录器
logger = LoggerManager.get_logger("app")


def get_logger(name: str = "app") -> logging.Logger:
    """
    获取日志记录器的便捷函数

    Args:
        name: 日志记录器名称

    Returns:
        logging.Logger: 日志记录器实例
    """
    return LoggerManager.get_logger(name)
