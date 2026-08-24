from __future__ import annotations

import asyncio
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from control_service.app.api.control import create_control_app
from fastapi.testclient import TestClient
from orchestrator_service.app.application.dispatcher import NodeDispatcher
from orchestrator_service.app.application.outbox import OutboxPublisher
from orchestrator_service.app.application.pipeline import PipelineInitializer, pipeline_nodes
from orchestrator_service.app.domain.ppt_work import PptImageWork, PptWorkLimits
from orchestrator_service.app.infrastructure.ppt_text import PptTextPipeline
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError
from vision_orchestrator_service.app.domain.adaptive_scan import (
    AdaptiveScanConfig,
    AdaptiveScanPlanner,
    BehaviorInterval,
)
from vision_orchestrator_service.app.domain.behavior_intervals import (
    TeacherBehaviorAggregationConfig,
    build_teacher_behavior_result,
)
from vision_orchestrator_service.app.domain.student_aggregation import (
    StudentAggregationConfig,
    StudentBehaviorAggregator,
    StudentFrameObservation,
)

from packages.platform_common.config import PlatformSettings
from packages.platform_common.operator_registry import (
    CapacityLease,
    UnavailableOperatorRegistry,
)
from packages.platform_common.repository import (
    CourseRepository,
    NodeResultWrite,
    NodeWorkItemWrite,
    RepositoryNotFoundError,
    TaskTypeWrite,
)
from packages.platform_common.state_machine import InvalidNodeTransition
from packages.platform_contracts.status import NodeStatus, Priority, TaskType

pytestmark = pytest.mark.integration

if TYPE_CHECKING:
    from conftest import Milestone1Postgres


@pytest.fixture(scope="session")
def database_engine(milestone1_postgres: Milestone1Postgres) -> Engine:
    return milestone1_postgres.engine


@pytest.fixture
def repository(database_engine: Engine) -> Iterator[CourseRepository]:
    with database_engine.begin() as connection:
        for table in (
            "operator_instance_events",
            "operator_instances",
            "outbox_events",
            "visual_fallback_values",
            "node_work_items",
            "node_results",
            "task_nodes",
            "course_task_types",
            "course_jobs",
        ):
            connection.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
    yield CourseRepository(database_engine)


def test_sparse_task_type_creation_is_idempotent(repository: CourseRepository) -> None:
    first = repository.create_task_types(
        task_id="course-001",
        writes=[
            TaskTypeWrite(
                task_type=TaskType.PPT,
                request_payload={"slides_video_path": "http://media/ppt.mp4"},
            )
        ],
    )
    appended = repository.create_task_types(
        task_id="course-001",
        writes=[
            TaskTypeWrite(
                task_type=TaskType.PPT,
                request_payload={"slides_video_path": "http://changed/ppt.mp4"},
            ),
            TaskTypeWrite(
                task_type=TaskType.ASR,
                priority=Priority.URGENT,
                request_payload={"teacher_video_path": "http://media/teacher.mp4"},
            ),
        ],
    )

    assert [item.created for item in first] == [True]
    assert [item.created for item in appended] == [False, True]
    assert appended[0].request_payload == {"slides_video_path": "http://media/ppt.mp4"}
    assert appended[1].priority is Priority.URGENT
    assert repository.count_courses() == 1


def test_node_result_and_completion_are_persisted_together(
    repository: CourseRepository,
) -> None:
    task_type = repository.create_task_types(
        task_id="course-asr",
        writes=[TaskTypeWrite(task_type=TaskType.ASR)],
    )[0]
    node = repository.create_node(
        course_task_type_id=task_type.id,
        node_code="ASR_TRANSCRIPTION",
        status=NodeStatus.PENDING,
        priority=Priority.NORMAL,
        reason="等待语音转写",
        required_capability="asr_offline",
    )
    repository.update_node_state(node.id, NodeStatus.QUEUED, "语音转写已排队")
    repository.update_node_state(node.id, NodeStatus.RUNNING, "正在进行语音转写")
    asr_result = {"language": "zh", "segments": [], "text": "课堂内容"}
    effective_params = {"showSpk": True, "showRoleIdentify": False}

    repository.complete_node(
        node.id,
        NodeResultWrite(result=asr_result, effective_params=effective_params),
        reason="语音转写完成",
    )

    completed = repository.get_node(node.id)
    assert completed.status is NodeStatus.COMPLETED
    assert completed.reason == "语音转写完成"
    assert completed.result == asr_result
    assert completed.effective_params == effective_params


def test_running_visual_node_progress_uses_platform_node_result(
    repository: CourseRepository,
) -> None:
    task_type = repository.create_task_types(
        task_id="course-visual-progress",
        writes=[TaskTypeWrite(task_type=TaskType.TEACHER_BEHAVIOR)],
    )[0]
    node = repository.create_node(
        course_task_type_id=task_type.id,
        node_code="TEACHER_BEHAVIOR_ANALYSIS",
        status=NodeStatus.RUNNING,
        priority=Priority.NORMAL,
        reason="教师行为分析中",
    )

    updated = repository.update_node_progress(
        node.id,
        {"percent": 35, "stage": "粗粒度扫描"},
        reason="教师视频粗粒度扫描中",
    )

    assert updated.status is NodeStatus.RUNNING
    assert updated.reason == "教师视频粗粒度扫描中"
    assert updated.progress == {"percent": 35, "stage": "粗粒度扫描"}


def test_visual_fallback_value_is_created_once_and_reused(
    repository: CourseRepository,
) -> None:
    task_type = repository.create_task_types(
        task_id="course-student-fallback",
        writes=[TaskTypeWrite(task_type=TaskType.STUDENT_BEHAVIOR)],
    )[0]

    first = repository.get_or_create_visual_fallback(
        task_type.id,
        "FRONT_OCCUPANCY_RATIO",
        0.12,
    )
    duplicate = repository.get_or_create_visual_fallback(
        task_type.id,
        "FRONT_OCCUPANCY_RATIO",
        0.15,
    )

    assert first == duplicate == 0.12


def test_deterministic_visual_refinement_preserves_exact_boundaries_and_tolerates_one_frame(
    repository: CourseRepository,
) -> None:
    del repository
    planner = AdaptiveScanPlanner(
        AdaptiveScanConfig(
            coarse_interval_seconds=30,
            refinement_intervals_seconds=(10, 5, 2, 1),
        )
    )

    def detector(points: Iterator[float]) -> dict[float, bool]:
        return {
            point: 1_190 <= point < 1_206 and point != 1_198
            for point in points
        }

    scan = planner.scan(duration_seconds=2_400, detector=detector)
    outcome = build_teacher_behavior_result(
        intervals={"writing": list(scan.intervals)},
        valid_frame_count=scan.evaluated_point_count,
        total_frame_count=scan.evaluated_point_count,
        config=TeacherBehaviorAggregationConfig(),
    )

    assert scan.intervals == (
        BehaviorInterval(1_190, 1_198),
        BehaviorInterval(1_199, 1_206),
    )
    assert outcome.result["writing_intervals"] == [
        {"start_seconds": 1_190, "end_seconds": 1_206}
    ]


def test_deterministic_teacher_gap_rules_merge_writing_and_sitting_boundaries(
    repository: CourseRepository,
) -> None:
    del repository
    outcome = build_teacher_behavior_result(
        intervals={
            "writing": [BehaviorInterval(1, 9), BehaviorInterval(12, 21)],
            "sitting": [BehaviorInterval(30, 40), BehaviorInterval(45, 55)],
        },
        valid_frame_count=20,
        total_frame_count=20,
        config=TeacherBehaviorAggregationConfig(),
    )

    assert outcome.result["writing_intervals"] == [
        {"start_seconds": 1, "end_seconds": 21}
    ]
    assert outcome.result["sitting_intervals"] == [
        {"start_seconds": 30, "end_seconds": 55}
    ]


def test_deterministic_teacher_empty_and_invalid_coverage_have_distinct_results(
    repository: CourseRepository,
) -> None:
    del repository
    config = TeacherBehaviorAggregationConfig(
        min_valid_frame_count=5,
        min_valid_frame_ratio=0.5,
    )
    empty = build_teacher_behavior_result(
        intervals={},
        valid_frame_count=20,
        total_frame_count=20,
        config=config,
    )
    invalid = build_teacher_behavior_result(
        intervals={"writing": [BehaviorInterval(10, 20)]},
        valid_frame_count=2,
        total_frame_count=20,
        config=config,
    )

    assert empty.completed is True
    assert empty.reason == "教师行为分析完成，未检测到目标行为"
    assert empty.result["writing_intervals"] == []
    assert invalid.completed is True
    assert invalid.reason == "有效教师画面不足，无法确认教师行为区间"
    assert invalid.result["writing_intervals"] == []
    assert invalid.result["sitting_intervals"] == []
    assert invalid.result["standing_intervals"] == []
    assert invalid.result["teaching_intervals"] == []


def test_missing_student_regions_reuse_database_owned_fallbacks(
    repository: CourseRepository,
) -> None:
    task_type = repository.create_task_types(
        task_id="course-student-simulation",
        writes=[TaskTypeWrite(task_type=TaskType.STUDENT_BEHAVIOR)],
    )[0]
    generated = iter([0.12, 0.30, 0.15, 0.40])
    aggregator = StudentBehaviorAggregator(
        repository,
        config=StudentAggregationConfig(
            front_fallback_min=0.10,
            front_fallback_max=0.15,
            back_fallback_min=0.25,
            back_fallback_max=0.40,
        ),
        random_uniform=lambda minimum, maximum: next(generated),
    )
    observations = [StudentFrameObservation(30, 28, 0, 0)]

    first = aggregator.aggregate(
        course_task_type_id=task_type.id,
        student_count=38,
        observations=observations,
        front_points=None,
        back_point=None,
    )
    second = aggregator.aggregate(
        course_task_type_id=task_type.id,
        student_count=38,
        observations=observations,
        front_points=None,
        back_point=None,
    )

    assert first["front_occupancy_ratio"] == second["front_occupancy_ratio"] == 0.12
    assert first["back_occupancy_ratio"] == second["back_occupancy_ratio"] == 0.30
    assert first["front_region_provided"] is False
    assert first["back_region_provided"] is False


def test_completed_asr_reuses_large_result_and_original_effective_params(
    repository: CourseRepository,
    database_engine: Engine,
    tmp_path: Path,
) -> None:
    original_params = {
        "language": "auto",
        "showSpk": True,
        "showEmotion": True,
        "showRoleIdentify": False,
        "wordTimestamps": False,
        "hotWords": [],
    }
    task_type = repository.create_task_types(
        task_id="course-asr-large",
        writes=[
            TaskTypeWrite(
                task_type=TaskType.ASR,
                request_payload={"teacher_video_path": "http://media/teacher.mp4"},
                effective_params=original_params,
            )
        ],
    )[0]
    nodes = repository.initialize_pipeline(
        "course-asr-large",
        TaskType.ASR,
        pipeline_nodes(TaskType.ASR, Priority.NORMAL),
    )
    by_code = {node.node_code: node for node in nodes}
    asr_node = by_code["ASR_TRANSCRIPTION"]
    repository.transition_node(asr_node.id, NodeStatus.QUEUED, "离线 ASR 已排队")
    repository.transition_node(asr_node.id, NodeStatus.RUNNING, "正在进行离线 ASR")
    large_segments = [
        {
            "segment_text": f"第 {index} 个转写片段",
            "bg": f"{index * 1.5:.2f}",
            "ed": f"{index * 1.5 + 1.0:.2f}",
            "role": "teacher",
            "emotion": "平淡",
        }
        for index in range(2_000)
    ]
    large_result = {
        "language": "auto",
        "segments": large_segments,
        "text": "。".join(segment["segment_text"] for segment in large_segments),
        "speed_info": [],
        "load_audio_time_ms": "100.00",
        "gpu_time_ms": "2000.00",
    }
    repository.complete_node(
        asr_node.id,
        NodeResultWrite(result=large_result, effective_params=original_params),
        reason="离线语音转写完成",
    )
    completed_task_type = repository.aggregate_task_type_state(task_type.id)
    assert completed_task_type.status is NodeStatus.COMPLETED
    assert completed_task_type.reason == "ASR 所有节点处理完成"

    app = create_control_app(
        repository=repository,
        operator_registry=UnavailableOperatorRegistry(),
        settings=PlatformSettings(
            service_name="control-service",
            course_root=tmp_path / "course",
            result_root=tmp_path / "result",
        ),
    )
    with TestClient(app) as client:
        duplicate = client.post(
            "/api/course-jobs",
            json={
                "task_id": "course-asr-large",
                "task_types": ["ASR"],
                "teacher_video_path": "http://media/changed.mp4",
                "asr_options": {"showRoleIdentify": True},
            },
        ).json()
        queried = client.get("/api/course-jobs/course-asr-large").json()

    with database_engine.connect() as connection:
        outbox_count = connection.execute(
            text(
                """
                SELECT count(*)
                FROM outbox_events
                WHERE aggregate_id = 'course-asr-large:ASR'
                """
            )
        ).scalar_one()

    assert duplicate["data"]["tasks"][0]["created"] is False
    assert duplicate["data"]["tasks"][0]["status"] == 60
    assert outbox_count == 1
    asr_task = next(
        item for item in queried["data"]["tasks"] if item["task_type"] == "ASR"
    )
    assert asr_task["effective_params"] == original_params
    queried_asr = next(
        item for item in asr_task["nodes"] if item["node_code"] == "ASR_TRANSCRIPTION"
    )
    assert len(queried_asr["result"]["segments"]) == 2_000
    assert queried_asr["result"]["segments"][-1] == large_segments[-1]
    assert queried_asr["effective_params"] == original_params


def test_failed_result_write_rolls_back_completion(repository: CourseRepository) -> None:
    task_type = repository.create_task_types(
        task_id="course-rollback",
        writes=[TaskTypeWrite(task_type=TaskType.PPT)],
    )[0]
    node = repository.create_node(
        course_task_type_id=task_type.id,
        node_code="PPT_SLICE",
        status=NodeStatus.RUNNING,
        priority=Priority.NORMAL,
        reason="正在切片",
    )

    with pytest.raises(IntegrityError):
        repository.complete_node(
            node.id,
            NodeResultWrite(artifact_path="/data/result/course-rollback/ppt", artifact_count=-1),
            reason="切片完成",
        )

    assert repository.get_node(node.id).status is NodeStatus.RUNNING


def test_list_running_ppt_slice_nodes_returns_only_reconcilable_nodes(
    repository: CourseRepository,
) -> None:
    task_type = repository.create_task_types(
        task_id="course-ppt-runtime",
        writes=[TaskTypeWrite(task_type=TaskType.PPT)],
    )[0]
    running = repository.create_node(
        course_task_type_id=task_type.id,
        node_code="PPT_SLICE",
        status=NodeStatus.RUNNING,
        priority=Priority.NORMAL,
        reason="PPT 算子后台处理中",
        required_capability="ppt_slice",
    )
    repository.create_node(
        course_task_type_id=task_type.id,
        node_code="PPT_SLICE_OLD",
        status=NodeStatus.RUNNING,
        priority=Priority.NORMAL,
        reason="其他节点",
        required_capability="ppt_slice",
    )
    pending_task_type = repository.create_task_types(
        task_id="course-ppt-pending",
        writes=[TaskTypeWrite(task_type=TaskType.PPT)],
    )[0]
    repository.create_node(
        course_task_type_id=pending_task_type.id,
        node_code="PPT_SLICE",
        status=NodeStatus.PENDING,
        priority=Priority.NORMAL,
        reason="尚未运行",
        required_capability="ppt_slice",
    )

    nodes = repository.list_running_ppt_slice_nodes()

    assert [node.id for node in nodes] == [running.id]


def test_missing_node_update_has_explicit_error(repository: CourseRepository) -> None:
    with pytest.raises(RepositoryNotFoundError, match="节点不存在"):
        repository.update_node_state(9999, NodeStatus.RUNNING, "处理中")


def test_concurrent_ready_node_claims_do_not_overlap(repository: CourseRepository) -> None:
    task_type = repository.create_task_types(
        task_id="course-claim",
        writes=[TaskTypeWrite(task_type=TaskType.PPT)],
    )[0]
    expected_ids = {
        repository.create_node(
            course_task_type_id=task_type.id,
            node_code=f"PPT_OCR_{index}",
            status=NodeStatus.PENDING,
            priority=Priority.NORMAL,
            reason="等待 OCR 处理",
            required_capability="ocr",
        ).id
        for index in range(2)
    }

    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed = list(
            executor.map(
                lambda worker: repository.claim_ready_node("ocr", worker),
                ("worker-a", "worker-b"),
            )
        )

    assert {node.id for node in claimed if node is not None} == expected_ids
    assert all(node is not None and node.status is NodeStatus.QUEUED for node in claimed)


def test_course_query_returns_only_persisted_task_types(repository: CourseRepository) -> None:
    repository.create_task_types(
        task_id="course-query",
        writes=[
            TaskTypeWrite(task_type=TaskType.PPT),
            TaskTypeWrite(task_type=TaskType.TEACHER_BEHAVIOR),
        ],
    )

    records = repository.list_task_types("course-query")

    assert [record.task_type for record in records] == [
        TaskType.PPT,
        TaskType.TEACHER_BEHAVIOR,
    ]


def test_completed_active_and_appended_task_types_are_idempotent(
    repository: CourseRepository,
) -> None:
    initial = repository.create_task_types(
        task_id="course-idempotent",
        writes=[
            TaskTypeWrite(task_type=TaskType.PPT),
            TaskTypeWrite(task_type=TaskType.ASR),
        ],
    )
    repository.update_task_type_state(initial[0].id, NodeStatus.COMPLETED, "PPT 已全部完成")
    repository.update_task_type_state(initial[1].id, NodeStatus.RUNNING, "ASR 正在处理")

    result = repository.create_task_types(
        task_id="course-idempotent",
        writes=[
            TaskTypeWrite(task_type=TaskType.PPT),
            TaskTypeWrite(task_type=TaskType.ASR),
            TaskTypeWrite(task_type=TaskType.TEACHER_BEHAVIOR),
        ],
    )

    assert [(item.task_type, item.status, item.created) for item in result] == [
        (TaskType.PPT, NodeStatus.COMPLETED, False),
        (TaskType.ASR, NodeStatus.RUNNING, False),
        (TaskType.TEACHER_BEHAVIOR, NodeStatus.PENDING, True),
    ]


def test_new_task_types_create_outbox_events_without_duplicate_events(
    repository: CourseRepository,
    database_engine: Engine,
) -> None:
    write = TaskTypeWrite(
        task_type=TaskType.PPT,
        priority=Priority.URGENT,
        request_payload={"slides_video_path": "http://media/ppt.mp4"},
    )

    repository.create_task_types(task_id="course-outbox-transaction", writes=[write])
    repository.create_task_types(task_id="course-outbox-transaction", writes=[write])

    with database_engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT aggregate_id, event_type, payload
                FROM outbox_events
                ORDER BY created_at
                """
            )
        ).mappings().all()

    assert len(rows) == 1
    assert rows[0]["aggregate_id"] == "course-outbox-transaction:PPT"
    assert rows[0]["event_type"] == "COURSE_TASK_REQUESTED"
    assert rows[0]["payload"]["task_id"] == "course-outbox-transaction"
    assert rows[0]["payload"]["task_type"] == "PPT"
    assert rows[0]["payload"]["priority"] == "URGENT"
    assert rows[0]["payload"]["submission_id"]


def test_combined_task_types_share_internal_submission_id(
    repository: CourseRepository,
    database_engine: Engine,
) -> None:
    repository.create_task_types(
        task_id="course-shared-submission",
        writes=[
            TaskTypeWrite(task_type=TaskType.ASR),
            TaskTypeWrite(task_type=TaskType.TEACHER_BEHAVIOR),
        ],
    )

    with database_engine.connect() as connection:
        payloads = connection.execute(
            text(
                """
                SELECT payload
                FROM outbox_events
                WHERE aggregate_id LIKE 'course-shared-submission:%'
                ORDER BY aggregate_id
                """
            )
        ).scalars().all()
        task_type_submission_ids = connection.execute(
            text(
                """
                SELECT submission_id::text
                FROM course_task_types
                WHERE task_id = 'course-shared-submission'
                ORDER BY task_type
                """
            )
        ).scalars().all()

    assert len(payloads) == 2
    assert payloads[0]["submission_id"] == payloads[1]["submission_id"]
    assert task_type_submission_ids == [
        payloads[0]["submission_id"],
        payloads[0]["submission_id"],
    ]


def test_later_task_type_submission_uses_a_new_persisted_submission_id(
    repository: CourseRepository,
) -> None:
    asr = repository.create_task_types(
        task_id="course-later-submission",
        writes=[TaskTypeWrite(task_type=TaskType.ASR)],
    )[0]
    teacher = repository.create_task_types(
        task_id="course-later-submission",
        writes=[TaskTypeWrite(task_type=TaskType.TEACHER_BEHAVIOR)],
    )[0]

    assert asr.submission_id
    assert teacher.submission_id
    assert teacher.submission_id != asr.submission_id


def test_pipeline_initialization_rejects_mismatched_submission_id(
    repository: CourseRepository,
) -> None:
    task_type = repository.create_task_types(
        task_id="course-submission-mismatch",
        writes=[TaskTypeWrite(task_type=TaskType.ASR)],
    )[0]

    with pytest.raises(ValueError, match="submission_id 与持久化任务事实不一致"):
        repository.initialize_pipeline(
            task_type.task_id,
            task_type.task_type,
            pipeline_nodes(task_type.task_type, task_type.priority),
            submission_id="00000000-0000-0000-0000-000000000000",
        )

    nodes = repository.initialize_pipeline(
        task_type.task_id,
        task_type.task_type,
        pipeline_nodes(task_type.task_type, task_type.priority),
        submission_id=task_type.submission_id,
    )
    assert [node.node_code for node in nodes] == ["ASR_TRANSCRIPTION"]


def test_duplicate_pipeline_initialization_creates_each_node_once(
    repository: CourseRepository,
) -> None:
    repository.create_task_types(
        task_id="course-pipeline-idempotent",
        writes=[TaskTypeWrite(task_type=TaskType.PPT)],
    )
    definitions = pipeline_nodes(TaskType.PPT, Priority.NORMAL)

    first = repository.initialize_pipeline(
        "course-pipeline-idempotent",
        TaskType.PPT,
        definitions,
    )
    second = repository.initialize_pipeline(
        "course-pipeline-idempotent",
        TaskType.PPT,
        definitions,
    )

    assert [node.node_code for node in first] == ["PPT_SLICE", "PPT_OCR"]
    assert [node.id for node in second] == [node.id for node in first]


def test_completed_prerequisite_releases_only_direct_dependent_node(
    repository: CourseRepository,
) -> None:
    repository.create_task_types(
        task_id="course-release",
        writes=[TaskTypeWrite(task_type=TaskType.PPT)],
    )
    nodes = repository.initialize_pipeline(
        "course-release",
        TaskType.PPT,
        pipeline_nodes(TaskType.PPT, Priority.NORMAL),
    )
    by_code = {node.node_code: node for node in nodes}
    repository.transition_node(by_code["PPT_SLICE"].id, NodeStatus.QUEUED, "已排队")
    repository.transition_node(by_code["PPT_SLICE"].id, NodeStatus.RUNNING, "切片中")

    repository.complete_node(
        by_code["PPT_SLICE"].id,
        NodeResultWrite(artifact_path="/data/result/course-release/ppt/slices", artifact_count=3),
        reason="切片完成",
    )

    released = {
        node.node_code: node.status
        for node in repository.list_nodes(by_code["PPT_SLICE"].course_task_type_id)
    }
    assert released == {
        "PPT_SLICE": NodeStatus.COMPLETED,
        "PPT_OCR": NodeStatus.PENDING,
    }


def test_terminal_node_cannot_transition_back_to_running(repository: CourseRepository) -> None:
    task_type = repository.create_task_types(
        task_id="course-terminal",
        writes=[TaskTypeWrite(task_type=TaskType.ASR)],
    )[0]
    node = repository.create_node(
        course_task_type_id=task_type.id,
        node_code="TERMINAL_NODE",
        status=NodeStatus.RUNNING,
        priority=Priority.NORMAL,
        reason="处理中",
    )
    repository.complete_node(node.id, NodeResultWrite(result={}), reason="处理完成")

    with pytest.raises(InvalidNodeTransition):
        repository.transition_node(node.id, NodeStatus.RUNNING, "错误倒退")


def test_urgent_claims_before_normal_fifo_without_preempting_running_node(
    repository: CourseRepository,
) -> None:
    task_type = repository.create_task_types(
        task_id="course-priority",
        writes=[TaskTypeWrite(task_type=TaskType.PPT)],
    )[0]
    running_normal = repository.create_node(
        course_task_type_id=task_type.id,
        node_code="NORMAL_RUNNING",
        status=NodeStatus.PENDING,
        priority=Priority.NORMAL,
        reason="等待处理",
        required_capability="ocr",
    )
    repository.transition_node(running_normal.id, NodeStatus.QUEUED, "已排队")
    repository.transition_node(running_normal.id, NodeStatus.RUNNING, "正在处理")
    first_normal = repository.create_node(
        course_task_type_id=task_type.id,
        node_code="NORMAL_WAITING_1",
        status=NodeStatus.PENDING,
        priority=Priority.NORMAL,
        reason="等待处理",
        required_capability="ocr",
    )
    second_normal = repository.create_node(
        course_task_type_id=task_type.id,
        node_code="NORMAL_WAITING_2",
        status=NodeStatus.PENDING,
        priority=Priority.NORMAL,
        reason="等待处理",
        required_capability="ocr",
    )
    urgent = repository.create_node(
        course_task_type_id=task_type.id,
        node_code="URGENT_WAITING",
        status=NodeStatus.PENDING,
        priority=Priority.URGENT,
        reason="等待处理",
        required_capability="ocr",
    )

    first_claim = repository.claim_ready_node("ocr", "worker-priority")
    second_claim = repository.claim_ready_node("ocr", "worker-priority")

    assert first_claim is not None and first_claim.id == urgent.id
    assert second_claim is not None and second_claim.id == first_normal.id
    assert first_claim.claimed_at is not None
    assert first_claim.started_at is None
    assert second_claim.claimed_at is not None
    assert second_claim.started_at is None
    assert second_normal.id != second_claim.id
    assert repository.get_node(running_normal.id).status is NodeStatus.RUNNING


def test_expired_outbox_claim_is_recoverable_after_publisher_restart(
    repository: CourseRepository,
    database_engine: Engine,
) -> None:
    repository.create_task_types(
        task_id="course-publisher-restart",
        writes=[TaskTypeWrite(task_type=TaskType.PPT)],
    )
    first_claim = repository.claim_outbox_events(1)[0]
    with database_engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE outbox_events
                SET claimed_at = now() - interval '10 minutes'
                WHERE event_id = :event_id
                """
            ),
            {"event_id": first_claim.event_id},
        )

    recovered = repository.claim_outbox_events(1)[0]

    assert recovered.event_id == first_claim.event_id
    assert recovered.claim_token != first_claim.claim_token


def test_dynamic_ppt_work_items_are_idempotent(repository: CourseRepository) -> None:
    repository.create_task_types(
        task_id="course-ppt-items",
        writes=[TaskTypeWrite(task_type=TaskType.PPT)],
    )
    nodes = repository.initialize_pipeline(
        "course-ppt-items",
        TaskType.PPT,
        pipeline_nodes(TaskType.PPT, Priority.NORMAL),
    )
    ocr_node = next(node for node in nodes if node.node_code == "PPT_OCR")
    items = [
        NodeWorkItemWrite(item_key=f"ppt-{index:03d}", ordinal=index)
        for index in range(30)
    ]

    first = repository.create_node_work_items(ocr_node.id, items)
    duplicate = repository.create_node_work_items(ocr_node.id, items)

    assert len(first) == 30
    assert [item.id for item in duplicate] == [item.id for item in first]


def test_work_item_result_updates_structured_progress(repository: CourseRepository) -> None:
    repository.create_task_types(
        task_id="course-ppt-progress",
        writes=[TaskTypeWrite(task_type=TaskType.PPT)],
    )
    nodes = repository.initialize_pipeline(
        "course-ppt-progress",
        TaskType.PPT,
        pipeline_nodes(TaskType.PPT, Priority.NORMAL),
    )
    ocr_node = next(node for node in nodes if node.node_code == "PPT_OCR")
    repository.create_node_work_items(
        ocr_node.id,
        [
            NodeWorkItemWrite(item_key="ppt-001", ordinal=0),
            NodeWorkItemWrite(item_key="ppt-002", ordinal=1),
        ],
    )

    progress = repository.complete_node_work_item(
        ocr_node.id,
        "ppt-001",
        {"ppt_image_id": "ppt-001", "text": "第一章"},
        reason="OCR 完成",
    )

    items = repository.list_node_work_items(ocr_node.id)
    assert progress.completed_count == 1
    assert progress.total_count == 2
    assert items[0].status is NodeStatus.COMPLETED
    assert items[0].result == {"ppt_image_id": "ppt-001", "text": "第一章"}
    assert repository.get_node(ocr_node.id).progress == {
        "completed_count": 1,
        "total_count": 2,
    }


def test_ppt_pipeline_exposes_slices_while_ocr_waits_then_preserves_image_identity(
    repository: CourseRepository,
    tmp_path: Path,
) -> None:
    repository.create_task_types(
        task_id="course-ppt-e2e",
        writes=[TaskTypeWrite(task_type=TaskType.PPT)],
    )
    nodes = repository.initialize_pipeline(
        "course-ppt-e2e",
        TaskType.PPT,
        pipeline_nodes(TaskType.PPT, Priority.NORMAL),
    )
    by_code = {node.node_code: node for node in nodes}
    slice_node = by_code["PPT_SLICE"]
    repository.transition_node(slice_node.id, NodeStatus.QUEUED, "PPT 切片已排队")
    repository.transition_node(slice_node.id, NodeStatus.RUNNING, "正在进行 PPT 切片")
    repository.complete_node(
        slice_node.id,
        NodeResultWrite(
            artifact_path="/data/result/course-ppt-e2e/ppt/slices",
            artifact_count=2,
        ),
        reason="PPT 切片完成",
    )

    class ToggleCapacity:
        available = False

        def has_available_capacity(self, capability: str) -> bool:
            del capability
            return self.available

    capacity = ToggleCapacity()
    dispatcher = NodeDispatcher(repository, capacity)
    assert dispatcher.claim_next("ocr", "ppt-worker") is None

    app = create_control_app(
        repository=repository,
        operator_registry=UnavailableOperatorRegistry(),
        settings=PlatformSettings(
            service_name="control-service",
            course_root=tmp_path / "course",
            result_root=tmp_path / "result",
        ),
    )
    with TestClient(app) as client:
        waiting_body = client.get("/api/course-jobs/course-ppt-e2e").json()
    ppt_task = next(
        item for item in waiting_body["data"]["tasks"] if item["task_type"] == "PPT"
    )
    waiting_nodes = {item["node_code"]: item for item in ppt_task["nodes"]}
    assert waiting_nodes["PPT_SLICE"]["status"] == 60
    assert waiting_nodes["PPT_SLICE"]["path"] == (
        "/data/result/course-ppt-e2e/ppt/slices"
    )
    assert waiting_nodes["PPT_SLICE"]["count"] == 2
    assert waiting_nodes["PPT_OCR"]["status"] == 30
    assert waiting_nodes["PPT_OCR"]["reason"] == "等待算子能力可用: ocr"

    capacity.available = True
    claimed_ocr = dispatcher.claim_next("ocr", "ppt-worker")
    assert claimed_ocr is not None
    repository.transition_node(claimed_ocr.id, NodeStatus.RUNNING, "正在进行 PPT OCR")

    class E2eOcrAdapter:
        async def recognize(
            self,
            instance_url: str,
            work: PptImageWork,
            *,
            enable_formula: bool = False,
        ) -> dict[str, object]:
            del instance_url, enable_formula
            return {
                "ppt_image_id": work.ppt_image_id,
                "text": f"第 {work.ordinal + 1} 页",
            }

    class E2eLeaseClient:
        async def acquire(
            self,
            capability: str,
            *,
            ttl_seconds: int | None = None,
            work_context: object | None = None,
        ) -> CapacityLease:
            del ttl_seconds
            assert work_context is not None
            assert capability == "ocr"
            return CapacityLease(
                lease_id=f"lease-{capability}-{uuid4().hex}",
                instance_id=f"{capability}-instance",
                capability=capability,
                service_url="http://ocr:8000",
                expires_at=datetime.now(UTC) + timedelta(seconds=60),
            )

        async def run_with_renewal(
            self,
            lease: CapacityLease,
            operation,
            **kwargs,
        ):
            del lease, kwargs
            return await operation

        async def release(self, lease_id: str) -> None:
            del lease_id

    work = [
        PptImageWork("ppt-001", Path("/ppt-001.jpg"), 0),
        PptImageWork("ppt-002", Path("/ppt-002.jpg"), 1),
    ]
    pipeline = PptTextPipeline(
        repository,
        E2eLeaseClient(),
        E2eOcrAdapter(),
        PptWorkLimits(batch_size=2, max_concurrency=2),
    )
    asyncio.run(
        pipeline.run_ocr(
            task_id="course-ppt-e2e",
            node_id=claimed_ocr.id,
            work=work,
        )
    )

    terminal = repository.aggregate_task_type_state(claimed_ocr.course_task_type_id)
    assert terminal.status is NodeStatus.COMPLETED

    with TestClient(app) as client:
        completed_body = client.get("/api/course-jobs/course-ppt-e2e").json()
    completed_task = next(
        item for item in completed_body["data"]["tasks"] if item["task_type"] == "PPT"
    )
    completed_nodes = {item["node_code"]: item for item in completed_task["nodes"]}
    assert completed_nodes["PPT_OCR"]["progress"] == {
        "completed_count": 2,
        "total_count": 2,
    }
    assert set(completed_nodes["PPT_OCR"]["result"]) == {"ppt-001", "ppt-002"}
    assert completed_task["status"] == 60
    assert set(completed_nodes) == {"PPT_SLICE", "PPT_OCR"}


def test_concurrent_outbox_scans_do_not_overlap(
    repository: CourseRepository,
    database_engine: Engine,
) -> None:
    with database_engine.begin() as connection:
        for index in range(2):
            connection.execute(
                text(
                    """
                    INSERT INTO outbox_events (
                        event_id, aggregate_type, aggregate_id, event_type, payload
                    )
                    VALUES (
                        :event_id, 'COURSE_TASK_TYPE', :aggregate_id,
                        'COURSE_TASK_REQUESTED', '{}'::jsonb
                    )
                    """
                ),
                {"event_id": uuid4(), "aggregate_id": f"course-outbox-{index}"},
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        batches = list(executor.map(lambda _: repository.claim_outbox_events(1), range(2)))

    event_ids = [batch[0].event_id for batch in batches]
    assert len(set(event_ids)) == 2


class AcceptanceKafkaProducer:
    def __init__(self) -> None:
        self.messages: list[bytes] = []

    async def send_and_wait(self, topic: str, value: bytes, key: bytes) -> object:
        assert topic == "algorithm.course.commands"
        assert key
        self.messages.append(value)
        return object()


def _complete_acceptance_pipeline(
    repository: CourseRepository,
    task_type: TaskType,
    course_task_type_id: int,
) -> None:
    nodes = repository.list_nodes(course_task_type_id)
    for original in nodes:
        node = repository.get_node(original.id)
        assert node.status is NodeStatus.PENDING
        repository.transition_node(node.id, NodeStatus.QUEUED, "验收节点已排队")
        repository.transition_node(node.id, NodeStatus.RUNNING, "验收节点处理中")
        if node.node_code == "PPT_SLICE":
            result = NodeResultWrite(
                artifact_path="/data/result/course-acceptance/ppt/slices",
                artifact_count=2,
            )
        elif node.node_code == "PPT_OCR":
            result = NodeResultWrite(
                result={
                    "ppt-001": {"ppt_image_id": "ppt-001", "text": "集合"},
                    "ppt-002": {"ppt_image_id": "ppt-002", "text": "函数"},
                },
                progress={"completed_count": 2, "total_count": 2},
            )
        elif node.node_code == "ASR_TRANSCRIPTION":
            result = NodeResultWrite(
                result={
                    "language": "auto",
                    "segments": [
                        {"segment_text": "今天学习函数", "bg": 0.0, "ed": 2.0}
                    ],
                    "text": "今天学习函数",
                    "speed_info": [],
                    "load_audio_time_ms": "10.00",
                    "gpu_time_ms": "20.00",
                },
                effective_params={
                    "language": "auto",
                    "showSpk": True,
                    "showEmotion": True,
                    "showRoleIdentify": False,
                    "wordTimestamps": False,
                    "hotWords": [],
                },
            )
        elif node.node_code == "TEACHER_BEHAVIOR_ANALYSIS":
            result = NodeResultWrite(
                result={
                    "writing_intervals": [],
                    "sitting_intervals": [],
                    "standing_intervals": [],
                    "teaching_intervals": [],
                    "evidence": [],
                }
            )
        else:
            result = NodeResultWrite(
                result={
                    "student_count": 38,
                    "detected_total": 35,
                    "stable_person_count": 34,
                    "attendance_rate": 34 / 38,
                    "front_occupancy_ratio": 0.12,
                    "back_occupancy_ratio": 0.30,
                    "front_region_provided": False,
                    "back_region_provided": False,
                    "evidence": [],
                }
            )
        repository.complete_node(node.id, result, reason="验收节点处理完成")
    repository.update_task_type_state(
        course_task_type_id,
        NodeStatus.COMPLETED,
        f"{task_type.value} 验收流程完成",
    )


@pytest.mark.parametrize(
    ("case_name", "request_payload", "expected_types"),
    [
        (
            "ppt-only",
            {
                "task_types": ["PPT"],
                "slides_video_path": "http://media/ppt.mp4",
            },
            {TaskType.PPT},
        ),
        (
            "asr-only",
            {
                "task_types": ["ASR"],
                "teacher_video_path": "http://media/teacher.mp4",
            },
            {TaskType.ASR},
        ),
        (
            "teacher-only",
            {
                "task_types": ["TEACHER_BEHAVIOR"],
                "teacher_video_path": "http://media/teacher.mp4",
            },
            {TaskType.TEACHER_BEHAVIOR},
        ),
        (
            "student-only",
            {
                "task_types": ["STUDENT_BEHAVIOR"],
                "student_video_path": "http://media/student.mp4",
                "student_count": 38,
            },
            {TaskType.STUDENT_BEHAVIOR},
        ),
        (
            "combined",
            {
                "task_types": [
                    "PPT",
                    "ASR",
                    "TEACHER_BEHAVIOR",
                    "STUDENT_BEHAVIOR",
                ],
                "slides_video_path": "http://media/ppt.mp4",
                "teacher_video_path": "http://media/teacher.mp4",
                "student_video_path": "http://media/student.mp4",
                "student_count": 38,
            },
            set(TaskType),
        ),
    ],
)
def test_platform_acceptance_flows_complete_from_api_through_outbox_and_query(
    repository: CourseRepository,
    tmp_path: Path,
    case_name: str,
    request_payload: dict[str, object],
    expected_types: set[TaskType],
) -> None:
    task_id = f"course-acceptance-{case_name}"
    app = create_control_app(
        repository=repository,
        operator_registry=UnavailableOperatorRegistry(),
        settings=PlatformSettings(
            course_root=tmp_path / "course",
            result_root=tmp_path / "result",
        ),
    )
    with TestClient(app) as client:
        submitted = client.post(
            "/api/course-jobs",
            json={"task_id": task_id, **request_payload},
        )
        assert submitted.status_code == 200
        assert submitted.json()["code"] == 0

        producer = AcceptanceKafkaProducer()
        published = asyncio.run(
            OutboxPublisher(
                repository,
                producer,
                topic="algorithm.course.commands",
            ).publish_once()
        )
        assert published == len(expected_types)
        initializer = PipelineInitializer(repository)
        for message in producer.messages:
            asyncio.run(initializer.handle(message))

        records = repository.list_task_types(task_id)
        assert {record.task_type for record in records} == expected_types
        for record in records:
            _complete_acceptance_pipeline(repository, record.task_type, record.id)

        queried = client.get(f"/api/course-jobs/{task_id}")

    assert queried.status_code == 200
    body = queried.json()
    assert body["code"] == 0
    tasks = {
        TaskType(task["task_type"]): task
        for task in body["data"]["tasks"]
        if task["status"] != NodeStatus.UNREQUESTED.value
    }
    assert set(tasks) == expected_types
    assert all(task["status"] == NodeStatus.COMPLETED.value for task in tasks.values())
    assert all(
        all(node["status"] == NodeStatus.COMPLETED.value for node in task["nodes"])
        for task in tasks.values()
    )
