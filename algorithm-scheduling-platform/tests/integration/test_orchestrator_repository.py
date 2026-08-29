from __future__ import annotations

import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import TYPE_CHECKING

import pytest
from orchestrator_service.app.application.pipeline import pipeline_nodes
from orchestrator_service.app.infrastructure.ppt_slice import (
    PptSliceManifestValidator,
    PptSliceTerminalCallback,
    PptSliceTerminalHandler,
)
from sqlalchemy import Connection, Engine, text

from packages.platform_common.repository import (
    CourseRepository,
    NodeResultWrite,
    PostgresRetryPolicy,
    TaskTypeWrite,
    TransientInfrastructureError,
)
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
            "outbox_events",
            "node_results",
            "task_node_dependencies",
            "task_nodes",
            "course_task_types",
            "course_jobs",
        ):
            connection.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
    yield CourseRepository(database_engine)


def _create_asr_pipeline(
    repository: CourseRepository,
    *,
    task_id: str,
    priority: Priority = Priority.NORMAL,
) -> tuple[int, list[int]]:
    task_type = repository.create_task_types(
        task_id=task_id,
        writes=[
            TaskTypeWrite(
                task_type=TaskType.ASR,
                priority=priority,
                request_payload={"teacher_video_path": "http://media/teacher.mp4"},
                effective_params={"showSpk": True, "showEmotion": True},
            )
        ],
    )[0]
    nodes = repository.initialize_pipeline(
        task_id,
        TaskType.ASR,
        pipeline_nodes(TaskType.ASR, priority),
    )
    return task_type.id, [node.id for node in nodes]


def test_worker_reads_task_context_and_dynamic_dispatch_capabilities(
    repository: CourseRepository,
) -> None:
    task_type_id, _ = _create_asr_pipeline(repository, task_id="course-context")

    task_type = repository.get_task_type(task_type_id)

    assert task_type.request_payload == {"teacher_video_path": "http://media/teacher.mp4"}
    assert task_type.effective_params == {
        "language": "auto",
        "showSpk": True,
        "showEmotion": True,
        "showRoleIdentify": False,
        "wordTimestamps": False,
        "hotWords": [],
    }
    assert repository.list_dispatch_capabilities() == ["asr_offline"]

    repository.defer_capability_nodes("asr_offline")

    assert repository.list_dispatch_capabilities() == ["asr_offline"]


def test_task_type_status_is_derived_from_node_facts(repository: CourseRepository) -> None:
    task_type_id, node_ids = _create_asr_pipeline(
        repository,
        task_id="course-derived-state",
    )
    (asr_node_id,) = node_ids

    waiting = repository.aggregate_task_type_state(task_type_id)
    assert waiting.status is NodeStatus.PENDING
    assert waiting.reason == "等待节点处理: ASR_TRANSCRIPTION"

    repository.defer_capability_nodes("asr_offline")
    waiting_operator = repository.aggregate_task_type_state(task_type_id)
    assert waiting_operator.status is NodeStatus.WAITING_OPERATOR
    assert waiting_operator.reason == "等待算子能力可用: asr_offline"

    repository.resume_capability_nodes("asr_offline")
    claimed = repository.claim_ready_node("asr_offline", "worker-a")
    assert claimed is not None and claimed.id == asr_node_id
    running = repository.aggregate_task_type_state(task_type_id)
    assert running.status is NodeStatus.RUNNING
    assert running.reason == "正在处理节点: ASR_TRANSCRIPTION"

    repository.transition_node(asr_node_id, NodeStatus.RUNNING, "正在转写")
    repository.complete_node(
        asr_node_id,
        NodeResultWrite(result={"text": "课堂内容", "segments": []}),
        reason="语音转写完成",
    )
    completed = repository.aggregate_task_type_state(task_type_id)
    duplicate = repository.aggregate_task_type_state(task_type_id)

    assert completed.status is NodeStatus.COMPLETED
    assert completed.reason == "ASR 所有节点处理完成"
    assert repository.get_node(asr_node_id).result == {
        "text": "课堂内容",
        "segments": [],
    }
    assert duplicate.status is NodeStatus.COMPLETED
    assert duplicate.reason == completed.reason


def test_ppt_callback_and_reconcile_are_idempotent_under_real_database_race(
    repository: CourseRepository,
    tmp_path: Path,
) -> None:
    task_id = "course-ppt-terminal-race"
    repository.create_task_types(
        task_id=task_id,
        writes=[
            TaskTypeWrite(
                task_type=TaskType.PPT,
                priority=Priority.NORMAL,
                request_payload={"slides_video_path": "http://media/slides.mp4"},
                effective_params={},
            )
        ],
    )[0]
    nodes = repository.initialize_pipeline(
        task_id,
        TaskType.PPT,
        pipeline_nodes(TaskType.PPT, Priority.NORMAL),
    )
    ppt_node = next(node for node in nodes if node.node_code == "PPT_SLICE")
    claimed = repository.claim_ready_node("ppt_slice", "worker-ppt-race")
    assert claimed is not None and claimed.id == ppt_node.id
    repository.transition_node(ppt_node.id, NodeStatus.RUNNING, "PPT 切片处理中")
    operator_task_id = f"ppt-node-{ppt_node.id}"
    repository.merge_node_progress(
        ppt_node.id,
        {"task_id": task_id, "operator_task_id": operator_task_id},
        reason="PPT 异步任务已受理",
    )

    ppt_root = tmp_path / task_id / "ppt"
    slices = ppt_root / "slices"
    slices.mkdir(parents=True)
    image_path = slices / "ppt-0001.jpg"
    image_path.write_bytes(b"jpeg")
    manifest_path = ppt_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "task_id": task_id,
                "operator_task_id": operator_task_id,
                "status": 60,
                "path": str(slices),
                "manifest_path": str(manifest_path),
                "count": 1,
                "reason": "",
                "images": [{"frame_seq": 1, "snap_time": 0, "path": str(image_path)}],
                "dynamic_segments": [],
            }
        ),
        encoding="utf-8",
    )
    callback = PptSliceTerminalCallback(
        task_id=task_id,
        operator_task_id=operator_task_id,
        status=NodeStatus.COMPLETED,
        path=str(slices),
        manifest_path=str(manifest_path),
        count=1,
        dynamic_segments=[],
    )
    barrier = Barrier(2)

    class BarrierRepository:
        def get_node(self, node_id: int):
            return repository.get_node(node_id)

        def complete_node(
            self,
            node_id: int,
            result: NodeResultWrite,
            *,
            reason: str,
        ):
            barrier.wait(timeout=5)
            return repository.complete_node(node_id, result, reason=reason)

        def transition_node(
            self,
            node_id: int,
            status: NodeStatus,
            reason: str,
        ):
            return repository.transition_node(node_id, status, reason)

    handler = PptSliceTerminalHandler(
        repository=BarrierRepository(),
        validator=PptSliceManifestValidator(
            result_root=tmp_path,
            max_manifest_bytes=4096,
        ),
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: handler.handle_callback(
                    node_id=ppt_node.id,
                    callback=callback,
                ),
                range(2),
            )
        )

    assert sorted(result.duplicate for result in results) == [False, True]
    persisted = repository.get_node(ppt_node.id)
    assert persisted.status is NodeStatus.COMPLETED
    assert persisted.artifact_path == str(slices)
    assert persisted.artifact_count == 1


def test_failed_node_derives_failed_task_type(repository: CourseRepository) -> None:
    task_type_id, node_ids = _create_asr_pipeline(
        repository,
        task_id="course-failed-state",
    )
    asr_node_id = node_ids[0]
    repository.claim_ready_node("asr_offline", "worker-b")
    repository.transition_node(asr_node_id, NodeStatus.RUNNING, "正在转写")
    repository.transition_node(asr_node_id, NodeStatus.FAILED, "ASR Stub 返回业务错误")

    failed = repository.aggregate_task_type_state(task_type_id)

    assert failed.status is NodeStatus.FAILED
    assert failed.reason == "节点处理失败: ASR Stub 返回业务错误"


def test_capability_state_changes_are_aggregated_for_affected_tasks(
    repository: CourseRepository,
) -> None:
    task_type_id, _ = _create_asr_pipeline(
        repository,
        task_id="course-capability-state",
    )
    repository.defer_capability_nodes("asr_offline")

    waiting = repository.aggregate_capability_task_types("asr_offline")

    assert [record.id for record in waiting] == [task_type_id]
    assert waiting[0].status is NodeStatus.WAITING_OPERATOR

    repository.resume_capability_nodes("asr_offline")
    resumed = repository.aggregate_capability_task_types("asr_offline")

    assert resumed[0].status is NodeStatus.PENDING
    assert resumed[0].reason == "等待节点处理: ASR_TRANSCRIPTION"


def test_capability_level_wait_coordination_and_claims_do_not_deadlock(
    repository: CourseRepository,
) -> None:
    task_type_ids: set[int] = set()
    node_ids: set[int] = set()
    for index in range(100):
        task_type_id, created_node_ids = _create_asr_pipeline(
            repository,
            task_id=f"course-concurrent-asr-{index}",
            priority=Priority.URGENT if index < 10 else Priority.NORMAL,
        )
        task_type_ids.add(task_type_id)
        node_ids.update(created_node_ids)

    with ThreadPoolExecutor(max_workers=16) as executor:
        coordinated = list(
            executor.map(
                lambda _: repository.coordinate_capability_waiting("asr_offline"),
                range(16),
            )
        )

    affected = {item for batch in coordinated for item in batch}
    assert affected == task_type_ids
    assert sum(bool(batch) for batch in coordinated) == 1

    for task_type_id in sorted(affected):
        state = repository.aggregate_task_type_state(task_type_id)
        assert state.status is NodeStatus.WAITING_OPERATOR

    with ThreadPoolExecutor(max_workers=16) as executor:
        claimed = list(
            executor.map(
                lambda index: repository.claim_ready_node(
                    "asr_offline",
                    f"worker-{index % 16}",
                ),
                range(100),
            )
        )

    claimed_ids = {node.id for node in claimed if node is not None}
    assert claimed_ids == node_ids
    assert all(node is not None and node.status is NodeStatus.QUEUED for node in claimed)


@pytest.mark.parametrize("sqlstate", ("40P01", "40001"))
def test_real_postgres_retry_uses_fresh_transaction_then_recovers(
    database_engine: Engine,
    sqlstate: str,
) -> None:
    repository = CourseRepository(
        database_engine,
        postgres_retry=PostgresRetryPolicy(
            max_attempts=3,
            base_delay_seconds=0,
            max_delay_seconds=0,
            jitter_ratio=0,
        ),
    )
    attempts = 0

    def operation(connection: Connection) -> int:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            connection.execute(
                text(
                    "DO $$ BEGIN "
                    "RAISE EXCEPTION '受控事务故障注入' USING ERRCODE = '"
                    + sqlstate
                    + "'; END $$"
                )
            )
        return int(connection.execute(text("SELECT 1")).scalar_one())

    assert repository._run_retryable_transaction("real-postgres", operation) == 1
    assert attempts == 2


def test_real_postgres_retry_exhaustion_and_non_retryable_error_are_distinct(
    database_engine: Engine,
) -> None:
    repository = CourseRepository(
        database_engine,
        postgres_retry=PostgresRetryPolicy(
            max_attempts=2,
            base_delay_seconds=0,
            max_delay_seconds=0,
            jitter_ratio=0,
        ),
    )

    def force(sqlstate: str):
        def operation(connection: Connection) -> None:
            connection.execute(
                text(
                    "DO $$ BEGIN "
                    "RAISE EXCEPTION '受控事务故障注入' USING ERRCODE = '"
                    + sqlstate
                    + "'; END $$"
                )
            )

        return operation

    with pytest.raises(TransientInfrastructureError) as transient:
        repository._run_retryable_transaction(
            "real-postgres-exhausted",
            force("40P01"),
        )
    assert transient.value.attempts == 2
    assert transient.value.sqlstate == "40P01"

    with pytest.raises(Exception) as non_retryable:
        repository._run_retryable_transaction(
            "real-postgres-non-retryable",
            force("23505"),
        )
    assert not isinstance(non_retryable.value, TransientInfrastructureError)


def test_real_postgres_stale_recovery_preserves_attempt_and_excludes_ppt(
    repository: CourseRepository,
    database_engine: Engine,
) -> None:
    asr_task_type_id, _ = _create_asr_pipeline(
        repository,
        task_id="course-stale-asr",
    )
    asr = repository.claim_ready_node("asr_offline", "dead-worker")
    assert asr is not None
    repository.transition_node(asr.id, NodeStatus.RUNNING, "ASR 运行中")

    ppt_task_type = repository.create_task_types(
        task_id="course-stale-ppt",
        writes=[TaskTypeWrite(task_type=TaskType.PPT)],
    )[0]
    ppt_nodes = repository.initialize_pipeline(
        "course-stale-ppt",
        TaskType.PPT,
        pipeline_nodes(TaskType.PPT, Priority.NORMAL),
    )
    ppt_slice = next(node for node in ppt_nodes if node.node_code == "PPT_SLICE")
    claimed_ppt = repository.claim_ready_node("ppt_slice", "dead-worker")
    assert claimed_ppt is not None and claimed_ppt.id == ppt_slice.id
    repository.transition_node(ppt_slice.id, NodeStatus.RUNNING, "PPT 后台处理中")

    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE task_nodes SET claimed_at = now() - interval '10 minutes' "
                "WHERE id IN (:asr_id, :ppt_id)"
            ),
            {"asr_id": asr.id, "ppt_id": ppt_slice.id},
        )

    claimed_before = datetime.now(UTC) - timedelta(minutes=5)
    stale = repository.list_stale_claimed_nodes(claimed_before)
    assert [node.id for node in stale] == [asr.id]
    attempt_before = repository.get_node(asr.id).attempt

    assert repository.recover_stale_claimed_node(
        asr.id,
        claimed_before=claimed_before,
        reason="受控恢复",
    )
    recovered = repository.get_node(asr.id)
    assert recovered.status is NodeStatus.WAITING_OPERATOR
    assert recovered.attempt == attempt_before
    assert recovered.claimed_by is None
    assert recovered.claim_token is None
    assert repository.get_node(ppt_slice.id).status is NodeStatus.RUNNING
    assert repository.get_task_type(asr_task_type_id).task_id == "course-stale-asr"
    assert repository.get_task_type(ppt_task_type.id).task_id == "course-stale-ppt"
