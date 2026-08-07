from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from packages.platform_common.config import PlatformSettings
from packages.platform_common.operator_registry import (
    CapacityLease,
    OperatorCode,
    OperatorInstance,
    OperatorInstanceNotFoundError,
    OperatorLifecycle,
)
from packages.platform_common.repository import (
    NodeRecord,
    OperationsQueueSnapshot,
    QueueCount,
    TaskTypeRecord,
    TaskTypeWrite,
)
from packages.platform_contracts.status import NodeStatus, Priority, TaskType
from services.control_service.api import create_control_app


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


def test_operations_lists_and_drains_operator_instance(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        listed = client.get("/ops/operator-instances")
        drained = client.post("/ops/operator-instances/ocr-gpu0/drain")
        missing = client.post("/ops/operator-instances/missing/drain")

    assert listed.status_code == 200
    assert listed.json()[0]["instance_id"] == "ocr-gpu0"
    assert drained.status_code == 200
    assert drained.json()["lifecycle"] == "DRAINING"
    assert missing.status_code == 404


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
