from __future__ import annotations

import os
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Literal

from packages.platform_common.config import LoggingConfig
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

SERVICE_ROOT = Path(__file__).resolve().parents[2]
_config_path_override: ContextVar[Path | None] = ContextVar(
    "orchestrator_config_path_override",
    default=None,
)


class ServiceConfig(BaseModel):
    name: str = "orchestrator-service"
    host: str = "0.0.0.0"
    port: int = Field(default=18101, ge=1, le=65535)
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


class PostgresRetryConfig(BaseModel):
    max_attempts: int = Field(default=5, ge=1, le=10)
    base_delay_seconds: float = Field(default=0.05, ge=0, le=10)
    max_delay_seconds: float = Field(default=1.0, ge=0, le=30)
    jitter_ratio: float = Field(default=0.2, ge=0, le=1)

    @model_validator(mode="after")
    def validate_delays(self) -> PostgresRetryConfig:
        if self.base_delay_seconds > self.max_delay_seconds:
            raise ValueError("PostgreSQL 基础重试延迟不能大于最大延迟")
        return self


class KafkaConfig(BaseModel):
    bootstrap_servers: list[str] = Field(default_factory=lambda: ["127.0.0.1:9092"])
    client_id: str = "orchestrator-service"
    course_command_topic: str = "algorithm.course.commands"
    course_consumer_group: str = "algorithm-orchestrator"
    visual_command_topic: str = "algorithm.visual.commands"
    visual_event_topic: str = "algorithm.visual.events"
    visual_event_consumer_group: str = "algorithm-orchestrator-visual-events"
    enable_auto_commit: bool = False
    auto_offset_reset: str = "earliest"
    max_poll_records: int = Field(default=10, ge=1)
    poll_timeout_seconds: float = Field(default=0.5, gt=0)
    ensure_topics: bool = True
    topic_partitions: int = Field(default=1, ge=1)
    topic_replication_factor: int = Field(default=1, ge=1)
    acks: str = "all"


class OutboxConfig(BaseModel):
    batch_size: int = Field(default=20, ge=1)
    poll_interval_seconds: float = Field(default=1.0, gt=0)


class WorkerConfig(BaseModel):
    worker_id: str = ""
    node_concurrency: int = Field(default=4, ge=1)
    claim_poll_interval_seconds: float = Field(default=1.0, gt=0)
    shutdown_timeout_seconds: float = Field(default=60.0, gt=0)
    transient_error_base_delay_seconds: float = Field(default=0.2, gt=0)
    transient_error_max_delay_seconds: float = Field(default=5.0, gt=0)
    stale_node_recovery_seconds: float = Field(default=120.0, gt=0)
    recovery_scan_interval_seconds: float = Field(default=30.0, gt=0)

    @model_validator(mode="after")
    def validate_transient_delays(self) -> WorkerConfig:
        if self.transient_error_base_delay_seconds > self.transient_error_max_delay_seconds:
            raise ValueError("后台循环基础退避不能大于最大退避")
        return self


class ControlClientConfig(BaseModel):
    base_url: str = "http://127.0.0.1:18100"
    request_timeout_seconds: float = Field(default=10.0, gt=0)
    default_lease_ttl_seconds: int = Field(default=60, gt=0)


class LeaseRenewalConfig(BaseModel):
    max_attempts: int = Field(default=3, ge=1, le=10)
    base_delay_seconds: float = Field(default=0.2, ge=0, le=30)
    max_delay_seconds: float = Field(default=2.0, ge=0, le=60)
    safety_margin_seconds: float = Field(default=5.0, ge=0)

    @model_validator(mode="after")
    def validate_delays(self) -> LeaseRenewalConfig:
        if self.base_delay_seconds > self.max_delay_seconds:
            raise ValueError("租约续租基础退避不能大于最大退避")
        return self


class StorageConfig(BaseModel):
    course_root: Path = Path("/data/course")
    result_root: Path = Path("/data/result")
    max_video_bytes: int = Field(default=10_737_418_240, gt=0)
    cleanup_terminal_workspace: bool = True
    cleanup_reconcile_interval_seconds: float = Field(default=60.0, gt=0)


class MediaConfig(BaseModel):
    download_connect_timeout_seconds: float = Field(default=10.0, gt=0)
    download_read_timeout_seconds: float = Field(default=3600.0, gt=0)
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"


class PptConfig(BaseModel):
    slice_threshold: float = Field(default=0.99, ge=0, le=1)
    callback_base_url: str = "http://127.0.0.1:18101"
    terminal_callback_path: str = "/internal/ppt-slice/callback"
    processing_timeout_seconds: float = Field(default=7200.0, gt=0)
    max_manifest_bytes: int = Field(default=2_097_152, gt=0)
    lease_ttl_seconds: int = Field(default=60, gt=0)
    lease_renew_interval_seconds: float = Field(default=20.0, gt=0)
    reconcile_interval_seconds: float = Field(default=30.0, gt=0)
    submit_transport_max_attempts: int = Field(default=2, ge=1, le=5)
    submit_transport_retry_delay_seconds: float = Field(default=0.2, ge=0, le=10)
    ocr_batch_size: int = Field(default=8, ge=1)
    ocr_max_concurrency: int = Field(default=2, ge=1)
    ocr_request_timeout_seconds: float = Field(default=600.0, gt=0)
    ocr_transport_max_attempts: int = Field(default=2, ge=1, le=5)
    ocr_transport_retry_delay_seconds: float = Field(default=0.2, ge=0, le=10)

    @field_validator("lease_renew_interval_seconds")
    @classmethod
    def validate_lease_renewal_interval(cls, value: float, info: Any) -> float:
        ttl = info.data.get("lease_ttl_seconds", 60)
        if value >= ttl:
            raise ValueError("PPT 容量续约间隔必须小于租约 TTL")
        return value


class AsrConfig(BaseModel):
    request_timeout_seconds: float = Field(default=7200.0, gt=0)


class ReadinessConfig(BaseModel):
    dependency_timeout_seconds: float = Field(default=3.0, gt=0)


class OrchestratorSettings(BaseSettings):
    """Orchestrator settings loaded as defaults < TOML < environment < explicit values."""

    model_config = SettingsConfigDict(
        env_prefix="ORCHESTRATOR_",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    service: ServiceConfig = Field(default_factory=ServiceConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    postgres: PostgresConfig = Field(default_factory=PostgresConfig)
    postgres_retry: PostgresRetryConfig = Field(default_factory=PostgresRetryConfig)
    kafka: KafkaConfig = Field(default_factory=KafkaConfig)
    outbox: OutboxConfig = Field(default_factory=OutboxConfig)
    worker: WorkerConfig = Field(default_factory=WorkerConfig)
    control: ControlClientConfig = Field(default_factory=ControlClientConfig)
    lease_renewal: LeaseRenewalConfig = Field(default_factory=LeaseRenewalConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    media: MediaConfig = Field(default_factory=MediaConfig)
    ppt: PptConfig = Field(default_factory=PptConfig)
    asr: AsrConfig = Field(default_factory=AsrConfig)
    readiness: ReadinessConfig = Field(default_factory=ReadinessConfig)

    @model_validator(mode="after")
    def validate_lease_safety_window(self) -> OrchestratorSettings:
        if (
            self.lease_renewal.safety_margin_seconds
            >= self.control.default_lease_ttl_seconds
        ):
            raise ValueError("租约续租安全余量必须小于默认租约 TTL")
        if self.lease_renewal.safety_margin_seconds >= self.ppt.lease_ttl_seconds:
            raise ValueError("租约续租安全余量必须小于 PPT 租约 TTL")
        return self

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
    def load(
        cls,
        config_path: str | Path | None = None,
        **values: Any,
    ) -> OrchestratorSettings:
        token = _config_path_override.set(Path(config_path) if config_path is not None else None)
        try:
            return cls(**values)
        finally:
            _config_path_override.reset(token)
