from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LoggingConfig(BaseSettings):
    """文件日志合同；相对目录始终从服务项目根解析。"""

    level: str = "INFO"
    directory: str = "logs"
    file_name: str = "application.log"
    max_file_size_mib: int = Field(default=100, gt=0)
    retention_days: int = Field(default=7, gt=0)
    stdout_enabled: bool = True
    file_enabled: bool = True
    instance_id: str | None = None

    @field_validator("level")
    @classmethod
    def normalize_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}:
            raise ValueError("logging.level 不是有效的 Python 日志级别")
        return normalized


class PlatformSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PLATFORM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "algorithm-platform"
    environment: str = "development"
    log_level: str = "INFO"
    trace_header: str = "X-Trace-ID"
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    project_root: Path = Path(".")

    postgres_dsn: str = "postgresql+psycopg://algorithm:algorithm@127.0.0.1:5432/algorithm"
    kafka_bootstrap_servers: str = "127.0.0.1:9092"
    redis_url: str = "redis://127.0.0.1:6379/0"
    control_service_url: str = "http://127.0.0.1:18100"
    orchestrator_metrics_url: str = "http://127.0.0.1:18101/metrics"
    operator_registry_token: str = "local-development-registry-token"
    trusted_operator_service_urls: dict[str, str] = Field(default_factory=dict)

    course_root: Path = Path("/data/course")
    result_root: Path = Path("/data/result")

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()
