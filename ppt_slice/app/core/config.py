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
from pydantic import Field
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

        # 将 TOML 配置转换为 Settings 字段格式（大写+下划线）
        flat_config = {}

        # app section
        if "app" in config:
            flat_config["APP_NAME"] = config["app"].get("name")
            flat_config["APP_VERSION"] = config["app"].get("version")

        # task section
        if "task" in config:
            flat_config["MAX_CONCURRENT_TASKS"] = config["task"].get("max_concurrent_tasks")
            flat_config["MAX_QUEUE_SIZE"] = config["task"].get("max_queue_size")
            flat_config["MIN_FRAMES_OK"] = config["task"].get("min_frames_ok")

        # similarity section
        if "similarity" in config:
            flat_config["DEFAULT_CONTIGUOUS_SIMILARITY"] = config["similarity"].get("default_contiguous_similarity")
            flat_config["DEFAULT_SAVED_SIMILARITY"] = config["similarity"].get("default_saved_similarity")

        # dynamic detection section
        if "dynamic_detection" in config:
            dynamic = config["dynamic_detection"]
            mappings = {
                "DYNAMIC_DETECTION_ENABLED": "enabled",
                "DYNAMIC_SAMPLE_INTERVAL_MS": "sample_interval_ms",
                "DYNAMIC_PIXEL_DIFFERENCE_THRESHOLD": "pixel_difference_threshold",
                "DYNAMIC_CHANGED_PIXEL_RATIO": "changed_pixel_ratio",
                "DYNAMIC_GRID_ROWS": "grid_rows",
                "DYNAMIC_GRID_COLUMNS": "grid_columns",
                "DYNAMIC_ACTIVE_GRID_RATIO": "active_grid_ratio",
                "DYNAMIC_WINDOW_MS": "window_ms",
                "DYNAMIC_CONFIRMATION_MS": "confirmation_ms",
                "DYNAMIC_REQUIRED_ACTIVE_RATIO": "required_active_ratio",
                "DYNAMIC_EXIT_STABLE_MS": "exit_stable_ms",
                "DYNAMIC_MERGE_GAP_MS": "merge_gap_ms",
                "DYNAMIC_CLUSTER_GAP_MS": "cluster_gap_ms",
                "DYNAMIC_CLUSTER_MIN_SEGMENTS": "cluster_min_segments",
                "DYNAMIC_OPTICAL_FLOW_ENABLED": "optical_flow_enabled",
                "DYNAMIC_OPTICAL_FLOW_WIDTH": "optical_flow_width",
                "DYNAMIC_OPTICAL_FLOW_MAGNITUDE_THRESHOLD": "optical_flow_magnitude_threshold",
                "DYNAMIC_OPTICAL_FLOW_ACTIVE_RATIO": "optical_flow_active_ratio",
                "DYNAMIC_MOTION_GRACE_MS": "motion_grace_ms",
                "DYNAMIC_CANDIDATE_STABLE_MS": "candidate_stable_ms",
            }
            for field_name, toml_name in mappings.items():
                flat_config[field_name] = dynamic.get(toml_name)

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

    # 任务配置
    MAX_CONCURRENT_TASKS: int = 15  # 最大并发任务数
    MAX_QUEUE_SIZE: int = 25  # 帧队列最大缓冲大小
    MIN_FRAMES_OK: int = 5  # 最小有效帧数

    # 相似度阈值配置
    DEFAULT_CONTIGUOUS_SIMILARITY: float = 0.99  # 连续帧相似度阈值
    DEFAULT_SAVED_SIMILARITY: float = 0.98  # 保存帧相似度阈值

    # 持续动态区间检测配置
    DYNAMIC_DETECTION_ENABLED: bool = True
    DYNAMIC_SAMPLE_INTERVAL_MS: int = Field(1000, gt=0)
    DYNAMIC_PIXEL_DIFFERENCE_THRESHOLD: int = Field(30, ge=1, le=255)
    DYNAMIC_CHANGED_PIXEL_RATIO: float = Field(0.04, ge=0, le=1)
    DYNAMIC_GRID_ROWS: int = Field(4, gt=0)
    DYNAMIC_GRID_COLUMNS: int = Field(4, gt=0)
    DYNAMIC_ACTIVE_GRID_RATIO: float = Field(0.18, ge=0, le=1)
    DYNAMIC_WINDOW_MS: int = Field(8000, gt=0)
    DYNAMIC_CONFIRMATION_MS: int = Field(5000, gt=0)
    DYNAMIC_REQUIRED_ACTIVE_RATIO: float = Field(0.7, ge=0, le=1)
    DYNAMIC_EXIT_STABLE_MS: int = Field(3000, gt=0)
    DYNAMIC_MERGE_GAP_MS: int = Field(20000, gt=0)
    DYNAMIC_CLUSTER_GAP_MS: int = Field(90000, gt=0)
    DYNAMIC_CLUSTER_MIN_SEGMENTS: int = Field(3, ge=3)
    DYNAMIC_OPTICAL_FLOW_ENABLED: bool = True
    DYNAMIC_OPTICAL_FLOW_WIDTH: int = Field(320, ge=64)
    DYNAMIC_OPTICAL_FLOW_MAGNITUDE_THRESHOLD: float = Field(0.5, gt=0)
    DYNAMIC_OPTICAL_FLOW_ACTIVE_RATIO: float = Field(0.05, ge=0, le=1)
    DYNAMIC_MOTION_GRACE_MS: int = Field(15000, gt=0)
    DYNAMIC_CANDIDATE_STABLE_MS: int = Field(2000, gt=0)

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
        case_sensitive = True


# 加载配置的优先级：
# 1. 环境变量（最高优先级）
# 2. config.toml 文件
# 3. 默认值（最低优先级）

# 先从 config.toml 加载
toml_config = load_toml_config()

# 显式环境变量优先于 TOML 初始化值。
for field_name in Settings.model_fields:
    if field_name in os.environ:
        toml_config.pop(field_name, None)
settings = Settings(**toml_config)
