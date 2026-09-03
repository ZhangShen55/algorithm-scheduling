from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "deploy/scripts/capacity_recovery_sampler.py"
SPEC = importlib.util.spec_from_file_location("capacity_recovery_sampler", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
sampler = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sampler
SPEC.loader.exec_module(sampler)


def test_sample_contains_required_read_only_evidence(monkeypatch) -> None:
    requested: list[str] = []

    def fake_get(url: str) -> object:
        requested.append(url)
        return {"ok": True}

    monkeypatch.setattr(sampler, "_get_json", fake_get)
    monkeypatch.setattr(sampler, "_command", lambda *command: {"command": command})

    result = sampler._sample("http://control:18100")

    assert set(result) == {
        "recorded_at",
        "queues",
        "operator_instances",
        "active_leases",
        "gpu",
        "gpu_processes",
        "containers",
        "disk",
    }
    assert set(result["active_leases"]) == set(sampler.VBAS_INSTANCES)
    assert requested == [
        "http://control:18100/ops/operator-instances/vbas-gpu0/active-leases",
        "http://control:18100/ops/operator-instances/vbas-gpu1/active-leases",
        "http://control:18100/ops/operator-instances/vbas-gpu2/active-leases",
        "http://control:18100/ops/queues",
        "http://control:18100/ops/operator-instances",
    ]
