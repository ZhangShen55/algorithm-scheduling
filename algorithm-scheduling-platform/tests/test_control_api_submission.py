from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from control_service.app.api.control import create_control_app
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from packages.platform_common.config import PlatformSettings
from packages.platform_common.repository import NodeRecord, TaskTypeRecord, TaskTypeWrite
from packages.platform_contracts.status import NodeStatus, Priority, TaskType


class RecordingRepository:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create_task_types(
        self,
        *,
        task_id: str,
        writes: list[TaskTypeWrite],
        input_snapshot: dict[str, Any] | None = None,
    ) -> list[TaskTypeRecord]:
        self.calls.append(
            {"task_id": task_id, "writes": writes, "input_snapshot": input_snapshot}
        )
        now = datetime.now(UTC)
        return [
            TaskTypeRecord(
                id=index,
                task_id=task_id,
                task_type=write.task_type,
                status=NodeStatus.PENDING,
                priority=write.priority,
                reason="任务已接收，等待处理",
                request_payload=write.request_payload,
                effective_params=write.effective_params,
                created=True,
                updated_at=now,
            )
            for index, write in enumerate(writes, start=1)
        ]

    def list_task_types(self, task_id: str) -> list[TaskTypeRecord]:
        return []

    def list_nodes(self, course_task_type_id: int) -> list[NodeRecord]:
        return []


class QueryRepository(RecordingRepository):
    def __init__(self) -> None:
        super().__init__()
        now = datetime.now(UTC)
        self.task_record = TaskTypeRecord(
            id=11,
            task_id="course-query-api",
            task_type=TaskType.PPT,
            status=NodeStatus.RUNNING,
            priority=Priority.NORMAL,
            reason="OCR 正在处理",
            request_payload={"slides_video_path": "http://media/ppt.mp4"},
            effective_params=None,
            created=False,
            updated_at=now,
        )
        self.nodes = [
            NodeRecord(
                id=101,
                course_task_type_id=11,
                node_code="PPT_SLICE",
                status=NodeStatus.COMPLETED,
                priority=Priority.NORMAL,
                reason="PPT 切片完成",
                required_capability="ppt_slice",
                result=None,
                artifact_path="/data/result/course-query-api/ppt/slices",
                artifact_count=20,
                progress={},
                effective_params=None,
                updated_at=now,
                claimed_at=now,
                started_at=now,
            ),
            NodeRecord(
                id=102,
                course_task_type_id=11,
                node_code="PPT_OCR",
                status=NodeStatus.RUNNING,
                priority=Priority.NORMAL,
                reason="已完成 5/20 张 OCR",
                required_capability="ocr",
                result={"items": [{"ppt_image_id": "ppt-001", "text": "第一章"}]},
                artifact_path=None,
                artifact_count=None,
                progress={"completed_count": 5, "total_count": 20},
                effective_params=None,
                updated_at=now,
                claimed_at=now,
                started_at=now,
            ),
        ]

    def list_task_types(self, task_id: str) -> list[TaskTypeRecord]:
        return [self.task_record] if task_id == self.task_record.task_id else []

    def list_nodes(self, course_task_type_id: int) -> list[NodeRecord]:
        return self.nodes if course_task_type_id == self.task_record.id else []


class StatefulRecordingRepository(RecordingRepository):
    def __init__(self) -> None:
        super().__init__()
        self.records: dict[tuple[str, TaskType], TaskTypeRecord] = {}

    def create_task_types(
        self,
        *,
        task_id: str,
        writes: list[TaskTypeWrite],
        input_snapshot: dict[str, Any] | None = None,
    ) -> list[TaskTypeRecord]:
        self.calls.append(
            {"task_id": task_id, "writes": writes, "input_snapshot": input_snapshot}
        )
        result: list[TaskTypeRecord] = []
        for write in writes:
            key = (task_id, write.task_type)
            existing = self.records.get(key)
            if existing is not None:
                result.append(
                    TaskTypeRecord(
                        id=existing.id,
                        task_id=existing.task_id,
                        task_type=existing.task_type,
                        status=existing.status,
                        priority=existing.priority,
                        reason=existing.reason,
                        request_payload=existing.request_payload,
                        effective_params=existing.effective_params,
                        created=False,
                        updated_at=existing.updated_at,
                    )
                )
                continue
            created = TaskTypeRecord(
                id=len(self.records) + 1,
                task_id=task_id,
                task_type=write.task_type,
                status=NodeStatus.PENDING,
                priority=write.priority,
                reason="任务已接收，等待处理",
                request_payload=write.request_payload,
                effective_params=write.effective_params,
                created=True,
                updated_at=datetime.now(UTC),
            )
            self.records[key] = created
            result.append(created)
        return result


class UnavailableRepository(RecordingRepository):
    def create_task_types(
        self,
        *,
        task_id: str,
        writes: list[TaskTypeWrite],
        input_snapshot: dict[str, Any] | None = None,
    ) -> list[TaskTypeRecord]:
        del task_id, writes, input_snapshot
        raise SQLAlchemyError("database unavailable")

    def list_task_types(self, task_id: str) -> list[TaskTypeRecord]:
        del task_id
        raise SQLAlchemyError("database unavailable")


class UnavailableNodeRepository(QueryRepository):
    def list_nodes(self, course_task_type_id: int) -> list[NodeRecord]:
        del course_task_type_id
        raise SQLAlchemyError("database unavailable")


def test_post_course_job_accepts_sparse_ppt_request(tmp_path: Path) -> None:
    repository = RecordingRepository()
    settings = PlatformSettings(course_root=tmp_path / "course", result_root=tmp_path / "result")
    app = create_control_app(repository=repository, settings=settings)

    with TestClient(app) as client:
        response = client.post(
            "/api/course-jobs",
            json={
                "task_id": "course-ppt",
                "task_types": ["PPT"],
                "slides_video_path": "http://media/ppt.mp4",
                "teacher_video_path": {"上游无关脏字段": True},
            },
        )

    body = response.json()
    assert response.status_code == 200
    assert body["code"] == 0
    assert body["data"]["task_id"] == "course-ppt"
    assert body["data"]["tasks"][0]["task_type"] == "PPT"
    assert repository.calls[0]["writes"][0].request_payload == {
        "slides_video_path": "http://media/ppt.mp4"
    }


def test_disabled_task_type_returns_business_validation_error(tmp_path: Path) -> None:
    repository = RecordingRepository()
    settings = PlatformSettings(course_root=tmp_path / "course", result_root=tmp_path / "result")
    app = create_control_app(
        repository=repository,
        settings=settings,
        enabled_task_types={TaskType.PPT, TaskType.ASR},
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/course-jobs",
            json={
                "task_id": "course-disabled-vision",
                "task_types": ["TEACHER_BEHAVIOR"],
                "teacher_video_path": "http://media/teacher.mp4",
            },
        )

    assert response.status_code == 200
    assert response.json()["code"] != 0
    assert response.json()["message"] == "任务类型未启用: TEACHER_BEHAVIOR"
    assert repository.calls == []


def test_enabled_task_type_is_accepted_without_registered_capacity(tmp_path: Path) -> None:
    repository = RecordingRepository()
    settings = PlatformSettings(course_root=tmp_path / "course", result_root=tmp_path / "result")
    app = create_control_app(
        repository=repository,
        settings=settings,
        enabled_task_types={TaskType.PPT},
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/course-jobs",
            json={
                "task_id": "course-enabled-without-capacity",
                "task_types": ["PPT"],
                "slides_video_path": "http://media/ppt.mp4",
            },
        )

    assert response.json()["code"] == 0
    assert repository.calls[0]["writes"][0].task_type is TaskType.PPT


def test_post_course_job_reports_selected_input_error_in_http_200(tmp_path: Path) -> None:
    repository = RecordingRepository()
    settings = PlatformSettings(course_root=tmp_path / "course", result_root=tmp_path / "result")
    app = create_control_app(repository=repository, settings=settings)

    with TestClient(app) as client:
        response = client.post(
            "/api/course-jobs",
            json={"task_id": "course-student", "task_types": ["STUDENT_BEHAVIOR"]},
        )

    body = response.json()
    assert response.status_code == 200
    assert body["code"] != 0
    assert "student_video_path" in body["message"]
    assert repository.calls == []


def test_post_course_job_uses_normal_priority_by_default(tmp_path: Path) -> None:
    repository = RecordingRepository()
    settings = PlatformSettings(course_root=tmp_path / "course", result_root=tmp_path / "result")
    app = create_control_app(repository=repository, settings=settings)

    with TestClient(app) as client:
        response = client.post(
            "/api/course-jobs",
            json={
                "task_id": "course-asr",
                "task_types": ["ASR"],
                "teacher_video_path": "http://media/teacher.mp4",
            },
        )

    assert response.json()["data"]["tasks"][0]["priority"] == Priority.NORMAL.value


def test_asr_options_merge_partial_override_over_documented_defaults(tmp_path: Path) -> None:
    repository = RecordingRepository()
    settings = PlatformSettings(course_root=tmp_path / "course", result_root=tmp_path / "result")
    app = create_control_app(repository=repository, settings=settings)

    with TestClient(app) as client:
        response = client.post(
            "/api/course-jobs",
            json={
                "task_id": "course-asr-options",
                "task_types": ["ASR"],
                "teacher_video_path": "http://media/teacher.mp4",
                "asr_options": {"showRoleIdentify": True, "hotWords": ["板书", "函数"]},
            },
        )

    assert response.json()["code"] == 0
    assert repository.calls[0]["writes"][0].effective_params == {
        "language": "auto",
        "showSpk": False,
        "showEmotion": False,
        "showRoleIdentify": True,
        "wordTimestamps": False,
        "hotWords": ["板书", "函数"],
    }


def test_asr_options_use_documented_defaults_when_omitted(tmp_path: Path) -> None:
    repository = RecordingRepository()
    settings = PlatformSettings(course_root=tmp_path / "course", result_root=tmp_path / "result")
    app = create_control_app(repository=repository, settings=settings)

    with TestClient(app) as client:
        response = client.post(
            "/api/course-jobs",
            json={
                "task_id": "course-asr-defaults",
                "task_types": ["ASR"],
                "teacher_video_path": "http://media/teacher.mp4",
            },
        )

    assert response.json()["code"] == 0
    assert repository.calls[0]["writes"][0].effective_params == {
        "language": "auto",
        "showSpk": False,
        "showEmotion": False,
        "showRoleIdentify": False,
        "wordTimestamps": False,
        "hotWords": [],
    }


def test_get_course_job_returns_all_task_types_and_node_results(tmp_path: Path) -> None:
    repository = QueryRepository()
    settings = PlatformSettings(course_root=tmp_path / "course", result_root=tmp_path / "result")
    app = create_control_app(repository=repository, settings=settings)

    with TestClient(app) as client:
        response = client.get("/api/course-jobs/course-query-api")

    body = response.json()
    assert response.status_code == 200
    assert body["code"] == 0
    assert [task["task_type"] for task in body["data"]["tasks"]] == [
        "PPT",
        "ASR",
        "TEACHER_BEHAVIOR",
        "STUDENT_BEHAVIOR",
    ]
    ppt = body["data"]["tasks"][0]
    assert ppt["nodes"][0]["path"] == "/data/result/course-query-api/ppt/slices"
    assert ppt["nodes"][0]["count"] == 20
    assert ppt["nodes"][1]["result"]["items"][0]["ppt_image_id"] == "ppt-001"
    assert ppt["nodes"][1]["progress"] == {"completed_count": 5, "total_count": 20}
    assert datetime.fromisoformat(ppt["nodes"][0]["claimed_at"]) == repository.nodes[
        0
    ].claimed_at
    assert datetime.fromisoformat(ppt["nodes"][0]["started_at"]) == repository.nodes[
        0
    ].started_at
    assert "result" not in ppt["nodes"][0]
    assert "path" not in ppt["nodes"][1]
    assert "count" not in ppt["nodes"][1]
    assert body["data"]["tasks"][1]["status"] == NodeStatus.UNREQUESTED.value
    assert body["data"]["tasks"][1]["reason"] == "未请求该任务"


def test_get_unknown_course_job_returns_business_not_found(tmp_path: Path) -> None:
    repository = QueryRepository()
    settings = PlatformSettings(course_root=tmp_path / "course", result_root=tmp_path / "result")
    app = create_control_app(repository=repository, settings=settings)

    with TestClient(app) as client:
        response = client.get("/api/course-jobs/not-found")

    assert response.status_code == 200
    assert response.json()["code"] != 0
    assert "未找到" in response.json()["message"]


def test_combined_asr_and_teacher_request_creates_two_scoped_writes(tmp_path: Path) -> None:
    repository = RecordingRepository()
    settings = PlatformSettings(course_root=tmp_path / "course", result_root=tmp_path / "result")
    app = create_control_app(repository=repository, settings=settings)

    with TestClient(app) as client:
        response = client.post(
            "/api/course-jobs",
            json={
                "task_id": "course-combined-teacher",
                "task_types": ["ASR", "TEACHER_BEHAVIOR"],
                "teacher_video_path": "http://media/teacher.mp4",
            },
        )

    assert response.json()["code"] == 0
    writes = repository.calls[0]["writes"]
    assert [write.task_type for write in writes] == [TaskType.ASR, TaskType.TEACHER_BEHAVIOR]
    assert all(
        write.request_payload["teacher_video_path"] == "http://media/teacher.mp4"
        for write in writes
    )


def test_student_request_preserves_exact_region_field_names(tmp_path: Path) -> None:
    repository = RecordingRepository()
    settings = PlatformSettings(course_root=tmp_path / "course", result_root=tmp_path / "result")
    app = create_control_app(repository=repository, settings=settings)
    front_points = [{"X": 0, "Y": 0}, {"X": 960, "Y": 0}, {"X": 960, "Y": 1080}]
    back_point = [{"X": 960, "Y": 0}, {"X": 1920, "Y": 0}, {"X": 1920, "Y": 1080}]

    with TestClient(app) as client:
        response = client.post(
            "/api/course-jobs",
            json={
                "task_id": "course-student-region",
                "task_types": ["STUDENT_BEHAVIOR"],
                "student_video_path": "http://media/student.mp4",
                "student_count": 38,
                "front_points": front_points,
                "back_point": back_point,
            },
        )

    assert response.json()["code"] == 0
    payload = repository.calls[0]["writes"][0].request_payload
    assert payload["student_count"] == 38
    assert payload["front_points"] == front_points
    assert payload["back_point"] == back_point


def test_duplicate_submission_returns_existing_task_type(tmp_path: Path) -> None:
    repository = StatefulRecordingRepository()
    settings = PlatformSettings(course_root=tmp_path / "course", result_root=tmp_path / "result")
    app = create_control_app(repository=repository, settings=settings)
    request = {
        "task_id": "course-duplicate",
        "task_types": ["PPT"],
        "slides_video_path": "http://media/ppt.mp4",
    }

    with TestClient(app) as client:
        first = client.post("/api/course-jobs", json=request).json()
        second = client.post("/api/course-jobs", json=request).json()

    assert first["data"]["tasks"][0]["created"] is True
    assert second["data"]["tasks"][0]["created"] is False


def test_post_course_job_returns_business_error_when_database_is_unavailable(
    tmp_path: Path,
) -> None:
    settings = PlatformSettings(course_root=tmp_path / "course", result_root=tmp_path / "result")
    app = create_control_app(repository=UnavailableRepository(), settings=settings)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/course-jobs",
            json={
                "task_id": "course-database-unavailable",
                "task_types": ["PPT"],
                "slides_video_path": "http://media/ppt.mp4",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "code": 50000,
        "message": "任务数据库暂不可用",
        "data": None,
    }


def test_get_course_job_returns_business_error_when_database_is_unavailable(
    tmp_path: Path,
) -> None:
    settings = PlatformSettings(course_root=tmp_path / "course", result_root=tmp_path / "result")
    app = create_control_app(repository=UnavailableRepository(), settings=settings)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/course-jobs/course-database-unavailable")

    assert response.status_code == 200
    assert response.json() == {
        "code": 50000,
        "message": "任务数据库暂不可用",
        "data": None,
    }


def test_get_course_job_returns_business_error_when_node_query_fails(
    tmp_path: Path,
) -> None:
    settings = PlatformSettings(course_root=tmp_path / "course", result_root=tmp_path / "result")
    app = create_control_app(repository=UnavailableNodeRepository(), settings=settings)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/course-jobs/course-query-api")

    assert response.status_code == 200
    assert response.json() == {
        "code": 50000,
        "message": "任务数据库暂不可用",
        "data": None,
    }
