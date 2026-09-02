from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from control_service.app.api.control import create_control_app
from fastapi.testclient import TestClient

from packages.platform_common.config import PlatformSettings
from packages.platform_common.operator_registry import (
    CapacityLease,
    OperatorActiveLeases,
    OperatorCode,
    OperatorInstance,
    OperatorInstanceNotFoundError,
    OperatorLifecycle,
)
from packages.platform_common.repository import (
    CourseJobSummary,
    CourseTaskSummary,
    NodeRecord,
    OperationsQueueSnapshot,
    QueueCount,
    TaskTypeRecord,
    TaskTypeWrite,
)
from packages.platform_contracts.status import NodeStatus, Priority, TaskType

REGISTRY_TOKEN = "operations-registry-token"
REGISTRY_HEADERS = {"X-Operator-Registry-Token": REGISTRY_TOKEN}


class OperationsRepository:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.task = TaskTypeRecord(
            id=1,
            task_id="course-ops",
            task_type=TaskType.PPT,
            status=NodeStatus.RUNNING,
            priority=Priority.URGENT,
            reason="OCR 正在处理",
            request_payload={"slides_video_path": "http://media/ppt.mp4"},
            effective_params=None,
            created=False,
            updated_at=now,
        )
        self.node = NodeRecord(
            id=2,
            course_task_type_id=1,
            node_code="PPT_OCR",
            status=NodeStatus.WAITING_OPERATOR,
            priority=Priority.URGENT,
            reason="等待算子能力可用: ocr",
            required_capability="ocr",
            result=None,
            artifact_path=None,
            artifact_count=None,
            progress={"completed_count": 3, "total_count": 20},
            effective_params=None,
            updated_at=now,
        )

    def create_task_types(
        self,
        *,
        task_id: str,
        writes: list[TaskTypeWrite],
        input_snapshot: dict[str, Any] | None = None,
    ) -> list[TaskTypeRecord]:
        return []

    def list_task_types(self, task_id: str) -> list[TaskTypeRecord]:
        return [self.task] if task_id == self.task.task_id else []

    def list_course_jobs(
        self,
        *,
        offset: int,
        limit: int,
        sort_by: str,
        descending: bool,
    ) -> tuple[list[CourseJobSummary], int]:
        assert (offset, limit, sort_by, descending) == (0, 10, "updated_at", True)
        return [
            CourseJobSummary(
                task_id=self.task.task_id,
                created_at=self.task.updated_at,
                updated_at=self.task.updated_at,
                task_types=(
                    CourseTaskSummary(
                        task_type=self.task.task_type,
                        status=self.task.status,
                        priority=self.task.priority,
                        updated_at=self.task.updated_at,
                    ),
                ),
            )
        ][offset : offset + limit], 1

    def list_nodes(self, course_task_type_id: int) -> list[NodeRecord]:
        return [self.node] if course_task_type_id == self.task.id else []

    def operations_queue_snapshot(self) -> OperationsQueueSnapshot:
        return OperationsQueueSnapshot(
            queues=(
                QueueCount(
                    status=NodeStatus.WAITING_OPERATOR,
                    priority=Priority.URGENT,
                    capability="ocr",
                    count=1,
                ),
            ),
            outbox_pending=2,
        )


class OperationsRegistry:
    def __init__(self) -> None:
        self.instance = OperatorInstance(
            instance_id="ocr-gpu0",
            operator_code=OperatorCode.OCR,
            capabilities=["ocr"],
            service_url="http://127.0.0.1:18082",
            declared_capacity=2,
            labels={"gpu": "0"},
            inflight=2,
        )

    def register(self, instance: OperatorInstance) -> OperatorInstance:
        self.instance = instance
        return instance

    def heartbeat(
        self,
        instance_id: str,
        *,
        inflight: int,
        model_ready: bool,
    ) -> OperatorInstance:
        return self.instance

    def unregister(self, instance_id: str) -> None:
        return None

    def list_instances(self) -> list[OperatorInstance]:
        return [self.instance]

    def active_lease_count(self, instance_id: str) -> int:
        assert instance_id == self.instance.instance_id
        return 0

    def list_active_leases(self, instance_id: str) -> OperatorActiveLeases:
        if instance_id != self.instance.instance_id:
            raise OperatorInstanceNotFoundError(instance_id)
        return OperatorActiveLeases(
            instance_id=instance_id,
            active_lease_count=0,
            reported_inflight=self.instance.inflight,
            attribution_difference=self.instance.inflight,
            leases=(),
        )

    def set_lifecycle(
        self,
        instance_id: str,
        lifecycle: OperatorLifecycle,
    ) -> OperatorInstance:
        if instance_id != self.instance.instance_id:
            raise OperatorInstanceNotFoundError(instance_id)
        self.instance = OperatorInstance(
            instance_id=self.instance.instance_id,
            operator_code=self.instance.operator_code,
            capabilities=self.instance.capabilities,
            service_url=self.instance.service_url,
            declared_capacity=self.instance.declared_capacity,
            labels=self.instance.labels,
            lifecycle=lifecycle,
        )
        return self.instance

    def lease(self, capability: str, ttl_seconds: int) -> CapacityLease:
        raise AssertionError("运维接口不应申请容量")

    def release(self, lease_id: str) -> None:
        return None


def _client(tmp_path: Path) -> TestClient:
    settings = PlatformSettings(
        course_root=tmp_path / "course",
        result_root=tmp_path / "result",
        operator_registry_token=REGISTRY_TOKEN,
    )
    return TestClient(
        create_control_app(
            repository=OperationsRepository(),
            operator_registry=OperationsRegistry(),
            settings=settings,
        )
    )


def test_operations_course_inspection_uses_real_not_found_status(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        found = client.get("/ops/course-jobs/course-ops")
        missing = client.get("/ops/course-jobs/missing")

    assert found.status_code == 200
    assert found.json()["task_id"] == "course-ops"
    assert found.json()["tasks"][0]["nodes"][0]["node_code"] == "PPT_OCR"
    assert missing.status_code == 404
    assert "未找到课程任务" in missing.json()["detail"]


def test_operations_course_job_list_uses_latest_default_pagination(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/ops/course-jobs")

    assert response.status_code == 200
    assert response.json()["page"] == 1
    assert response.json()["page_size"] == 10
    assert response.json()["sort_by"] == "updated_at"
    assert response.json()["order"] == "desc"
    assert response.json()["items"][0]["task_id"] == "course-ops"


def test_operations_cors_preflight_is_available(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.options(
            "/ops/operator-instances",
            headers={
                "Origin": "http://192.168.29.11:5174",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


def test_operations_active_leases_is_read_only_and_returns_empty_snapshot(
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        response = client.get("/ops/operator-instances/ocr-gpu0/active-leases")
        missing = client.get("/ops/operator-instances/missing/active-leases")

    assert response.status_code == 200
    assert response.json()["leases"] == []
    assert missing.status_code == 404


def test_operations_kafka_aggregates_or_degrades_without_breaking_outbox(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    from control_service.app.api import control

    class MetricsResponse:
        text = (
            'algorithm_outbox_publish_total{outcome="published"} 12\n'
            'algorithm_outbox_publish_total{outcome="failed"} 2\n'
            'algorithm_kafka_consumer_lag{topic="course",consumer_group="worker",partition="0"} 3\n'
        )

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(control.httpx, "get", lambda *args, **kwargs: MetricsResponse())
    settings = PlatformSettings(
        course_root=tmp_path / "course",
        result_root=tmp_path / "result",
        operator_registry_token=REGISTRY_TOKEN,
        orchestrator_metrics_url="http://orchestrator.test/metrics",
    )
    with TestClient(
        create_control_app(
            repository=OperationsRepository(),
            operator_registry=OperationsRegistry(),
            settings=settings,
        )
    ) as client:
        response = client.get("/ops/kafka")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["outbox_pending"] == 2
    assert response.json()["published"] == 12
    assert response.json()["publish_failed"] == 2
    assert response.json()["consumer_lag"] == 3

    def fail_metrics(*args: Any, **kwargs: Any) -> None:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(control.httpx, "get", fail_metrics)
    with TestClient(
        create_control_app(
            repository=OperationsRepository(),
            operator_registry=OperationsRegistry(),
            settings=settings,
        )
    ) as client:
        degraded = client.get("/ops/kafka")

    assert degraded.status_code == 200
    assert degraded.json()["status"] == "degraded"
    assert degraded.json()["publisher_status"] == "unavailable"
    assert degraded.json()["outbox_pending"] == 2


def test_operations_lists_and_drains_operator_instance(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        listed = client.get("/ops/operator-instances")
        unauthenticated = client.post("/ops/operator-instances/ocr-gpu0/drain")
        wrong_token = client.post(
            "/ops/operator-instances/ocr-gpu0/drain",
            headers={"X-Operator-Registry-Token": "wrong-registry-token"},
        )
        drained = client.post(
            "/ops/operator-instances/ocr-gpu0/drain",
            headers=REGISTRY_HEADERS,
        )
        missing = client.post(
            "/ops/operator-instances/missing/drain",
            headers=REGISTRY_HEADERS,
        )

    assert listed.status_code == 200
    assert listed.json()[0]["instance_id"] == "ocr-gpu0"
    assert unauthenticated.status_code == 401
    assert wrong_token.status_code == 401
    assert drained.status_code == 200
    assert drained.json()["lifecycle"] == "DRAINING"
    assert missing.status_code == 404


def test_operations_operator_snapshot_exposes_capacity_mismatch(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/ops/operator-instances/snapshot")

    assert response.status_code == 200
    assert response.json() == [
        {
            "instance_id": "ocr-gpu0",
            "operator_code": "ocr",
            "lifecycle": "ONLINE",
            "model_ready": True,
            "declared_capacity": 2,
            "reported_inflight": 2,
            "active_lease_count": 0,
            "schedulable_used": 2,
            "attribution_difference": 2,
            "capacity_mismatch": True,
            "capacity_pools": {"default": 2},
            "inflight_by_pool": {},
        }
    ]


def test_operations_queue_snapshot_exposes_priority_capability_and_outbox(
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        response = client.get("/ops/queues")

    assert response.status_code == 200
    assert response.json() == {
        "queues": [
            {
                "status": 30,
                "status_text": "等待算子",
                "priority": "URGENT",
                "capability": "ocr",
                "count": 1,
            }
        ],
        "outbox_pending": 2,
    }


def test_operations_storage_reports_configured_paths_and_directory_bytes(
    tmp_path: Path,
) -> None:
    course_root = tmp_path / "course"
    result_root = tmp_path / "result"
    course_root.mkdir()
    result_root.mkdir()
    (course_root / "input.bin").write_bytes(b"12345")
    (result_root / "result.bin").write_bytes(b"1234567")

    with _client(tmp_path) as client:
        response = client.get("/ops/storage")

    assert response.status_code == 200
    roots = {item["kind"]: item for item in response.json()["roots"]}
    assert roots["course"]["path"] == str(course_root)
    assert roots["course"]["directory_bytes"] == 5
    assert roots["result"]["directory_bytes"] == 7
    assert roots["course"]["filesystem"]["total"] > 0
    assert roots["course"]["filesystem"]["free"] > 0
