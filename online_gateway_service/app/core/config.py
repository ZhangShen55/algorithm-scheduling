from __future__ import annotations

import os
from pathlib import Path
from typing import Self

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from packages.platform_common.config import LoggingConfig

SERVICE_ROOT = Path(__file__).resolve().parents[2]


class ServiceConfig(BaseModel):
    name: str = "online-gateway-service"
    host: str = "0.0.0.0"
    port: int = 8001
    workers: int = 1
    environment: str = "development"
    log_level: str = "INFO"
    trace_header: str = "X-Trace-ID"


class ControlConfig(BaseModel):
    base_url: str = "http://127.0.0.1:18100"
    timeout_seconds: float = 10.0


class FacePersonsConfig(BaseModel):
    base_url: str = "http://127.0.0.1:8003"


class HttpConfig(BaseModel):
    max_connections: int = Field(default=2048, gt=0)
    max_keepalive_connections: int = Field(default=512, gt=0)
    connect_timeout_seconds: float = Field(default=5.0, gt=0, allow_inf_nan=False)
    read_timeout_seconds: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    write_timeout_seconds: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    pool_timeout_seconds: float = Field(default=5.0, gt=0, allow_inf_nan=False)
    hard_timeout_seconds: float = Field(default=600.0, gt=0, allow_inf_nan=False)
    operator_max_attempts: int = Field(default=3, ge=1, le=10)
    retry_base_delay_seconds: float = Field(default=0.2, ge=0, le=30)
    retry_max_delay_seconds: float = Field(default=2.0, ge=0, le=60)

    @model_validator(mode="after")
    def keepalive_connections_must_fit_pool(self) -> Self:
        if self.max_keepalive_connections > self.max_connections:
            raise ValueError("保活连接数不能超过总连接数")
        if self.retry_base_delay_seconds > self.retry_max_delay_seconds:
            raise ValueError("算子调用基础退避不能大于最大退避")
        return self


class LeaseConfig(BaseModel):
    request_ttl_seconds: int = 60
    websocket_ttl_seconds: int = 3600
    renewal_max_attempts: int = Field(default=3, ge=1, le=10)
    renewal_base_delay_seconds: float = Field(default=0.2, ge=0, le=30)
    renewal_max_delay_seconds: float = Field(default=2.0, ge=0, le=60)
    renewal_safety_margin_seconds: float = Field(default=5.0, ge=0)
    acquire_wait_timeout_seconds: float = Field(default=300.0, gt=0)
    acquire_retry_interval_seconds: float = Field(default=0.2, gt=0)

    @model_validator(mode="after")
    def validate_renewal(self) -> Self:
        if self.renewal_base_delay_seconds > self.renewal_max_delay_seconds:
            raise ValueError("租约续租基础退避不能大于最大退避")
        if self.renewal_safety_margin_seconds >= min(
            self.request_ttl_seconds,
            self.websocket_ttl_seconds,
        ):
            raise ValueError("租约续租安全余量必须小于所有租约 TTL")
        return self


class Base64Config(BaseModel):
    max_decoded_bytes: int = 52_428_800
    allow_data_uri: bool = True


class BodyConfig(BaseModel):
    max_bytes: int = 75_497_472


class WebSocketConfig(BaseModel):
    open_timeout_seconds: float = 10.0
    ping_interval_seconds: float = 20.0
    ping_timeout_seconds: float = 20.0
    close_timeout_seconds: float = 10.0
    session_timeout_seconds: float = 14_400.0


class ReadinessConfig(BaseModel):
    dependency_timeout_seconds: float = 3.0
    require_control: bool = True


class OnlineGatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ONLINE_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    service: ServiceConfig = ServiceConfig()
    logging: LoggingConfig = LoggingConfig()
    control: ControlConfig = ControlConfig()
    face_persons: FacePersonsConfig = FacePersonsConfig()
    http: HttpConfig = HttpConfig()
    leases: LeaseConfig = LeaseConfig()
    base64: Base64Config = Base64Config()
    body: BodyConfig = BodyConfig()
    websocket: WebSocketConfig = WebSocketConfig()
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
