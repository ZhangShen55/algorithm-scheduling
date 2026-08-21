from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import yaml  # type: ignore[import-untyped]

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_ROOT = PLATFORM_ROOT / "deploy"
COMPOSE_PATH = DEPLOY_ROOT / "docker-compose.operators.yml"
INSTANCE_ENDPOINTS_PATH = DEPLOY_ROOT / "endpoints.json"
FULL_ENDPOINTS_PATH = DEPLOY_ROOT / "endpoints-full.json"

OPERATOR_INSTANCES = {
    "asr_offline": ("asr-offline", "gpu", "http"),
    "asr_online": ("asr-online", "gpu", "ws"),
    "ocr": ("ocr", "gpu", "http"),
    "vbas": ("vbas", "gpu", "http"),
    "facerec": ("facerec", "gpu", "http"),
    "screen_det": ("screen-det", "gpu", "http"),
    "ppt_slice": ("ppt-slice", "cpu", "http"),
}

SMOKE_SPEC = importlib.util.spec_from_file_location(
    "milestone_2b_endpoint_runner", DEPLOY_ROOT / "scripts/run_operator_smoke.py"
)
assert SMOKE_SPEC is not None and SMOKE_SPEC.loader is not None
SMOKE_MODULE = importlib.util.module_from_spec(SMOKE_SPEC)
SMOKE_SPEC.loader.exec_module(SMOKE_MODULE)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _published_host_port(service: dict[str, Any]) -> int:
    ports = service["ports"]
    assert isinstance(ports, list) and len(ports) == 1
    published = ports[0]
    if isinstance(published, dict):
        assert set(published) >= {"target", "published"}
        return int(published["published"])
    assert isinstance(published, str)
    host_port, _ = published.rsplit(":", 1)
    return int(host_port.rsplit(":", 1)[-1])


def test_instance_endpoints_match_all_compose_instances_and_published_ports() -> None:
    endpoints = _load_json(INSTANCE_ENDPOINTS_PATH)
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    services = compose["services"]

    assert set(endpoints) == set(OPERATOR_INSTANCES)
    assert sum(len(value) for value in endpoints.values()) == 21
    expected_instances = {
        f"{prefix}-{device_kind}{index}"
        for prefix, device_kind, _ in OPERATOR_INSTANCES.values()
        for index in range(3)
    }
    assert set(services) == expected_instances
    seen_instances: set[str] = set()
    for operator_code, (prefix, device_kind, scheme) in OPERATOR_INSTANCES.items():
        configured = endpoints[operator_code]
        expected_ids = {f"{prefix}-{device_kind}{index}" for index in range(3)}
        assert isinstance(configured, dict)
        assert set(configured) == expected_ids
        for instance_id, endpoint in configured.items():
            parsed = urlsplit(endpoint)
            service = services[instance_id]
            assert parsed.scheme == scheme
            assert parsed.hostname == "127.0.0.1"
            assert parsed.port == _published_host_port(service)
            assert parsed.path == ""
            assert parsed.query == ""
            assert parsed.fragment == ""
            assert parsed.username is None
            assert parsed.password is None
            assert service["environment"]["PLATFORM_INSTANCE_ID"] == instance_id
            seen_instances.add(instance_id)

    assert len(seen_instances) == 21


def test_full_endpoints_select_gpu0_cpu0_and_three_facerec_origins() -> None:
    instances = _load_json(INSTANCE_ENDPOINTS_PATH)
    full = _load_json(FULL_ENDPOINTS_PATH)

    assert set(full) == set(OPERATOR_INSTANCES)
    for operator_code, (prefix, device_kind, _) in OPERATOR_INSTANCES.items():
        if operator_code == "facerec":
            assert full[operator_code] == [
                instances[operator_code][f"{prefix}-{device_kind}{index}"]
                for index in range(3)
            ]
        else:
            assert full[operator_code] == instances[operator_code][
                f"{prefix}-{device_kind}0"
            ]


def test_instance_and_full_endpoint_files_are_not_interchangeable() -> None:
    instances = _load_json(INSTANCE_ENDPOINTS_PATH)
    full = _load_json(FULL_ENDPOINTS_PATH)

    assert all(isinstance(value, dict) for value in instances.values())
    assert isinstance(full["facerec"], list)
    assert all(
        isinstance(value, str)
        for operator_code, value in full.items()
        if operator_code != "facerec"
    )
    assert not any(isinstance(value, dict) for value in full.values())
    for operator_code, (prefix, device_kind, _) in OPERATOR_INSTANCES.items():
        with pytest.raises(ValueError, match="实例映射"):
            SMOKE_MODULE.resolve_endpoint(instances, operator_code, None)
        with pytest.raises(ValueError, match="未配置目标实例"):
            SMOKE_MODULE.resolve_endpoint(
                full,
                operator_code,
                f"{prefix}-{device_kind}0",
            )
