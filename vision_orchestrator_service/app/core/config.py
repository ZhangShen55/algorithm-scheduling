from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

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
    default_interval_seconds: float = 10.0
    refinement_intervals_seconds: tuple[float, ...] = (5.0, 2.0)
    min_interval_seconds: float = 2.0
    max_interval_seconds: float = 60.0
    max_candidate_windows: int = 20
    max_detection_points: int = 10_000


class MediaConfig(BaseModel):
    ffmpeg_binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"
    command_timeout_seconds: float = 60.0


class VbasConfig(BaseModel):
    request_timeout_seconds: float = 60.0
    max_batch_size: int = 8
    max_concurrency: int = 2
    lease_ttl_seconds: int = 30


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
    postgres: PostgresConfig = PostgresConfig()
    kafka: KafkaConfig = KafkaConfig()
    control: ControlConfig = ControlConfig()
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
