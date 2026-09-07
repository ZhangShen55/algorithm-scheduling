from __future__ import annotations

import pytest

from gpu_metrics_exporter.app import configured_host_id, prometheus


def test_configured_host_id_requires_valid_ipv4(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GPU_EXPORTER_HOST_ID", raising=False)
    with pytest.raises(RuntimeError, match="required"):
        configured_host_id()

    monkeypatch.setenv("GPU_EXPORTER_HOST_ID", "node-b")
    with pytest.raises(RuntimeError, match="IPv4"):
        configured_host_id()

    monkeypatch.setenv("GPU_EXPORTER_HOST_ID", "192.168.29.12")
    assert configured_host_id() == "192.168.29.12"


def test_prometheus_contains_host_identity() -> None:
    payload = prometheus({
        "host_id": "192.168.29.12",
        "devices": [{
            "index": 0,
            "name": "NVIDIA Test",
            "utilization_percent": 11,
            "memory_used_bytes": 1,
            "memory_total_bytes": 2,
            "temperature_celsius": None,
            "power_watts": None,
            "process_count": 0,
        }],
    })
    assert 'host_id="192.168.29.12"' in payload
    assert 'gpu_index="0"' in payload
