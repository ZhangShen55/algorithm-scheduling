"""
Utility Functions
工具函数
"""
import time


def get_current_timestamp_ms() -> int:
    """
    获取当前时间戳（毫秒）

    Returns:
        时间戳（毫秒）
    """
    return int(time.time() * 1000)


def format_duration(sec: int) -> str:
    """
    格式化时长

    Args:
        sec: 秒数

    Returns:
        格式化的时长字符串 (天 时:分:秒)
    """
    days = sec // 86400
    remaining = sec % 86400
    hours = remaining // 3600
    minutes = (remaining % 3600) // 60
    seconds = remaining % 60
    return f"{days}天 {hours:02d}:{minutes:02d}:{seconds:02d}"
