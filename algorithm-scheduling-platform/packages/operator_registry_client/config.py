from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[import-not-found,no-redef]

from packages.operator_registry_client.validation import validate_positive_int


@dataclass(frozen=True, slots=True)
class OperatorPlatformSettings:
    registration_enabled: bool
    control_service_url: str
    heartbeat_interval_seconds: float
    max_concurrent_requests: int


@dataclass(frozen=True, slots=True)
class OperatorRuntimeSettings:
    require_gpu: bool


@dataclass(frozen=True, slots=True)
class OperatorDeploymentSettings:
    platform: OperatorPlatformSettings
    runtime: OperatorRuntimeSettings


def load_operator_deployment_settings(
    config_path: str | Path,
    *,
    default_capacity: int,
) -> OperatorDeploymentSettings:
    validate_positive_int(default_capacity, field_name="算子默认容量")
    path = Path(config_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"算子配置文件不存在: {path}")
    with path.open("rb") as source:
        raw = tomllib.load(source)
    platform = _mapping_section(raw, "platform")
    runtime = _mapping_section(raw, "runtime")

    registration_enabled = _strict_bool(
        platform.get("registration_enabled", False),
        field_name="platform.registration_enabled",
    )
    control_service_url = platform.get("control_service_url", "")
    if type(control_service_url) is not str:
        raise ValueError("platform.control_service_url 必须是字符串")
    control_service_url = control_service_url.strip()
    if registration_enabled and not _is_http_url(control_service_url):
        raise ValueError(
            "启用算子注册时 platform.control_service_url 必须是合法的 HTTP(S) URL"
        )

    heartbeat_interval_seconds = platform.get("heartbeat_interval_seconds", 5)
    if (
        type(heartbeat_interval_seconds) not in {int, float}
        or isinstance(heartbeat_interval_seconds, bool)
        or not math.isfinite(heartbeat_interval_seconds)
        or heartbeat_interval_seconds <= 0
    ):
        raise ValueError("platform.heartbeat_interval_seconds 必须是有限正数")

    max_concurrent_requests = validate_positive_int(
        platform.get("max_concurrent_requests", default_capacity),
        field_name="platform.max_concurrent_requests",
    )
    require_gpu = _strict_bool(
        runtime.get("require_gpu", False),
        field_name="runtime.require_gpu",
    )
    return OperatorDeploymentSettings(
        platform=OperatorPlatformSettings(
            registration_enabled=registration_enabled,
            control_service_url=control_service_url,
            heartbeat_interval_seconds=float(heartbeat_interval_seconds),
            max_concurrent_requests=max_concurrent_requests,
        ),
        runtime=OperatorRuntimeSettings(require_gpu=require_gpu),
    )


def _mapping_section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    section = raw.get(name, {})
    if type(section) is not dict:
        raise ValueError(f"{name} 必须是 TOML 表")
    return section


def _strict_bool(value: object, *, field_name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{field_name} 必须是布尔值")
    return value


def _is_http_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
