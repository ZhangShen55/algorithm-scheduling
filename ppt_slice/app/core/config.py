"""
Application Configuration
应用配置
"""
import os
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # Python < 3.11

from pydantic import field_validator
from pydantic_settings import BaseSettings

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG_FORMAT = (
    "%(asctime)s - %(name)s - %(levelname)s - "
    "[%(filename)s:%(lineno)d] - %(message)s"
)


def load_toml_config() -> dict:
    """
    从 config.toml 加载配置

    Returns:
        配置字典
    """
    config_file = Path(os.environ.get("CONFIG_PATH", PROJECT_ROOT / "config.toml"))
    if not config_file.is_absolute():
        config_file = (PROJECT_ROOT / config_file).resolve()

    if not config_file.exists():
        return {}

    try:
        with open(config_file, "rb") as f:
            config = tomllib.load(f)

        # 将 TOML 配置转换为环境变量格式（大写+下划线）
        flat_config = {}

        # app section
        if "app" in config:
            flat_config["APP_NAME"] = config["app"].get("name")
            flat_config["APP_VERSION"] = config["app"].get("version")
            flat_config["DEBUG"] = config["app"].get("debug")
            flat_config["HOST"] = config["app"].get("host")
            flat_config["PORT"] = config["app"].get("port")

        # task section
        if "task" in config:
            flat_config["MAX_CONCURRENT_TASKS"] = config["task"].get("max_concurrent_tasks")
            flat_config["MAX_QUEUE_SIZE"] = config["task"].get("max_queue_size")
            flat_config["MIN_FRAMES_OK"] = config["task"].get("min_frames_ok")

        # similarity section
        if "similarity" in config:
            flat_config["DEFAULT_CONTIGUOUS_SIMILARITY"] = config["similarity"].get("default_contiguous_similarity")
            flat_config["DEFAULT_SAVED_SIMILARITY"] = config["similarity"].get("default_saved_similarity")

        # video section
        if "video" in config:
            flat_config["DEFAULT_FRAME_WIDTH"] = config["video"].get("default_frame_width")
            flat_config["DEFAULT_FRAME_HEIGHT"] = config["video"].get("default_frame_height")
            flat_config["DEFAULT_FPS"] = config["video"].get("default_fps")
            flat_config["STREAM_TIMEOUT_MS"] = config["video"].get("stream_timeout_ms")
            flat_config["FRAME_QUEUE_TIMEOUT"] = config["video"].get("frame_queue_timeout")

        # paths section
        if "paths" in config:
            flat_config["RESULT_ROOT"] = config["paths"].get("result_root")

        # logging section
        if "logging" in config:
            flat_config["LOG_LEVEL"] = config["logging"].get("level")
            flat_config["LOG_DIR"] = config["logging"].get("dir")
            flat_config["LOG_FILE"] = config["logging"].get("file")
            flat_config["LOG_MAX_BYTES"] = config["logging"].get("max_bytes")
            flat_config["LOG_BACKUP_COUNT"] = config["logging"].get("backup_count")
            flat_config["LOG_FORMAT"] = config["logging"].get("format")
            flat_config["LOG_DATE_FORMAT"] = config["logging"].get("date_format")

        # 移除 None 值
        return {k: v for k, v in flat_config.items() if v is not None}

    except Exception as e:
        print(f"Warning: Failed to load config.toml: {e}")
        return {}


class Settings(BaseSettings):
    """应用配置"""

    # 应用基础配置
    APP_NAME: str = "Video PPT Slice Service"
    APP_VERSION: str = "V1.0.0_20260806"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 9001

    # 任务配置
    MAX_CONCURRENT_TASKS: int = 15  # 最大并发任务数
    MAX_QUEUE_SIZE: int = 25  # 帧队列最大缓冲大小
    MIN_FRAMES_OK: int = 5  # 最小有效帧数

    # 相似度阈值配置
    DEFAULT_CONTIGUOUS_SIMILARITY: float = 0.99  # 连续帧相似度阈值
    DEFAULT_SAVED_SIMILARITY: float = 0.98  # 保存帧相似度阈值

    # 视频处理配置
    DEFAULT_FRAME_WIDTH: int = 1920
    DEFAULT_FRAME_HEIGHT: int = 1080
    DEFAULT_FPS: int = 30
    STREAM_TIMEOUT_MS: int = 100000  # 流超时时间（毫秒）
    FRAME_QUEUE_TIMEOUT: int = 7  # 帧队列超时时间（秒）

    # 输出路径配置
    RESULT_ROOT: Path = PROJECT_ROOT / "shared_results"

    @field_validator("RESULT_ROOT", mode="before")
    @classmethod
    def resolve_result_root(cls, value):
        path = Path(value).expanduser()
        return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()

    # 日志配置
    LOG_LEVEL: str = "INFO"
    LOG_DIR: str = "./logs"
    LOG_FILE: str = "app.log"
    LOG_MAX_BYTES: int = 10 * 1024 * 1024  # 10MB
    LOG_BACKUP_COUNT: int = 5
    LOG_FORMAT: str = DEFAULT_LOG_FORMAT
    LOG_DATE_FORMAT: str = "%Y-%m-%d %H:%M:%S"

    @field_validator("LOG_FORMAT", mode="before")
    @classmethod
    def validate_log_format(cls, value):
        return value if isinstance(value, str) and "%" in value else DEFAULT_LOG_FORMAT

    class Config:
        env_file = ".env"
        case_sensitive = True


# 加载配置的优先级：
# 1. 环境变量（最高优先级）
# 2. .env 文件
# 3. config.toml 文件
# 4. 默认值（最低优先级）

# 先从 config.toml 加载
toml_config = load_toml_config()

# 显式环境变量优先于 TOML 初始化值。
for field_name in Settings.model_fields:
    if field_name in os.environ:
        toml_config.pop(field_name, None)
settings = Settings(**toml_config)
