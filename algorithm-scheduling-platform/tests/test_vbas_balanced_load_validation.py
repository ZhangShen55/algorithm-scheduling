from __future__ import annotations

import asyncio
import importlib.util
import sys
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy/scripts/vbas_balanced_load_validation.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("vbas_balanced_load_validation", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


validation = _load_module()


def _snapshot(instance_id: str, *, active: int = 0, inflight: int = 0) -> dict[str, object]:
    return {
        "instance_id": instance_id,
        "operator_code": "vbas",
        "lifecycle": "ONLINE",
        "model_ready": True,
        "declared_capacity": 1024,
        "reported_inflight": inflight,
        "active_lease_count": active,
        "schedulable_used": max(active, inflight),
    }


def _lease(
    lease_id: str,
    instance_id: str,
    source: str,
    *,
    task_id: str | None = None,
    trace_id: str | None = None,
) -> dict[str, object]:
    return {
        "lease_id": lease_id,
        "instance_id": instance_id,
        "capability": "student_behavior",
        "expires_at": "2026-08-27T08:00:00+00:00",
        "work_context": {
            "source_service": source,
            "work_type": "vbas_student_batch" if task_id else "online_vbas",
            "work_id": f"work-{lease_id}",
            "task_id": task_id,
            "item_id": f"batch-{lease_id}",
            "trace_id": trace_id,
        },
    }


def _observe(tracker: Any, leases: list[dict[str, object]]) -> None:
    by_instance = {
        instance_id: [item for item in leases if item["instance_id"] == instance_id]
        for instance_id in validation.VBAS_INSTANCE_IDS
    }
    tracker.observe(
        [
            _snapshot(
                instance_id,
                active=len(by_instance[instance_id]),
                inflight=len(by_instance[instance_id]),
            )
            for instance_id in validation.VBAS_INSTANCE_IDS
        ],
        [
            {
                "instance_id": instance_id,
                "active_lease_count": len(by_instance[instance_id]),
                "reported_inflight": len(by_instance[instance_id]),
                "leases": by_instance[instance_id],
            }
            for instance_id in validation.VBAS_INSTANCE_IDS
        ],
        {"queues": [], "outbox_pending": 0},
    )


def test_student_submission_preserves_frozen_a_service_fields() -> None:
    payload = validation.build_student_submission("course-001")

    assert list(payload) == [
        "task_id",
        "task_types",
        "priority",
        "teacher_video_path",
        "student_video_path",
        "slides_video_path",
        "front_points",
        "back_point",
        "student_count",
        "asr_options",
    ]
    assert payload["task_types"] == ["STUDENT_BEHAVIOR"]
    assert payload["student_count"] == 70
    assert payload["asr_options"] is None
    assert "expected_student_count" not in payload


def test_tracker_correlates_task_batch_instance_and_source_then_converges() -> None:
    tracker = validation.EvidenceTracker(validation.VBAS_INSTANCE_IDS)
    leases = [
        _lease(f"lease-{index}", instance_id, validation.VISION_SOURCE, task_id=f"task-{index}")
        for index, instance_id in enumerate(validation.VBAS_INSTANCE_IDS)
    ]

    _observe(tracker, leases)
    _observe(tracker, [])

    assert tracker.instances_for_source(validation.VISION_SOURCE) == set(
        validation.VBAS_INSTANCE_IDS
    )
    assert tracker.all_converged()
    assert all(item.task_id and item.batch_id for item in tracker.leases.values())
    assert all(item.disappeared_at for item in tracker.leases.values())
    assert validation._validate_distribution(
        tracker, validation.VISION_SOURCE, expected_minimum=3
    ) == {instance_id: 1 for instance_id in validation.VBAS_INSTANCE_IDS}


def test_tracker_rejects_capacity_oversell() -> None:
    tracker = validation.EvidenceTracker(validation.VBAS_INSTANCE_IDS)
    snapshots = [_snapshot(instance_id) for instance_id in validation.VBAS_INSTANCE_IDS]
    snapshots[0]["active_lease_count"] = 1025

    with pytest.raises(validation.ValidationError, match="租约超卖"):
        tracker.observe(
            snapshots,
            [
                {
                    "instance_id": instance_id,
                    "active_lease_count": 0,
                    "reported_inflight": 0,
                    "leases": [],
                }
                for instance_id in validation.VBAS_INSTANCE_IDS
            ],
            {"queues": [], "outbox_pending": 0},
        )


def test_distribution_fails_closed_when_first_observed_leases_are_biased() -> None:
    tracker = validation.EvidenceTracker(validation.VBAS_INSTANCE_IDS)
    leases = [
        _lease(f"lease-{index}", "vbas-gpu0", validation.ONLINE_SOURCE, trace_id=f"trace-{index}")
        for index in range(3)
    ]
    _observe(tracker, leases)
    leases.extend(
        _lease(
            f"lease-later-{index}", instance_id, validation.ONLINE_SOURCE, trace_id=f"later-{index}"
        )
        for index, instance_id in enumerate(("vbas-gpu1", "vbas-gpu2"), start=1)
    )
    _observe(tracker, leases)

    with pytest.raises(validation.ValidationError, match="首次观测"):
        validation._validate_distribution(tracker, validation.ONLINE_SOURCE, expected_minimum=3)


@pytest.mark.asyncio
async def test_synchronized_online_burst_proves_all_requests_were_inflight() -> None:
    class SlowHttp:
        async def post(
            self,
            url: str,
            body: Mapping[str, object],
            headers: Mapping[str, str],
        ) -> Any:
            del url, body, headers
            await asyncio.sleep(0.01)
            return validation.HttpObservation(200, {"code": 0}, 0.01)

    requests = [
        (f"image-{index}", {"value": index}, {"X-Trace-ID": f"trace-{index}"})
        for index in range(20)
    ]
    result = await validation._run_synchronized_posts(
        SlowHttp(), "http://127.0.0.1:18103/api/online/vbas/analyze", requests
    )

    assert result.released_count == 20
    assert result.peak_client_inflight == 20
    assert validation._validate_online(result, 20) == {"成功": 20}


def test_online_validation_rejects_any_capacity_error() -> None:
    result = validation.BurstResult(
        records=[
            {
                "request_id": "ok",
                "classification": "成功",
                "business_code": 0,
            },
            {
                "request_id": "capacity",
                "classification": "容量不足",
                "business_code": 50301,
            },
        ],
        peak_client_inflight=2,
        released_count=2,
    )

    with pytest.raises(validation.ValidationError, match="在线请求存在"):
        validation._validate_online(result, 2)


def test_gateway_metrics_prove_exact_online_leases_and_three_instance_calls() -> None:
    before = validation.parse_gateway_metrics(
        "\n".join(
            (
                "algorithm_capacity_lease_events_total"
                '{capability="student_behavior",instance_id="vbas-gpu0",'
                'outcome="acquired"} 10',
                "algorithm_capacity_lease_events_total"
                '{capability="student_behavior",instance_id="vbas-gpu0",'
                'outcome="released"} 10',
                "algorithm_operator_request_latency_seconds_count"
                '{capability="student_behavior",instance_id="vbas-gpu0",'
                'operator_code="vbas"} 10',
            )
        )
    )
    after_lines: list[str] = []
    for instance_id, count in zip(validation.VBAS_INSTANCE_IDS, (334, 333, 333), strict=True):
        prior = 10 if instance_id == "vbas-gpu0" else 0
        for outcome in ("acquired", "released"):
            after_lines.append(
                "algorithm_capacity_lease_events_total"
                f'{{capability="student_behavior",instance_id="{instance_id}",'
                f'outcome="{outcome}"}} {prior + count}'
            )
        after_lines.append(
            "algorithm_operator_request_latency_seconds_count"
            f'{{capability="student_behavior",instance_id="{instance_id}",'
            f'operator_code="vbas"}} {prior + count}'
        )
    after = validation.parse_gateway_metrics("\n".join(after_lines))

    delta = validation.gateway_metric_delta(before, after)
    validation.validate_gateway_metric_delta(delta, 1000)

    assert sum(delta["lease_acquired"].values()) == 1000
    assert delta["operator_requests"] == {
        "vbas-gpu0": 334,
        "vbas-gpu1": 333,
        "vbas-gpu2": 333,
    }


def test_gateway_metrics_fail_when_one_instance_is_starved() -> None:
    delta = {
        metric: {"vbas-gpu0": 1000, "vbas-gpu1": 0, "vbas-gpu2": 0}
        for metric in ("lease_acquired", "lease_released", "operator_requests")
    }
    delta["operator_errors"] = {instance_id: 0 for instance_id in validation.VBAS_INSTANCE_IDS}

    with pytest.raises(validation.ValidationError, match="没有覆盖三个实例"):
        validation.validate_gateway_metric_delta(delta, 1000)


def test_write_once_evidence_and_no_media_payload(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    validation._atomic_write(path, {"status": "失败", "reason": "三实例没有全部获得租约"})

    assert path.stat().st_mode & 0o777 == 0o600
    assert "三实例没有全部获得租约" in path.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError, match="禁止覆盖"):
        validation._atomic_write(path, {"status": "通过"})


def test_course_summary_keeps_status_and_reason_but_not_large_result() -> None:
    body = {
        "code": 0,
        "data": {
            "task_id": "task-1",
            "tasks": [
                {
                    "task_type": "STUDENT_BEHAVIOR",
                    "status": 60,
                    "reason": "学生行为分析完成",
                    "result": {"large": "not-for-evidence"},
                    "nodes": [
                        {
                            "node_code": "STUDENT_BEHAVIOR",
                            "status": 60,
                            "reason": "处理完成",
                            "progress": 100,
                            "result": {"large": "not-for-evidence"},
                        }
                    ],
                }
            ],
        },
    }

    summary = validation._course_summary(body, "task-1")

    assert summary["status"] == 60
    assert summary["reason"] == "学生行为分析完成"
    assert "result" not in summary
    assert "result" not in summary["nodes"][0]


def test_kafka_lag_probe_uses_the_compose_project_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...], **_: object) -> Any:
        calls.append(command)
        if command[:2] == ("docker", "ps"):
            return type("Result", (), {"stdout": "a" * 64 + "\n", "stderr": ""})()
        return type(
            "Result",
            (),
            {
                "stdout": "GROUP TOPIC PARTITION CURRENT-OFFSET LOG-END-OFFSET LAG\n"
                "vision topic 0 1 1 0\n",
                "stderr": "",
            },
        )()

    monkeypatch.setattr(validation.subprocess, "run", run)

    assert validation.collect_kafka_lag() == 0
    assert "label=com.docker.compose.project=algorithm-scheduling-platform" in calls[0]


@pytest.mark.asyncio
async def test_online_scenario_runs_full_gate_with_reduced_test_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeHttp:
        def __init__(self) -> None:
            self.leases: dict[str, dict[str, object]] = {}
            self.sequence = 0
            self.metrics = {
                name: {instance_id: 0 for instance_id in validation.VBAS_INSTANCE_IDS}
                for name in (
                    "lease_acquired",
                    "lease_released",
                    "operator_requests",
                    "operator_errors",
                )
            }

        def metrics_text(self) -> str:
            lines: list[str] = []
            for instance_id in validation.VBAS_INSTANCE_IDS:
                for outcome, metric in (
                    ("acquired", "lease_acquired"),
                    ("released", "lease_released"),
                ):
                    lines.append(
                        "algorithm_capacity_lease_events_total"
                        f'{{capability="student_behavior",instance_id="{instance_id}",'
                        f'outcome="{outcome}"}} {self.metrics[metric][instance_id]}'
                    )
                lines.append(
                    "algorithm_operator_request_latency_seconds_count"
                    f'{{capability="student_behavior",instance_id="{instance_id}",'
                    'operator_code="vbas"} '
                    f"{self.metrics['operator_requests'][instance_id]}"
                )
                lines.append(
                    "algorithm_operator_request_errors_total"
                    f'{{capability="student_behavior",instance_id="{instance_id}",'
                    'operator_code="vbas"} '
                    f"{self.metrics['operator_errors'][instance_id]}"
                )
            return "\n".join(lines)

        async def get(self, url: str) -> Any:
            if url.endswith("/metrics"):
                body: object = self.metrics_text()
            elif url.endswith("/ops/operator-instances/snapshot"):
                body = [
                    _snapshot(
                        instance_id,
                        active=sum(
                            1
                            for lease in self.leases.values()
                            if lease["instance_id"] == instance_id
                        ),
                        inflight=sum(
                            1
                            for lease in self.leases.values()
                            if lease["instance_id"] == instance_id
                        ),
                    )
                    for instance_id in validation.VBAS_INSTANCE_IDS
                ]
            elif url.endswith("/ops/queues"):
                body = {"queues": [], "outbox_pending": 0}
            elif url.endswith("/active-leases"):
                instance_id = url.rsplit("/", 2)[-2]
                selected = [
                    lease for lease in self.leases.values() if lease["instance_id"] == instance_id
                ]
                body = {
                    "instance_id": instance_id,
                    "active_lease_count": len(selected),
                    "reported_inflight": len(selected),
                    "leases": selected,
                }
            else:
                raise AssertionError(url)
            return validation.HttpObservation(200, body, 0.001)

        async def post(
            self,
            url: str,
            body: Mapping[str, object],
            headers: Mapping[str, str],
        ) -> Any:
            assert url.endswith("/api/online/vbas/analyze")
            self.sequence += 1
            instance_id = validation.VBAS_INSTANCE_IDS[(self.sequence - 1) % 3]
            lease_id = f"lease-{self.sequence:04d}"
            trace_id = headers["X-Trace-ID"]
            self.leases[lease_id] = _lease(
                lease_id,
                instance_id,
                validation.ONLINE_SOURCE,
                trace_id=trace_id,
            )
            self.metrics["lease_acquired"][instance_id] += 1
            await asyncio.sleep(0.03)
            del self.leases[lease_id]
            self.metrics["lease_released"][instance_id] += 1
            self.metrics["operator_requests"][instance_id] += 1
            return validation.HttpObservation(200, {"code": 0, "data": {}}, 0.03)

    monkeypatch.setattr(validation, "collect_kafka_lag", lambda: 0)
    monkeypatch.setattr(
        validation,
        "collect_local_system_evidence",
        lambda: {"gpu": ["0, uuid, 10, 100"], "processes": ["uuid, 1, 100, vbas"]},
    )
    monkeypatch.setattr(
        validation,
        "collect_vbas_log_summary",
        lambda _started_at, _task_ids: {
            instance_id: {
                "container_id": "a" * 64,
                "student_batches_accepted": 4,
                "student_batches_rejected": 0,
                "student_batches_failed": 0,
                "matched_task_ids": [],
            }
            for instance_id in validation.VBAS_INSTANCE_IDS
        },
    )
    image = tmp_path / "frame.jpg"
    image.write_bytes(b"\xff\xd8\xfftest")
    config = validation.RunConfig(
        scenario="online1000",
        run_id="unit-online",
        control_origin="http://127.0.0.1:18100",
        gateway_origin="http://127.0.0.1:18103",
        image_path=image,
        online_count=12,
        task_timeout_seconds=1,
        mixed_gate_timeout_seconds=1,
        convergence_timeout_seconds=1,
        course_poll_seconds=0.01,
        lease_poll_seconds=0.001,
        system_poll_seconds=0.01,
    )

    document = await validation.run_validation(config, FakeHttp())

    assert document["status"] == "通过"
    assert document["online"]["peak_client_inflight"] == 12
    assert document["online"]["gateway_metric_delta"]["operator_requests"] == {
        "vbas-gpu0": 4,
        "vbas-gpu1": 4,
        "vbas-gpu2": 4,
    }
    serialized = str(document)
    assert "/9j/dGVzdA==" not in serialized
    assert validation.STUDENT_VIDEO_URL not in serialized
