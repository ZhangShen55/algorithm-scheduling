from __future__ import annotations

import os
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

SERVICE_ROOT = Path(__file__).resolve().parents[2]
_config_path_override: ContextVar[Path | None] = ContextVar(
    "control_config_path_override",
    default=None,
)


class ServiceConfig(BaseModel):
    name: str = "control-service"
    host: str = "0.0.0.0"
    port: int = Field(default=18100, ge=1, le=65535)
    workers: Literal[1] = 1
    environment: str = "development"
    log_level: str = "INFO"
    trace_header: str = "X-Trace-ID"

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()


class PostgresConfig(BaseModel):
    dsn: str = "postgresql+psycopg://algorithm:algorithm@127.0.0.1:5432/algorithm"
    pool_size: int = Field(default=10, ge=1)
    max_overflow: int = Field(default=20, ge=0)
    pool_timeout_seconds: float = Field(default=30.0, gt=0)
    pool_pre_ping: bool = True


class RedisConfig(BaseModel):
    url: str = "redis://127.0.0.1:6379/0"
    key_prefix: str = "algorithm-platform:"
    heartbeat_ttl_seconds: int = Field(default=15, gt=0)
    max_connections: int = Field(default=50, ge=1)
    socket_connect_timeout_seconds: float = Field(default=3.0, gt=0)
    socket_timeout_seconds: float = Field(default=3.0, gt=0)


class OperatorRegistryConfig(BaseModel):
    default_lease_ttl_seconds: int = Field(default=60, gt=0)
    max_lease_ttl_seconds: int = Field(default=3600, gt=0)
    heartbeat_audit_interval_seconds: int = Field(default=60, gt=0)


class FeatureConfig(BaseModel):
    enabled_task_types: list[str] = Field(
        default_factory=lambda: ["PPT", "ASR", "TEACHER_BEHAVIOR", "STUDENT_BEHAVIOR"]
    )

    @field_validator("enabled_task_types")
    @classmethod
    def validate_task_types(cls, values: list[str]) -> list[str]:
        supported = {"PPT", "ASR", "TEACHER_BEHAVIOR", "STUDENT_BEHAVIOR"}
        normalized = list(dict.fromkeys(value.upper() for value in values))
        unknown = set(normalized) - supported
        if unknown:
            raise ValueError(f"不支持的任务类型: {', '.join(sorted(unknown))}")
        return normalized


class ReadinessConfig(BaseModel):
    dependency_timeout_seconds: float = Field(default=3.0, ge=2.0)


class ControlSettings(BaseSettings):
    """Control settings loaded as defaults < TOML < environment < explicit values."""

    model_config = SettingsConfigDict(
        env_prefix="CONTROL_",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    service: ServiceConfig = Field(default_factory=ServiceConfig)
    postgres: PostgresConfig = Field(default_factory=PostgresConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    operator_registry: OperatorRegistryConfig = Field(default_factory=OperatorRegistryConfig)
    features: FeatureConfig = Field(default_factory=FeatureConfig)
    readiness: ReadinessConfig = Field(default_factory=ReadinessConfig)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        config_path = _config_path_override.get()
        if config_path is None:
            config_path = Path(os.environ.get("CONFIG_PATH", SERVICE_ROOT / "config.toml"))
        return (
            init_settings,
            env_settings,
            TomlConfigSettingsSource(settings_cls, toml_file=config_path),
            dotenv_settings,
            file_secret_settings,
        )

    @classmethod
    def load(cls, config_path: str | Path | None = None, **values: Any) -> ControlSettings:
        token = _config_path_override.set(Path(config_path) if config_path is not None else None)
        try:
            return cls(**values)
        finally:
            _config_path_override.reset(token)
