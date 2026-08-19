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
    max_connections: int = 100
    max_keepalive_connections: int = 20
    connect_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 60.0
    write_timeout_seconds: float = 60.0
    pool_timeout_seconds: float = 5.0
    hard_timeout_seconds: float = 600.0


class LeaseConfig(BaseModel):
    request_ttl_seconds: int = 60
    websocket_ttl_seconds: int = 3600


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
