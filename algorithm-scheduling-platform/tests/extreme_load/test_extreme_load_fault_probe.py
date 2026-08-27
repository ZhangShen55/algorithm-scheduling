from __future__ import annotations

import json
import os
import stat
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from deploy.scripts import extreme_load_fault_probe as probe
from deploy.scripts.operator_topology import CURRENT_TOPOLOGY
from packages.platform_common.redis_operator_registry import _LEASE_SCRIPT

_SHA = "a" * 40
_TOPOLOGY_CAPACITY = {
    "asr_offline": 4,
    "asr_online": 10,
    "facerec": 128,
    "ocr": 256,
    "ppt_slice": 10,
    "screen_det": 128,
    "vbas": 1024,
}


def _target(service: str, index: int = 1) -> probe.TargetIdentity:
    project = (
        "algorithm-operators"
        if service
        not in {
            "control-service",
            "orchestrator-service",
            "vision-orchestrator-service",
            "online-gateway-service",
            "kafka",
            "redis",
        }
        else "algorithm-scheduling-platform"
    )
    return probe.TargetIdentity(project, service, f"{index:064x}")


def _request(
    tmp_path: Path,
    *,
    case_id: str = "RECOVERY-OPERATOR-OCR",
    scenario_id: str = "fault-operator-03-ocr",
    phase: str = "recovery",
    check_index: int = 2,
    targets: tuple[probe.TargetIdentity, ...] | None = None,
    baseline_ref: str | None = None,
    action_ref: str | None = None,
    fault_window_token: str | None = None,
    fault_window_opened_at: str | None = None,
) -> probe.ProbeRequest:
    release_root = tmp_path / "reports" / "milestone-2b" / "releases" / "r1" / _SHA
    release_root.mkdir(parents=True, mode=0o700, exist_ok=True)
    lock_path = release_root.parent / ".operator-lifecycle.lock"
    lock_path.touch(mode=0o600, exist_ok=True)
    lock_path.chmod(0o600)
    evidence_root = release_root / "campaign" / "phase-5-recovery" / "fault-probes"
    window_token = fault_window_token or (None if phase == "baseline" else "c" * 32)
    window_opened_at = fault_window_opened_at or (
        None if phase == "baseline" else "2026-08-24T00:00:05+00:00"
    )
    return probe.ProbeRequest.build(
        campaign_id="campaign-r1-aabbccdd",
        case_id=case_id,
        scenario_id=scenario_id,
        phase=phase,
        check_index=check_index,
        challenge="b" * 32,
        targets=targets or (_target("ocr-gpu0"),),
        release_root=release_root,
        evidence_root=evidence_root,
        lock_holder_pid=os.getpid(),
        lock_path=lock_path,
        short_teacher_video_url="http://192.168.29.12:5555/course/short-T.mp4",
        long_teacher_video_url="http://192.168.29.12:5555/course/long-T.mp4",
        long_slides_video_url="http://192.168.29.12:5555/course/long-P.mp4",
        fault_window_token=window_token,
        fault_window_opened_at=window_opened_at,
        baseline_ref=baseline_ref,
        action_ref=action_ref,
    )


def _publish_phase_witness(
    request: probe.ProbeRequest,
    phase: str,
    *,
    observations: dict[str, object] | None = None,
) -> str:
    phase_request = probe.ProbeRequest.build(
        campaign_id=request.campaign_id,
        case_id=request.case_id,
        scenario_id=request.scenario_id,
        phase=phase,
        check_index=0,
        challenge=request.challenge,
        targets=request.targets,
        release_root=request.release_root,
        evidence_root=request.evidence_root,
        lock_holder_pid=request.lock_holder_pid,
        lock_path=request.lock_path,
        short_teacher_video_url=request.short_teacher_video_url,
        long_teacher_video_url=request.long_teacher_video_url,
        long_slides_video_url=request.long_slides_video_url,
        fault_window_token=(request.fault_window_token if phase != "baseline" else None),
        fault_window_opened_at=(
            request.fault_window_opened_at if phase != "baseline" else None
        ),
    )
    payload = {
        "schema_version": 1,
        "campaign_id": request.campaign_id,
        "case_id": request.case_id,
        "scenario_id": request.scenario_id,
        "phase": phase,
        "check_index": 0,
        "challenge": request.challenge,
        "fault_window": (
            None
            if phase == "baseline"
            else {
                "token": request.fault_window_token,
                "opened_at": request.fault_window_opened_at,
            }
        ),
        "status": "passed",
        "targets": [target.to_dict() for target in request.targets],
        "observations": observations or {"phase_bound": True},
    }
    path, digest = probe.publish_json_once(
        phase_request,
        Path(f"{phase}-{request.scenario_id}.json"),
        payload,
    )
    return f"release:{path.relative_to(request.release_root).as_posix()}#sha256:{digest}"


def _instance(service: str, code: str, gpu: str | None) -> dict[str, object]:
    return {
        "instance_id": service,
        "operator_code": code,
        "capabilities": [code],
        "service_url": f"http://{service}:8000",
        "declared_capacity": 4,
        "labels": ({"gpu": gpu} if gpu is not None else {}),
        "lifecycle": "ONLINE",
        "inflight": 0,
        "model_ready": True,
        "last_heartbeat_at": "2026-08-25T00:00:00+00:00",
    }


def _capacity(
    service: str,
    code: str,
    *,
    active_lease_count: int = 0,
) -> dict[str, object]:
    return {
        "instance_id": service,
        "operator_code": code,
        "lifecycle": "ONLINE",
        "model_ready": True,
        "declared_capacity": _TOPOLOGY_CAPACITY[code],
        "reported_inflight": active_lease_count,
        "active_lease_count": active_lease_count,
        "schedulable_used": active_lease_count,
        "attribution_difference": 0,
        "capacity_mismatch": False,
    }


def _course_document(
    task_id: str,
    task_type: str,
    *,
    status: int = 10,
    node_status: int | None = None,
    include_nodes: bool = True,
    include_result: bool = True,
    node_updated_at: str = "2026-08-25T00:00:10+00:00",
) -> dict[str, object]:
    expected = {
        "ASR": ["ASR_TRANSCRIPTION"],
        "PPT": ["PPT_SLICE", "PPT_OCR"],
        "TEACHER_BEHAVIOR": ["TEACHER_BEHAVIOR_ANALYSIS"],
    }[task_type]
    return {
        "code": 0,
        "data": {
            "task_id": task_id,
            "tasks": [
                {
                    "task_type": task_type,
                    "status": status,
                    "nodes": (
                        [
                            {
                                "node_code": code,
                                "status": status if node_status is None else node_status,
                                "updated_at": node_updated_at,
                                **(
                                    {"result": {"ok": True}}
                                    if include_result
                                    and (status if node_status is None else node_status) == 60
                                    else {}
                                ),
                            }
                            for code in expected
                        ]
                        if include_nodes
                        else []
                    ),
                }
            ],
        },
    }


@dataclass
class _FakeHttp:
    json_documents: dict[str, object]
    text_documents: dict[str, str | list[str]] = field(default_factory=dict)
    post_documents: dict[str, probe.HttpObservation] = field(default_factory=dict)
    asr_sessions: list[dict[str, object]] = field(default_factory=list)
    persistent_session: dict[str, object] = field(default_factory=dict)
    submitted_tasks: dict[str, str] = field(default_factory=dict)
    submitted_task_nodes: bool = True
    submitted_task_result: bool = True
    task_status: int = 10
    task_node_status: int | None = None
    task_node_updated_at: str = "2026-08-25T00:00:10+00:00"
    active_trace_routes: dict[str, str] = field(default_factory=dict)
    post_delay_seconds: float = 0.0
    active_trace_ids: set[str] = field(default_factory=set)
    calls: list[tuple[str, str]] = field(default_factory=list)

    def get_json(self, url: str) -> object:
        self.calls.append(("GET", url))
        if url.endswith("/active-leases") and (
            self.active_trace_routes or url not in self.json_documents
        ):
            instance_id = url.rsplit("/", 2)[1]
            leases = [
                {
                    "lease_id": f"lease-{trace_id}",
                    "instance_id": instance_id,
                    "work_context": {"trace_id": trace_id},
                }
                for trace_id in tuple(self.active_trace_ids)
                if self.active_trace_routes.get(trace_id) == instance_id
            ]
            return {
                "instance_id": instance_id,
                "active_lease_count": len(leases),
                "leases": leases,
            }
        if url.startswith(f"{probe.CONTROL_JOBS_URL}/"):
            task_id = url.rsplit("/", 1)[1]
            return _course_document(
                task_id,
                self.submitted_tasks[task_id],
                status=self.task_status,
                node_status=self.task_node_status,
                include_nodes=self.submitted_task_nodes,
                include_result=self.submitted_task_result,
                node_updated_at=self.task_node_updated_at,
            )
        return self.json_documents[url]

    def get_text(self, url: str) -> str:
        self.calls.append(("GET", url))
        document = self.text_documents[url]
        if isinstance(document, list):
            return document.pop(0)
        return document

    def post_json(
        self,
        url: str,
        payload: object,
        *,
        headers: object | None = None,
    ) -> probe.HttpObservation:
        self.calls.append(("POST", url))
        if url == probe.CONTROL_JOBS_URL:
            assert isinstance(payload, dict)
            task_id = str(payload["task_id"])
            task_type = str(payload["task_types"][0])
            self.submitted_tasks[task_id] = task_type
            return probe.HttpObservation(
                200,
                {"code": 0, "data": {"task_id": task_id, "tasks": []}},
            )
        trace_id = None
        if isinstance(headers, Mapping) and type(headers.get("X-Trace-ID")) is str:
            trace_id = str(headers["X-Trace-ID"])
            self.active_trace_ids.add(trace_id)
        try:
            if self.post_delay_seconds:
                time.sleep(self.post_delay_seconds)
            return self.post_documents[url]
        finally:
            if trace_id is not None:
                self.active_trace_ids.discard(trace_id)

    def probe_asr_lease(self, trace_id: str) -> dict[str, object]:
        self.calls.append(("WEBSOCKET", probe.GATEWAY_ASR_URL))
        session = dict(self.asr_sessions.pop(0)) if self.asr_sessions else {}
        session["trace_id"] = trace_id
        return session

    def probe_asr_leases(
        self,
        trace_ids: Sequence[str],
    ) -> tuple[dict[str, object], ...]:
        return tuple(self.probe_asr_lease(trace_id) for trace_id in trace_ids)

    def prepare_persistent_asr(
        self,
        request: probe.ProbeRequest,
        trace_id: str,
    ) -> dict[str, object]:
        del request
        self.calls.append(("WEBSOCKET", probe.GATEWAY_ASR_URL))
        return {**self.persistent_session, "trace_id": trace_id}

    def persistent_asr_state(
        self,
        request: probe.ProbeRequest,
        trace_id: str,
    ) -> dict[str, object]:
        del request
        return {**self.persistent_session, "trace_id": trace_id}


def _execute(request: probe.ProbeRequest, client: _FakeHttp) -> dict[str, object]:
    return probe.execute_probe(
        request,
        client=client,
        lock_validator=lambda _release, _holder, _path: None,
    )


def _only_ref(response: dict[str, object]) -> str:
    references = response["evidence_refs"]
    assert isinstance(references, list) and len(references) == 1
    return str(references[0])


def _control_documents(
    instances: list[dict[str, object]],
    capacities: list[dict[str, object]],
    *,
    outbox_pending: int = 0,
) -> dict[str, object]:
    return {
        probe.CONTROL_READINESS_URL: {"status": "ready", "checks": {}},
        probe.OPERATOR_INSTANCES_URL: instances,
        probe.OPERATOR_CAPACITY_URL: capacities,
        probe.QUEUES_URL: {"queues": [], "outbox_pending": outbox_pending},
    }


def _full_inventory() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    instances: list[dict[str, object]] = []
    capacities: list[dict[str, object]] = []
    for code, prefix in probe.ALL_OPERATOR_PREFIXES.items():
        device = "cpu" if code == "ppt_slice" else "gpu"
        for index in range(3):
            service = f"{prefix}-{device}{index}"
            instances.append(_instance(service, code, None if device == "cpu" else str(index)))
            capacities.append(_capacity(service, code))
    return instances, capacities


def _baseline_documents() -> dict[str, object]:
    instances, capacities = _full_inventory()
    documents = _control_documents(instances, capacities)
    documents.update(
        {
            probe.ORCHESTRATOR_READINESS_URL: {"status": "ready", "checks": {}},
            probe.VISION_READINESS_URL: {"status": "ready"},
            probe.GATEWAY_READINESS_URL: {"status": "ready"},
            probe.GATEWAY_HEALTH_URL: {
                "service": "online-gateway-service",
                "status": "ok",
            },
        }
    )
    return documents


def test_request_rejects_mismatched_case_challenge_and_target_identity(tmp_path: Path) -> None:
    with pytest.raises(probe.ProbeValidationError, match="case"):
        _request(tmp_path, case_id="RECOVERY-OPERATOR-VBAS")
    with pytest.raises(probe.ProbeValidationError, match="challenge"):
        request = _request(tmp_path)
        probe.ProbeRequest.build(
            campaign_id=request.campaign_id,
            case_id=request.case_id,
            scenario_id=request.scenario_id,
            phase=request.phase,
            check_index=request.check_index,
            challenge="stale",
            targets=request.targets,
            release_root=request.release_root,
            evidence_root=request.evidence_root,
            lock_holder_pid=request.lock_holder_pid,
            lock_path=request.lock_path,
            short_teacher_video_url=request.short_teacher_video_url,
            long_teacher_video_url=request.long_teacher_video_url,
            long_slides_video_url=request.long_slides_video_url,
            fault_window_token=request.fault_window_token,
            fault_window_opened_at=request.fault_window_opened_at,
        )
    with pytest.raises(probe.ProbeValidationError, match="target"):
        _request(tmp_path, targets=(_target("ocr-*"),))


def test_action_reference_must_match_the_exact_fault_window(tmp_path: Path) -> None:
    foreign_window = _request(tmp_path, fault_window_token="d" * 32)
    action_ref = _publish_phase_witness(foreign_window, "action")
    request = _request(tmp_path, action_ref=action_ref)

    assert probe._read_action_observations(request) is None


def test_operator_recovery_routes_a_northbound_workload_to_the_restored_instance(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    baseline_ref = _publish_phase_witness(request, "baseline")
    request = _request(tmp_path, baseline_ref=baseline_ref)
    instances = [_instance(f"ocr-gpu{gpu}", "ocr", str(gpu)) for gpu in range(3)]
    capacities = [_capacity(f"ocr-gpu{gpu}", "ocr") for gpu in range(3)]
    trace_id = probe._witness_id(request, "recovery-ocr-batch-trace", 0)
    client = _FakeHttp(
        _control_documents(instances, capacities),
        {
            probe.GATEWAY_METRICS_URL: [
                    "",
                    'algorithm_capacity_lease_events_total{capability="ocr",'
                    'instance_id="ocr-gpu0",outcome="acquired"} 1.0\n'
                    'algorithm_capacity_lease_events_total{capability="ocr",'
                    'instance_id="ocr-gpu0",outcome="released"} 1.0\n',
            ]
        },
        post_documents={
            "http://127.0.0.1:18103/api/online/ocr/recognize": probe.HttpObservation(
                200,
                {"code": 0, "data": {}},
            )
        },
        active_trace_routes={trace_id: "ocr-gpu0"},
        post_delay_seconds=0.02,
    )

    response = _execute(request, client)

    assert response["status"] == "passed"
    assert client.calls
    assert ("POST", "http://127.0.0.1:18103/api/online/ocr/recognize") in client.calls
    read_urls = {url for method, url in client.calls if method == "GET"}
    assert all(probe.UrllibReadOnlyClient._allowed_read_url(url) for url in read_urls)
    assert response["targets"] == [request.targets[0].to_dict()]


def test_background_metrics_cannot_replace_a_trace_bound_active_lease(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    capacities = {
        service: _capacity(service, "ocr")
        for service in probe._operator_services("ocr")
    }
    background_metrics = (
        'algorithm_capacity_lease_events_total{capability="ocr",instance_id="ocr-gpu0",'
        'outcome="acquired"} 20.0\n'
        'algorithm_capacity_lease_events_total{capability="ocr",instance_id="ocr-gpu0",'
        'outcome="released"} 20.0\n'
    )
    client = _FakeHttp(
        {},
        {probe.GATEWAY_METRICS_URL: ["", background_metrics]},
        post_documents={
            "http://127.0.0.1:18103/api/online/ocr/recognize": probe.HttpObservation(
                200,
                {"code": 0, "data": {}},
            )
        },
        post_delay_seconds=0.02,
    )

    evidence, reason = probe._active_operator_workload(
        request,
        client,
        "ocr",
        target_services={"ocr-gpu0"},
        capacities=capacities,
    )

    assert evidence is None
    assert reason is not None


@pytest.mark.parametrize(
    ("operator_code", "target_service"),
    (("asr_offline", "asr-offline-gpu1"), ("ppt_slice", "ppt-slice-cpu1")),
)
@pytest.mark.parametrize("phase", ("disruption", "recovery"))
def test_offline_operator_workload_uses_a_task_bound_active_lease(
    tmp_path: Path,
    operator_code: str,
    target_service: str,
    phase: str,
) -> None:
    request = _request(
        tmp_path,
        phase=phase,
        check_index=2,
    )
    services = sorted(probe._operator_services(operator_code))
    capacities = {
        service: _capacity(service, operator_code)
        for service in services
    }
    probe._recovery_route_width(
        operator_code,
        {target_service},
        capacities,
    )
    selected_service = target_service if phase == "recovery" else services[0]
    selected_attempt = services.index(target_service) if phase == "recovery" else 0
    task_id = probe._witness_id(
        request,
        f"{phase}-{operator_code}-routing",
        selected_attempt,
    )
    instances = [
        _instance(
            service,
            operator_code,
            None if "-cpu" in service else service.rsplit("gpu", 1)[1],
        )
        for service in services
        if phase == "recovery" or service != target_service
    ]
    documents: dict[str, object] = {probe.OPERATOR_INSTANCES_URL: instances}
    for instance in instances:
        instance_id = str(instance["instance_id"])
        lease = (
            [
                {
                    "lease_id": f"lease-{operator_code}-{phase}",
                    "instance_id": instance_id,
                    "work_context": {"task_id": task_id},
                }
            ]
            if instance_id == selected_service
            else []
        )
        documents[
            f"http://127.0.0.1:18100/ops/operator-instances/{instance_id}/active-leases"
        ] = {
            "instance_id": instance_id,
            "active_lease_count": len(lease),
            "leases": lease,
        }
    client = _FakeHttp(documents)

    evidence, reason = probe._active_operator_workload(
        request,
        client,
        operator_code,
        target_services={target_service},
        capacities=capacities,
    )

    assert reason is None
    assert evidence is not None
    assert evidence["participating_instance"] == selected_service
    assert ("POST", probe.CONTROL_JOBS_URL) in client.calls


@pytest.mark.parametrize(
    ("operator_code", "target_service"),
    (
        ("asr_online", "asr-online-gpu1"),
        ("ocr", "ocr-gpu1"),
        ("vbas", "vbas-gpu1"),
        ("facerec", "facerec-gpu1"),
        ("screen_det", "screen-det-gpu1"),
    ),
)
@pytest.mark.parametrize("phase", ("disruption", "recovery"))
def test_online_operator_workload_proves_exact_routed_instance(
    tmp_path: Path,
    operator_code: str,
    target_service: str,
    phase: str,
) -> None:
    request = _request(tmp_path, phase=phase, check_index=2)
    services = sorted(probe._operator_services(operator_code))
    capacities = {
        service: _capacity(service, operator_code)
        for service in services
    }
    workload_count = (
        probe._recovery_route_width(operator_code, {target_service}, capacities)
        if phase == "recovery"
        else 1
    )
    selected_service = (
        target_service
        if phase == "recovery"
        else sorted(set(services) - {target_service})[0]
    )
    if operator_code == "asr_online":
        routed_services = (
            services if phase == "recovery" else [selected_service] * workload_count
        )
        client = _FakeHttp(
            {},
            asr_sessions=[
                {"lease_id": f"lease-routed-{index}", "instance_id": service}
                for index, service in enumerate(routed_services)
            ],
        )
    else:
        capability, path, _kind = probe._ONLINE_WORKLOADS[operator_code]
        trace_ids = [
            probe._witness_id(
                request,
                f"{phase}-{operator_code}-batch-trace",
                attempt,
            )
            for attempt in range(workload_count)
        ]
        routed_services = services if phase == "recovery" else [selected_service]
        metrics = "".join(
            f'algorithm_capacity_lease_events_total{{capability="{capability}",'
            f'instance_id="{service}",outcome="{outcome}"}} 1.0\n'
            for service in routed_services
            for outcome in ("acquired", "released")
        )
        client = _FakeHttp(
            {},
            {probe.GATEWAY_METRICS_URL: ["", metrics]},
            post_documents={
                f"http://127.0.0.1:18103{path}": probe.HttpObservation(
                    200,
                    {"code": 0, "data": {}},
                )
            },
            active_trace_routes={
                trace_id: routed_services[index % len(routed_services)]
                for index, trace_id in enumerate(trace_ids)
            },
            post_delay_seconds=0.02,
        )

    evidence, reason = probe._active_operator_workload(
        request,
        client,
        operator_code,
        target_services={target_service},
        capacities=capacities,
    )

    assert reason is None
    assert evidence is not None
    assert evidence["participating_instance"] == selected_service
    if operator_code != "asr_online":
        assert client.calls.count(("POST", f"http://127.0.0.1:18103{path}")) == workload_count


@pytest.mark.parametrize(
    ("target_service", "expected_width"),
    (("ocr-gpu0", 3), ("ocr-gpu1", 3), ("ocr-gpu2", 3)),
)
def test_recovery_width_covers_one_live_load_round_robin_cycle(
    target_service: str,
    expected_width: int,
) -> None:
    assert "lowest_load_candidates" in _LEASE_SCRIPT
    capacities = {
        f"ocr-gpu{index}": _capacity(f"ocr-gpu{index}", "ocr")
        for index in range(3)
    }

    assert (
        probe._recovery_route_width("ocr", {target_service}, capacities)
        == expected_width
    )


@pytest.mark.parametrize("operator_code", tuple(_TOPOLOGY_CAPACITY))
def test_recovery_width_is_one_cycle_regardless_of_topology_capacity(operator_code: str) -> None:
    prefix = probe.ALL_OPERATOR_PREFIXES[operator_code]
    device = "cpu" if operator_code == "ppt_slice" else "gpu"
    topology_capacity = {
        item.operator_code: item.declared_capacity
        for item in CURRENT_TOPOLOGY.operators
    }
    assert _TOPOLOGY_CAPACITY[operator_code] == topology_capacity[operator_code]
    capacities = {
        f"{prefix}-{device}{index}": _capacity(
            f"{prefix}-{device}{index}",
            operator_code,
        )
        for index in range(3)
    }

    assert probe._recovery_route_width(
        operator_code,
        {f"{prefix}-{device}2"},
        capacities,
    ) == 3


def test_gpu_disruption_does_not_infer_takeover_from_ttl_and_capacity_alone(
    tmp_path: Path,
) -> None:
    targets = tuple(
        _target(service, index)
        for index, service in enumerate(
            (
                "asr-offline-gpu1",
                "asr-online-gpu1",
                "ocr-gpu1",
                "vbas-gpu1",
                "facerec-gpu1",
                "screen-det-gpu1",
            ),
            start=1,
        )
    )
    request = _request(
        tmp_path,
        case_id="RECOVERY-GPU-1",
        scenario_id="fault-gpu-1",
        phase="disruption",
        check_index=2,
        targets=targets,
    )
    baseline_ref = _publish_phase_witness(request, "baseline")
    request = _request(
        tmp_path,
        case_id="RECOVERY-GPU-1",
        scenario_id="fault-gpu-1",
        phase="disruption",
        check_index=2,
        targets=targets,
        baseline_ref=baseline_ref,
    )
    instances: list[dict[str, object]] = []
    capacities: list[dict[str, object]] = []
    for code, prefix in probe.GPU_OPERATOR_PREFIXES.items():
        for gpu in (0, 2):
            service = f"{prefix}-gpu{gpu}"
            instances.append(_instance(service, code, str(gpu)))
            capacities.append(_capacity(service, code))
    client = _FakeHttp(_control_documents(instances, capacities))

    assert _execute(request, client)["status"] == "pending"
    instances.append(_instance("ocr-gpu1", "ocr", "1"))
    blocked = probe.evaluate_request(request, _FakeHttp(_control_documents(instances, capacities)))
    assert blocked.status == "pending"


@pytest.mark.parametrize("gpu_index", (0, 1, 2))
def test_each_gpu_group_requires_all_six_active_operator_workloads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    gpu_index: int,
) -> None:
    targets = tuple(
        _target(f"{prefix}-gpu{gpu_index}", index)
        for index, prefix in enumerate(probe.GPU_OPERATOR_PREFIXES.values(), start=1)
    )
    request = _request(
        tmp_path,
        case_id=f"RECOVERY-GPU-{gpu_index}",
        scenario_id=f"fault-gpu-{gpu_index}",
        phase="disruption",
        check_index=2,
        targets=targets,
    )
    baseline_ref = _publish_phase_witness(request, "baseline")
    request = _request(
        tmp_path,
        case_id=request.case_id,
        scenario_id=request.scenario_id,
        phase="disruption",
        check_index=2,
        targets=targets,
        baseline_ref=baseline_ref,
    )
    instances: list[dict[str, object]] = []
    capacities: list[dict[str, object]] = []
    for code, prefix in probe.GPU_OPERATOR_PREFIXES.items():
        for other_gpu in {0, 1, 2} - {gpu_index}:
            service = f"{prefix}-gpu{other_gpu}"
            instances.append(_instance(service, code, str(other_gpu)))
            capacities.append(_capacity(service, code))
    witnessed: list[str] = []

    def fake_workload(
        _request_value: probe.ProbeRequest,
        _client: probe.ReadOnlyHttpClient,
        operator_code: str,
        *,
        target_services: set[str],
        capacities: Mapping[str, Mapping[str, object]],
    ) -> tuple[dict[str, object], None]:
        del capacities
        witnessed.append(operator_code)
        return {
            "operator_code": operator_code,
            "participating_instance": sorted(
                probe._operator_services(operator_code) - target_services
            )[0],
        }, None

    monkeypatch.setattr(probe, "_active_operator_workload", fake_workload)
    decision = probe.evaluate_request(
        request,
        _FakeHttp(_control_documents(instances, capacities)),
    )

    assert decision.status == "passed"
    assert set(witnessed) == set(probe.GPU_OPERATOR_PREFIXES)


@pytest.mark.parametrize(
    ("case_id", "scenario_id", "service", "readiness_url"),
    (
        (
            "RECOVERY-PLATFORM-CONTROL",
            "fault-platform-01",
            "control-service",
            probe.CONTROL_READINESS_URL,
        ),
        (
            "RECOVERY-PLATFORM-ORCHESTRATOR",
            "fault-platform-02",
            "orchestrator-service",
            probe.ORCHESTRATOR_READINESS_URL,
        ),
        (
            "RECOVERY-PLATFORM-VISION",
            "fault-platform-03",
            "vision-orchestrator-service",
            probe.VISION_READINESS_URL,
        ),
        (
            "RECOVERY-PLATFORM-ONLINE-GATEWAY",
            "fault-platform-04",
            "online-gateway-service",
            probe.GATEWAY_READINESS_URL,
        ),
    ),
)
def test_platform_recovery_collects_service_and_queue_facts(
    tmp_path: Path,
    case_id: str,
    scenario_id: str,
    service: str,
    readiness_url: str,
) -> None:
    request = _request(
        tmp_path,
        case_id=case_id,
        scenario_id=scenario_id,
        phase="disruption",
        check_index=2,
        targets=(_target(service),),
    )
    baseline_ref = _publish_phase_witness(request, "baseline")
    action_ref = _publish_phase_witness(request, "action")
    request = _request(
        tmp_path,
        case_id=case_id,
        scenario_id=scenario_id,
        phase="disruption",
        check_index=2,
        targets=(_target(service),),
        baseline_ref=baseline_ref,
        action_ref=action_ref,
    )
    documents = _control_documents([], [])
    documents[readiness_url] = {"status": "ready", "checks": {}}
    if service == "online-gateway-service":
        documents[probe.GATEWAY_HEALTH_URL] = {
            "service": "online-gateway-service",
            "status": "ok",
        }
    client = _FakeHttp(
        documents,
        {probe.GATEWAY_METRICS_URL: "algorithm_capacity_lease_events_total 1\n"},
    )

    decision = probe.evaluate_request(request, client)

    assert decision.status == "passed"
    assert ("GET", probe.QUEUES_URL) in client.calls


def test_kafka_and_redis_checks_use_live_outbox_consumer_and_capacity_facts(
    tmp_path: Path,
) -> None:
    kafka = _request(
        tmp_path,
        case_id="RECOVERY-KAFKA",
        scenario_id="fault-kafka",
        targets=(_target("kafka"),),
    )
    kafka_docs = _control_documents([], [], outbox_pending=0)
    kafka_docs[probe.ORCHESTRATOR_READINESS_URL] = {
        "status": "ready",
        "checks": {"kafka": {"ready": True, "detail": "lag=0"}},
    }
    kafka_ref = _publish_phase_witness(kafka, "baseline")
    kafka_action_ref = _publish_phase_witness(kafka, "action")
    kafka = _request(
        tmp_path,
        case_id="RECOVERY-KAFKA",
        scenario_id="fault-kafka",
        phase="disruption",
        check_index=2,
        targets=(_target("kafka"),),
        baseline_ref=kafka_ref,
        action_ref=kafka_action_ref,
    )
    assert probe.evaluate_request(kafka, _FakeHttp(kafka_docs)).status == "passed"

    redis = _request(
        tmp_path,
        case_id="RECOVERY-REDIS",
        scenario_id="fault-redis",
        targets=(_target("redis"),),
    )
    instances: list[dict[str, object]] = []
    capacities: list[dict[str, object]] = []
    for code, prefix in probe.ALL_OPERATOR_PREFIXES.items():
        device = "cpu" if code == "ppt_slice" else "gpu"
        for index in range(3):
            service = f"{prefix}-{device}{index}"
            instances.append(_instance(service, code, None if device == "cpu" else str(index)))
            capacities.append(_capacity(service, code))
    redis_ref = _publish_phase_witness(redis, "baseline")
    redis_action_ref = _publish_phase_witness(redis, "action")
    redis = _request(
        tmp_path,
        case_id="RECOVERY-REDIS",
        scenario_id="fault-redis",
        phase="disruption",
        check_index=2,
        targets=(_target("redis"),),
        baseline_ref=redis_ref,
        action_ref=redis_action_ref,
    )
    assert (
        probe.evaluate_request(
            redis,
            _FakeHttp(_control_documents(instances, capacities)),
        ).status
        == "passed"
    )


@pytest.mark.parametrize(
    ("case_id", "scenario_id", "service"),
    (
        ("RECOVERY-PLATFORM-ORCHESTRATOR", "fault-platform-02", "orchestrator-service"),
        ("RECOVERY-KAFKA", "fault-kafka", "kafka"),
    ),
)
def test_restart_window_submission_is_retained_then_consumed_once(
    tmp_path: Path,
    case_id: str,
    scenario_id: str,
    service: str,
) -> None:
    target = (_target(service),)
    seed = _request(
        tmp_path,
        case_id=case_id,
        scenario_id=scenario_id,
        phase="baseline",
        check_index=0,
        targets=target,
    )
    baseline_ref = _publish_phase_witness(
        seed,
        "baseline",
        observations={"outbox_pending": 0, "active_witness": {"kind": "readiness_only"}},
    )
    action = _request(
        tmp_path,
        case_id=case_id,
        scenario_id=scenario_id,
        phase="action",
        check_index=0,
        targets=target,
        baseline_ref=baseline_ref,
    )
    action_readiness_url = (
        probe.ORCHESTRATOR_READINESS_URL
        if service in {"orchestrator-service", "kafka"}
        else probe.CONTROL_READINESS_URL
    )
    action_readiness = (
        {"status": "ready", "checks": {"kafka": {"ready": False}}}
        if service == "kafka"
        else {"status": "not_ready"}
    )
    action_client = _FakeHttp(
        {
            action_readiness_url: action_readiness,
            probe.QUEUES_URL: {"queues": [], "outbox_pending": 1},
        },
        submitted_task_nodes=False,
    )
    action_response = _execute(action, action_client)
    assert action_response["status"] == "passed"
    assert ("POST", probe.CONTROL_JOBS_URL) in action_client.calls

    recovery = _request(
        tmp_path,
        case_id=case_id,
        scenario_id=scenario_id,
        targets=target,
        baseline_ref=baseline_ref,
        action_ref=_only_ref(action_response),
    )
    recovery_documents = {
        probe.ORCHESTRATOR_READINESS_URL: {
            "status": "ready",
            "checks": {"kafka": {"ready": True, "detail": "lag=0"}},
        },
        probe.QUEUES_URL: {"queues": [], "outbox_pending": 7},
    }
    recovery_client = _FakeHttp(
        recovery_documents,
        submitted_tasks=dict(action_client.submitted_tasks),
        task_status=60,
    )
    recovery_response = _execute(recovery, recovery_client)
    assert recovery_response["status"] == "passed"


@pytest.mark.parametrize(
    ("case_id", "scenario_id", "service", "readiness_url", "task_type"),
    (
        (
            "RECOVERY-PLATFORM-CONTROL",
            "fault-platform-01",
            "control-service",
            probe.CONTROL_READINESS_URL,
            "ASR",
        ),
        (
            "RECOVERY-PLATFORM-VISION",
            "fault-platform-03",
            "vision-orchestrator-service",
            probe.VISION_READINESS_URL,
            "TEACHER_BEHAVIOR",
        ),
    ),
)
def test_platform_task_fact_survives_restart_without_duplicate_nodes(
    tmp_path: Path,
    case_id: str,
    scenario_id: str,
    service: str,
    readiness_url: str,
    task_type: str,
) -> None:
    target = (_target(service),)
    baseline = _request(
        tmp_path,
        case_id=case_id,
        scenario_id=scenario_id,
        phase="baseline",
        check_index=0,
        targets=target,
    )
    baseline_client = _FakeHttp(
        _baseline_documents(),
        {probe.GATEWAY_METRICS_URL: ""},
        task_status=50 if service == "vision-orchestrator-service" else 10,
    )
    baseline_response = _execute(baseline, baseline_client)
    assert baseline_response["status"] == "passed"

    action = _request(
        tmp_path,
        case_id=case_id,
        scenario_id=scenario_id,
        phase="action",
        check_index=0,
        targets=target,
        baseline_ref=_only_ref(baseline_response),
    )
    action_client = _FakeHttp(
        {readiness_url: {"status": "not_ready"}},
        submitted_tasks=dict(baseline_client.submitted_tasks),
    )
    action_response = _execute(action, action_client)
    assert action_response["status"] == "passed"

    recovery = _request(
        tmp_path,
        case_id=case_id,
        scenario_id=scenario_id,
        targets=target,
        baseline_ref=_only_ref(baseline_response),
        action_ref=_only_ref(action_response),
    )
    recovery_documents: dict[str, object] = {
        readiness_url: {"status": "ready"},
        probe.QUEUES_URL: {"queues": [], "outbox_pending": 0},
    }
    if service == "control-service":
        instances, _ = _full_inventory()
        for instance in instances:
            instance["last_heartbeat_at"] = "2026-08-25T00:01:00+00:00"
        recovery_documents[probe.OPERATOR_INSTANCES_URL] = instances
    recovery_client = _FakeHttp(
        recovery_documents,
        submitted_tasks=dict(baseline_client.submitted_tasks),
        task_status=60,
    )
    recovery_response = _execute(recovery, recovery_client)
    assert recovery_response["status"] == "passed"
    task_id = next(iter(recovery_client.submitted_tasks))
    fact = probe._task_fact(
        recovery_client.get_json(f"{probe.CONTROL_JOBS_URL}/{task_id}"),
        task_id,
        task_type,
    )
    assert fact["node_count"] == 1


@pytest.mark.parametrize(
    ("status", "include_result", "node_updated_at"),
    (
        (70, True, "2026-08-25T00:00:10+00:00"),
        (80, True, "2026-08-25T00:00:10+00:00"),
        (60, False, "2026-08-25T00:00:10+00:00"),
        (60, True, "2026-08-23T00:00:10+00:00"),
    ),
)
def test_visual_recovery_requires_success_and_one_nonempty_result_digest(
    tmp_path: Path,
    status: int,
    include_result: bool,
    node_updated_at: str,
) -> None:
    target = (_target("vision-orchestrator-service"),)
    seed = _request(
        tmp_path,
        case_id="RECOVERY-PLATFORM-VISION",
        scenario_id="fault-platform-03",
        targets=target,
    )
    task_id = "fault-visual-result-proof"
    baseline_ref = _publish_phase_witness(
        seed,
        "baseline",
        observations={
            "active_witness": {
                "kind": "visual_task",
                "fact": {
                    "task_id": task_id,
                    "node_codes": ["TEACHER_BEHAVIOR_ANALYSIS"],
                },
            }
        },
    )
    action_ref = _publish_phase_witness(seed, "action")
    request = _request(
        tmp_path,
        case_id=seed.case_id,
        scenario_id=seed.scenario_id,
        targets=target,
        baseline_ref=baseline_ref,
        action_ref=action_ref,
    )
    client = _FakeHttp(
        {
            probe.VISION_READINESS_URL: {"status": "ready"},
            probe.QUEUES_URL: {"queues": [], "outbox_pending": 4},
        },
        submitted_tasks={task_id: "TEACHER_BEHAVIOR"},
        submitted_task_result=include_result,
        task_status=status,
        task_node_updated_at=node_updated_at,
    )

    decision = probe.evaluate_request(request, client)

    assert decision.status == "pending"


def test_visual_baseline_rejects_a_task_that_already_completed(tmp_path: Path) -> None:
    target = (_target("vision-orchestrator-service"),)
    request = _request(
        tmp_path,
        case_id="RECOVERY-PLATFORM-VISION",
        scenario_id="fault-platform-03",
        phase="baseline",
        check_index=0,
        targets=target,
    )
    client = _FakeHttp(
        _baseline_documents(),
        {probe.GATEWAY_METRICS_URL: ""},
        task_status=60,
    )

    assert _execute(request, client)["status"] == "pending"


def test_control_heartbeats_must_reach_the_fault_window(tmp_path: Path) -> None:
    target = (_target("control-service"),)
    request = _request(
        tmp_path,
        case_id="RECOVERY-PLATFORM-CONTROL",
        scenario_id="fault-platform-01",
        targets=target,
        fault_window_opened_at="2026-08-25T00:00:05+00:00",
    )
    task_id = "fault-control-window-proof"
    instances, _ = _full_inventory()
    baseline_ref = _publish_phase_witness(
        request,
        "baseline",
        observations={
            "operator_heartbeats": {
                str(item["instance_id"]): "2026-08-24T23:59:00+00:00"
                for item in instances
            },
            "active_witness": {
                "kind": "control_task_fact",
                "fact": {
                    "task_id": task_id,
                    "node_codes": ["ASR_TRANSCRIPTION"],
                    "node_count": 1,
                },
            },
        },
    )
    action_ref = _publish_phase_witness(request, "action")
    request = _request(
        tmp_path,
        case_id=request.case_id,
        scenario_id=request.scenario_id,
        targets=target,
        baseline_ref=baseline_ref,
        action_ref=action_ref,
        fault_window_opened_at=request.fault_window_opened_at,
    )
    for item in instances:
        item["last_heartbeat_at"] = "2026-08-25T00:00:01+00:00"
    client = _FakeHttp(
        {
            probe.CONTROL_READINESS_URL: {"status": "ready"},
            probe.QUEUES_URL: {"queues": [], "outbox_pending": 2},
            probe.OPERATOR_INSTANCES_URL: instances,
        },
        submitted_tasks={task_id: "ASR"},
    )

    assert probe.evaluate_request(request, client).status == "pending"


def test_redis_rejection_is_request_bound_during_background_ocr_traffic_then_recovers(
    tmp_path: Path,
) -> None:
    target = (_target("redis"),)
    baseline = _request(
        tmp_path,
        case_id="RECOVERY-REDIS",
        scenario_id="fault-redis",
        phase="baseline",
        check_index=0,
        targets=target,
    )
    baseline_client = _FakeHttp(
        _baseline_documents(),
        {probe.GATEWAY_METRICS_URL: ""},
        persistent_session={
            "status": "connected",
            "lease_id": "redis-old-lease",
            "instance_id": "asr-online-gpu0",
        },
    )
    baseline_response = _execute(baseline, baseline_client)
    assert baseline_response["status"] == "passed"

    action = _request(
        tmp_path,
        case_id=baseline.case_id,
        scenario_id=baseline.scenario_id,
        phase="action",
        check_index=0,
        targets=target,
        baseline_ref=_only_ref(baseline_response),
    )
    redis_background_before = (
        'algorithm_capacity_lease_events_total{capability="ocr",instance_id="ocr-gpu0",'
        'outcome="acquired"} 10.0\n'
        'algorithm_capacity_lease_events_total{capability="ocr",instance_id="none",'
        'outcome="rejected"} 20.0\n'
    )
    rejected_metrics = (
        'algorithm_capacity_lease_events_total{capability="ocr",instance_id="ocr-gpu0",'
        'outcome="acquired"} 12.0\n'
        'algorithm_capacity_lease_events_total{capability="ocr",instance_id="none",'
        'outcome="rejected"} 23.0\n'
    )
    action_client = _FakeHttp(
        {probe.CONTROL_READINESS_URL: {"status": "not_ready"}},
        {probe.GATEWAY_METRICS_URL: [redis_background_before, rejected_metrics]},
        post_documents={
            "http://127.0.0.1:18103/api/online/ocr/recognize": probe.HttpObservation(
                200,
                {"code": 50301, "data": None},
            )
        },
    )
    action_response = _execute(action, action_client)
    assert action_response["status"] == "passed"

    instances, capacities = _full_inventory()
    recovery_documents = _control_documents(instances, capacities)
    for index in range(3):
        instance_id = f"asr-online-gpu{index}"
        recovery_documents[
            f"http://127.0.0.1:18100/ops/operator-instances/{instance_id}/active-leases"
        ] = {
            "instance_id": instance_id,
            "active_lease_count": 0,
            "leases": [],
        }
    for index in range(3):
        instance_id = f"ocr-gpu{index}"
        recovery_documents[
            f"http://127.0.0.1:18100/ops/operator-instances/{instance_id}/active-leases"
        ] = {
            "instance_id": instance_id,
            "active_lease_count": 0,
            "leases": [],
        }
    background_metrics = (
        rejected_metrics
        + 'algorithm_capacity_lease_events_total{capability="ocr",instance_id="ocr-gpu0",'
        'outcome="acquired"} 10.0\n'
        + 'algorithm_capacity_lease_events_total{capability="ocr",instance_id="ocr-gpu0",'
        'outcome="released"} 10.0\n'
    )
    recovered_metrics = (
        rejected_metrics
        + 'algorithm_capacity_lease_events_total{capability="ocr",instance_id="ocr-gpu0",'
        'outcome="acquired"} 12.0\n'
        + 'algorithm_capacity_lease_events_total{capability="ocr",instance_id="ocr-gpu0",'
        'outcome="released"} 12.0\n'
    )
    recovery_client = _FakeHttp(
        recovery_documents,
        {probe.GATEWAY_METRICS_URL: [background_metrics, recovered_metrics]},
        post_documents={
            "http://127.0.0.1:18103/api/online/ocr/recognize": probe.HttpObservation(
                200,
                {"code": 0, "data": {}},
            )
        },
    )
    recovery = _request(
        tmp_path,
        case_id=baseline.case_id,
        scenario_id=baseline.scenario_id,
        targets=target,
        baseline_ref=_only_ref(baseline_response),
        action_ref=_only_ref(action_response),
    )
    recovery_response = _execute(recovery, recovery_client)
    assert recovery_response["status"] == "passed"


def test_missing_or_malformed_semantic_facts_remain_pending(tmp_path: Path) -> None:
    request = _request(tmp_path)
    client = _FakeHttp(_control_documents([], []))

    decision = probe.evaluate_request(request, client)

    assert decision.status == "pending"
    assert decision.reasons


def test_evidence_publication_is_atomic_mode_restricted_and_never_overwrites(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    relative = Path("fixed.json")
    payload = {"schema_version": 1, "status": "pending"}

    path, digest = probe.publish_json_once(request, relative, payload)

    assert path.read_bytes() == probe.canonical_json_bytes(payload)
    assert digest == probe.sha256_file(path)
    metadata = path.stat()
    assert stat.S_IMODE(metadata.st_mode) == 0o600
    assert metadata.st_nlink == 1
    with pytest.raises(FileExistsError):
        probe.publish_json_once(request, relative, {"schema_version": 1, "status": "passed"})
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "pending"


def test_release_and_evidence_roots_reject_escape_and_symlink(tmp_path: Path) -> None:
    request = _request(tmp_path)
    with pytest.raises(probe.ProbeValidationError, match="evidence root"):
        probe.ProbeRequest.build(
            campaign_id=request.campaign_id,
            case_id=request.case_id,
            scenario_id=request.scenario_id,
            phase=request.phase,
            check_index=request.check_index,
            challenge=request.challenge,
            targets=request.targets,
            release_root=request.release_root,
            evidence_root=tmp_path / "outside",
            lock_holder_pid=request.lock_holder_pid,
            lock_path=request.lock_path,
            short_teacher_video_url=request.short_teacher_video_url,
            long_teacher_video_url=request.long_teacher_video_url,
            long_slides_video_url=request.long_slides_video_url,
            fault_window_token=request.fault_window_token,
            fault_window_opened_at=request.fault_window_opened_at,
        )
    linked = request.release_root / "linked"
    linked.symlink_to(tmp_path)
    with pytest.raises(probe.ProbeValidationError, match="symlink"):
        probe.ProbeRequest.build(
            campaign_id=request.campaign_id,
            case_id=request.case_id,
            scenario_id=request.scenario_id,
            phase=request.phase,
            check_index=request.check_index,
            challenge=request.challenge,
            targets=request.targets,
            release_root=request.release_root,
            evidence_root=linked / "fault-probes",
            lock_holder_pid=request.lock_holder_pid,
            lock_path=request.lock_path,
            short_teacher_video_url=request.short_teacher_video_url,
            long_teacher_video_url=request.long_teacher_video_url,
            long_slides_video_url=request.long_slides_video_url,
            fault_window_token=request.fault_window_token,
            fault_window_opened_at=request.fault_window_opened_at,
        )


def test_gateway_http_recovery_is_request_bound_during_background_ocr_traffic(
    tmp_path: Path,
) -> None:
    target = (_target("online-gateway-service"),)
    baseline = _request(
        tmp_path,
        case_id="RECOVERY-PLATFORM-ONLINE-GATEWAY",
        scenario_id="fault-platform-04",
        phase="baseline",
        check_index=0,
        targets=target,
    )
    instances: list[dict[str, object]] = []
    capacities: list[dict[str, object]] = []
    for code, prefix in probe.ALL_OPERATOR_PREFIXES.items():
        device = "cpu" if code == "ppt_slice" else "gpu"
        for index in range(3):
            service = f"{prefix}-{device}{index}"
            instances.append(_instance(service, code, None if device == "cpu" else str(index)))
            capacities.append(_capacity(service, code))
    baseline_documents = _control_documents(instances, capacities)
    baseline_documents.update(
        {
            probe.ORCHESTRATOR_READINESS_URL: {"status": "ready", "checks": {}},
            probe.VISION_READINESS_URL: {"status": "ready", "checks": {}},
            probe.GATEWAY_READINESS_URL: {"status": "ready"},
            probe.GATEWAY_HEALTH_URL: {
                "service": "online-gateway-service",
                "status": "ok",
            },
        }
    )
    baseline_client = _FakeHttp(
        baseline_documents,
        {probe.GATEWAY_METRICS_URL: ""},
        persistent_session={
            "status": "connected",
            "lease_id": "lease-old",
            "instance_id": "asr-online-gpu0",
            "connected_at": "2026-08-25T00:00:00+00:00",
        },
    )
    baseline_response = _execute(baseline, baseline_client)
    assert baseline_response["status"] == "passed"
    baseline_refs = baseline_response["evidence_refs"]
    assert isinstance(baseline_refs, list)
    baseline_ref = str(baseline_refs[0])

    action = _request(
        tmp_path,
        case_id=baseline.case_id,
        scenario_id=baseline.scenario_id,
        phase="action",
        check_index=0,
        targets=target,
        baseline_ref=baseline_ref,
    )
    action_client = _FakeHttp(
        {probe.GATEWAY_READINESS_URL: {"status": "not_ready"}},
        persistent_session={
            "status": "disconnected",
            "lease_id": "lease-old",
            "instance_id": "asr-online-gpu0",
            "connected_at": "2026-08-25T00:00:00+00:00",
            "disconnected_at": "2026-08-25T00:00:10+00:00",
        },
    )
    action_response = _execute(action, action_client)
    assert action_response["status"] == "passed"
    action_refs = action_response["evidence_refs"]
    assert isinstance(action_refs, list)
    action_ref = str(action_refs[0])

    recovery = _request(
        tmp_path,
        case_id=baseline.case_id,
        scenario_id=baseline.scenario_id,
        targets=target,
        baseline_ref=baseline_ref,
        action_ref=action_ref,
    )
    recovery_documents = {
        probe.GATEWAY_READINESS_URL: {"status": "ready"},
        probe.GATEWAY_HEALTH_URL: {
            "service": "online-gateway-service",
            "status": "ok",
        },
        probe.QUEUES_URL: {"queues": [], "outbox_pending": 0},
        probe.OPERATOR_INSTANCES_URL: [
            _instance(f"asr-online-gpu{index}", "asr_online", str(index))
            for index in range(3)
        ],
    }
    for index in range(3):
        instance_id = f"asr-online-gpu{index}"
        recovery_documents[
            f"http://127.0.0.1:18100/ops/operator-instances/{instance_id}/active-leases"
        ] = {
            "instance_id": instance_id,
            "active_lease_count": 0,
            "leases": [],
        }
    gateway_acquired_metrics = (
        'algorithm_capacity_lease_events_total{capability="ocr",instance_id="ocr-gpu0",'
        'outcome="acquired"} 13.0\n'
    )
    gateway_background_before = (
        'algorithm_capacity_lease_events_total{capability="ocr",instance_id="ocr-gpu0",'
        'outcome="acquired"} 10.0\n'
    )
    gateway_trace_id = probe._witness_id(recovery, "recovery-ocr-batch-trace", 0)
    recovery_client = _FakeHttp(
        recovery_documents,
        {
            probe.GATEWAY_METRICS_URL: [
                gateway_background_before,
                gateway_acquired_metrics,
                gateway_acquired_metrics,
            ]
        },
        post_documents={
            "http://127.0.0.1:18103/api/online/ocr/recognize": probe.HttpObservation(
                200,
                {"code": 0, "data": {}},
            )
        },
        asr_sessions=[
            {"lease_id": "lease-new", "instance_id": "asr-online-gpu1"}
        ],
        active_trace_routes={gateway_trace_id: "ocr-gpu0"},
        post_delay_seconds=0.02,
    )
    recovery_response = _execute(recovery, recovery_client)
    assert recovery_response["status"] == "passed"
    assert ("WEBSOCKET", probe.GATEWAY_ASR_URL) in recovery_client.calls
    assert (
        "POST",
        "http://127.0.0.1:18103/api/online/ocr/recognize",
    ) in recovery_client.calls
