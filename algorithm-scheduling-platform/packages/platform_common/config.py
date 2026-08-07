from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    postgres_dsn: str = "postgresql+psycopg://algorithm:algorithm@127.0.0.1:5432/algorithm"
    kafka_bootstrap_servers: str = "127.0.0.1:9092"
    redis_url: str = "redis://127.0.0.1:6379/0"
    control_service_url: str = "http://127.0.0.1:18100"

    course_root: Path = Path("/data/course")
    result_root: Path = Path("/data/result")

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()
