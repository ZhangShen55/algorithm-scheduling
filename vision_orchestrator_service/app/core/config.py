from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field, StrictInt, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from packages.platform_common.config import LoggingConfig

SERVICE_ROOT = Path(__file__).resolve().parents[2]


class ServiceConfig(BaseModel):
    name: str = "vision-orchestrator-service"
    host: str = "0.0.0.0"
    port: int = 8010
    workers: int = 1
    environment: str = "development"
    log_level: str = "INFO"
    trace_header: str = "X-Trace-ID"


class PostgresConfig(BaseModel):
    dsn: str = "postgresql+psycopg://algorithm:algorithm@127.0.0.1:5432/algorithm"
    pool_size: int = 10
    max_overflow: int = 20
    pool_timeout_seconds: float = 30.0


class KafkaConfig(BaseModel):
    bootstrap_servers: str = "127.0.0.1:9092"
    client_id: str = "vision-orchestrator"
    command_topic: str = "algorithm.visual.commands"
    event_topic: str = "algorithm.visual.events"
    consumer_group: str = "vision-orchestrator"
    enable_auto_commit: bool = False
    auto_offset_reset: str = "earliest"
    max_poll_records: int = 2
    poll_timeout_seconds: float = 1.0
    ensure_topics: bool = False
    topic_partitions: int = 1
    topic_replication_factor: int = 1


class ControlConfig(BaseModel):
    base_url: str = "http://127.0.0.1:18100"
    timeout_seconds: float = 10.0


class LeaseRenewalConfig(BaseModel):
    max_attempts: int = Field(default=3, ge=1, le=10)
    base_delay_seconds: float = Field(default=0.2, ge=0, le=30)
    max_delay_seconds: float = Field(default=2.0, ge=0, le=60)
    safety_margin_seconds: float = Field(default=5.0, ge=0)
    acquire_wait_timeout_seconds: float = Field(default=300.0, gt=0)
    acquire_retry_interval_seconds: float = Field(default=0.2, gt=0)

    @model_validator(mode="after")
    def validate_delays(self) -> LeaseRenewalConfig:
        if self.base_delay_seconds > self.max_delay_seconds:
            raise ValueError("租约续租基础退避不能大于最大退避")
        return self


class WorkerConfig(BaseModel):
    concurrency: int = 2
    poll_interval_seconds: float = 1.0
    shutdown_timeout_seconds: float = 30.0


class StorageConfig(BaseModel):
    course_root: Path = Path("/data/course")
    result_root: Path = Path("/data/result")
    evidence_root: Path = Path("/data/result/evidence")


class ScanConfig(BaseModel):
    batch_size: int = 8
    batch_prefetch: Annotated[StrictInt, Field(gt=0, le=8)] = 2
    progress_update_interval_batches: Annotated[StrictInt, Field(gt=0)] = 2
    default_interval_seconds: float = 10.0
    refinement_intervals_seconds: tuple[float, ...] = (5.0, 2.0)
    end_frame_margin_seconds: Annotated[
        float,
        Field(gt=0, allow_inf_nan=False),
    ] = 0.5
    min_interval_seconds: float = 2.0
    max_interval_seconds: float = 60.0
    max_candidate_windows: int = 128
    max_detection_points: int = 10_000


class MediaConfig(BaseModel):
    ffmpeg_binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"
    command_timeout_seconds: float = 60.0
    max_concurrent_processes: Annotated[StrictInt, Field(gt=0)] = 2


class VbasConfig(BaseModel):
    request_timeout_seconds: float = 60.0
    max_batch_size: int = 8
    lease_ttl_seconds: int = 30
    capacity_snapshot_refresh_seconds: float = Field(default=1.0, gt=0)
    transient_max_attempts: int = Field(default=3, ge=1, le=10)
    transient_retry_base_delay_seconds: float = Field(default=0.2, ge=0, le=30)
    transient_retry_max_delay_seconds: float = Field(default=2.0, ge=0, le=60)

    @model_validator(mode="after")
    def validate_transient_retry_delays(self) -> VbasConfig:
        if self.transient_retry_base_delay_seconds > self.transient_retry_max_delay_seconds:
            raise ValueError("VBas 瞬时故障基础退避不能大于最大退避")
        return self


class CacheConfig(BaseModel):
    max_inference_entries: int = 2048
    frame_ttl_seconds: int = 600


class BehaviorConfig(BaseModel):
    enabled: bool = True
    minimum_confidence: float = 0.5
    interval_gap_seconds: float = 5.0


class EvidenceConfig(BaseModel):
    max_per_category: int = 3
    max_total: int = 20
    same_category_min_interval_seconds: float = 30.0


class ReadinessConfig(BaseModel):
    dependency_timeout_seconds: float = 3.0
    require_postgres: bool = True
    require_kafka: bool = True
    require_control: bool = True


class VisionSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VISION_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    service: ServiceConfig = ServiceConfig()
    logging: LoggingConfig = LoggingConfig()
    postgres: PostgresConfig = PostgresConfig()
    kafka: KafkaConfig = KafkaConfig()
    control: ControlConfig = ControlConfig()
    lease_renewal: LeaseRenewalConfig = LeaseRenewalConfig()
    worker: WorkerConfig = WorkerConfig()
    storage: StorageConfig = StorageConfig()
    scan: ScanConfig = ScanConfig()
    media: MediaConfig = MediaConfig()
    vbas: VbasConfig = VbasConfig()
    cache: CacheConfig = CacheConfig()
    teacher_behavior: BehaviorConfig = BehaviorConfig()
    student_behavior: BehaviorConfig = BehaviorConfig()
    evidence: EvidenceConfig = EvidenceConfig()
    readiness: ReadinessConfig = ReadinessConfig()

    @model_validator(mode="after")
    def validate_lease_safety_window(self) -> VisionSettings:
        if self.lease_renewal.safety_margin_seconds >= self.vbas.lease_ttl_seconds:
            raise ValueError("租约续租安全余量必须小于 VBas 租约 TTL")
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
        del dotenv_settings
        config_path = Path(os.environ.get("CONFIG_PATH", SERVICE_ROOT / "config.toml"))
        return (
            init_settings,
            env_settings,
            TomlConfigSettingsSource(settings_cls, toml_file=config_path),
            file_secret_settings,
        )
