from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from control_service.app.api.control import create_control_app
from control_service.app.infrastructure.retired_node_preflight import (
    ActiveRetiredNodesError,
    assert_no_active_retired_nodes,
    find_active_retired_nodes,
)
from fastapi.testclient import TestClient
from orchestrator_service.app.application.pipeline import pipeline_nodes
from sqlalchemy import text

from packages.platform_common.config import PlatformSettings
from packages.platform_common.operator_registry import UnavailableOperatorRegistry
from packages.platform_common.repository import (
    CourseRepository,
    NodeResultWrite,
    TaskTypeWrite,
)
from packages.platform_contracts.status import NodeStatus, Priority, TaskType

pytestmark = pytest.mark.integration

if TYPE_CHECKING:
    from conftest import Milestone1Postgres


@pytest.fixture
def retired_boundary_repository(
    milestone1_postgres: Milestone1Postgres,
) -> Iterator[CourseRepository]:
    with milestone1_postgres.engine.begin() as connection:
        connection.execute(
            text(
                """
                TRUNCATE TABLE
                    operator_instance_events,
                    operator_instances,
                    outbox_events,
                    visual_fallback_values,
                    node_work_items,
                    node_results,
                    task_node_dependencies,
                    task_nodes,
                    course_task_types,
                    course_jobs
                RESTART IDENTITY CASCADE
                """
            )
        )
    yield CourseRepository(milestone1_postgres.engine)


def _create_retired_node(
    repository: CourseRepository,
    *,
    task_id: str,
    task_type: TaskType,
    task_status: NodeStatus,
    node_code: str,
    node_status: NodeStatus,
) -> None:
    task = repository.create_task_types(
        task_id=task_id,
        writes=[TaskTypeWrite(task_type=task_type)],
    )[0]
    repository.create_node(
        course_task_type_id=task.id,
        node_code=node_code,
        status=node_status,
        priority=Priority.NORMAL,
        reason="历史退役节点",
    )
    repository.update_task_type_state(task.id, task_status, "历史任务状态")


@pytest.mark.parametrize(
    "task_status",
    [
        NodeStatus.PENDING,
        NodeStatus.WAITING_PREREQUISITE,
        NodeStatus.WAITING_OPERATOR,
        NodeStatus.QUEUED,
        NodeStatus.RUNNING,
    ],
)
def test_active_retired_node_preflight_fails_closed_for_status_10_through_50(
    retired_boundary_repository: CourseRepository,
    milestone1_postgres: Milestone1Postgres,
    task_status: NodeStatus,
) -> None:
    _create_retired_node(
        retired_boundary_repository,
        task_id=f"active-{task_status.value}",
        task_type=TaskType.PPT,
        task_status=task_status,
        node_code="PPT_KEYWORDS",
        node_status=NodeStatus.WAITING_PREREQUISITE,
    )

    active = find_active_retired_nodes(milestone1_postgres.engine)

    assert len(active) == 1
    assert active[0].task_id == f"active-{task_status.value}"
    assert active[0].task_type_status == task_status.value
    with pytest.raises(ActiveRetiredNodesError, match="PPT_KEYWORDS"):
        assert_no_active_retired_nodes(milestone1_postgres.engine)


def test_terminal_historical_retired_nodes_remain_queryable_and_do_not_block(
    retired_boundary_repository: CourseRepository,
    milestone1_postgres: Milestone1Postgres,
    tmp_path: Path,
) -> None:
    ppt = retired_boundary_repository.create_task_types(
        task_id="historical-course",
        writes=[TaskTypeWrite(task_type=TaskType.PPT)],
    )[0]
    keywords = retired_boundary_repository.create_node(
        course_task_type_id=ppt.id,
        node_code="PPT_KEYWORDS",
        status=NodeStatus.RUNNING,
        priority=Priority.NORMAL,
        reason="历史关键词处理中",
    )
    retired_boundary_repository.complete_node(
        keywords.id,
        NodeResultWrite(result={"ppt-001": {"keywords": ["历史"]}}),
        reason="历史关键词完成",
    )
    retired_boundary_repository.update_task_type_state(
        ppt.id,
        NodeStatus.COMPLETED,
        "历史 PPT 完成",
    )
    asr = retired_boundary_repository.create_task_types(
        task_id="historical-course",
        writes=[TaskTypeWrite(task_type=TaskType.ASR)],
    )[0]
    overview = retired_boundary_repository.create_node(
        course_task_type_id=asr.id,
        node_code="COURSE_OVERVIEW",
        status=NodeStatus.RUNNING,
        priority=Priority.NORMAL,
        reason="历史课程脑图处理中",
    )
    retired_boundary_repository.transition_node(
        overview.id,
        NodeStatus.FAILED,
        "历史课程脑图失败",
    )
    retired_boundary_repository.update_task_type_state(
        asr.id,
        NodeStatus.FAILED,
        "历史 ASR 任务失败",
    )

    assert find_active_retired_nodes(milestone1_postgres.engine) == ()
    assert assert_no_active_retired_nodes(milestone1_postgres.engine) == ()

    app = create_control_app(
        repository=retired_boundary_repository,
        operator_registry=UnavailableOperatorRegistry(),
        settings=PlatformSettings(
            course_root=tmp_path / "course",
            result_root=tmp_path / "result",
        ),
    )
    with TestClient(app) as client:
        response = client.get("/api/course-jobs/historical-course")

    assert response.status_code == 200
    tasks = {item["task_type"]: item for item in response.json()["data"]["tasks"]}
    ppt_node = tasks["PPT"]["nodes"][0]
    asr_node = tasks["ASR"]["nodes"][0]
    assert ppt_node["node_code"] == "PPT_KEYWORDS"
    assert ppt_node["status"] == 60
    assert ppt_node["result"] == {"ppt-001": {"keywords": ["历史"]}}
    assert asr_node["node_code"] == "COURSE_OVERVIEW"
    assert asr_node["status"] == 70
    assert asr_node["reason"] == "历史课程脑图失败"


@pytest.mark.parametrize(
    "terminal_status",
    [NodeStatus.COMPLETED, NodeStatus.FAILED, NodeStatus.CANCELLED],
)
def test_terminal_historical_nodes_never_reenter_dispatch(
    retired_boundary_repository: CourseRepository,
    terminal_status: NodeStatus,
) -> None:
    ppt = retired_boundary_repository.create_task_types(
        task_id=f"terminal-dispatch-{terminal_status.value}",
        writes=[TaskTypeWrite(task_type=TaskType.PPT)],
    )[0]
    keywords = retired_boundary_repository.create_node(
        course_task_type_id=ppt.id,
        node_code="PPT_KEYWORDS",
        status=NodeStatus.PENDING,
        priority=Priority.NORMAL,
        reason="历史关键词等待调度",
        required_capability="extract_keywords",
    )
    asr = retired_boundary_repository.create_task_types(
        task_id=f"terminal-dispatch-{terminal_status.value}",
        writes=[TaskTypeWrite(task_type=TaskType.ASR)],
    )[0]
    overview = retired_boundary_repository.create_node(
        course_task_type_id=asr.id,
        node_code="COURSE_OVERVIEW",
        status=NodeStatus.WAITING_OPERATOR,
        priority=Priority.NORMAL,
        reason="历史课程脑图等待算子",
        required_capability="course_overviews",
    )
    retired_boundary_repository.update_task_type_state(
        ppt.id,
        terminal_status,
        "历史 PPT 终态",
    )
    retired_boundary_repository.update_task_type_state(
        asr.id,
        terminal_status,
        "历史 ASR 终态",
    )

    assert retired_boundary_repository.list_dispatch_capabilities() == []
    assert (
        retired_boundary_repository.aggregate_capability_task_types(
            "extract_keywords"
        )
        == []
    )
    assert retired_boundary_repository.claim_ready_node("extract_keywords", "worker") is None
    assert retired_boundary_repository.defer_capability_nodes("extract_keywords") == 0
    assert retired_boundary_repository.resume_capability_nodes("course_overviews") == 0

    assert retired_boundary_repository.get_node(keywords.id).status is NodeStatus.PENDING
    assert (
        retired_boundary_repository.get_node(overview.id).status
        is NodeStatus.WAITING_OPERATOR
    )
    assert (
        retired_boundary_repository.aggregate_task_type_state(ppt.id).status
        is terminal_status
    )
    assert (
        retired_boundary_repository.aggregate_task_type_state(asr.id).status
        is terminal_status
    )


def test_new_ppt_and_asr_tasks_have_no_retired_nodes_or_placeholders(
    retired_boundary_repository: CourseRepository,
    tmp_path: Path,
) -> None:
    for task_type in (TaskType.PPT, TaskType.ASR):
        task = retired_boundary_repository.create_task_types(
            task_id="new-course",
            writes=[TaskTypeWrite(task_type=task_type)],
        )[0]
        retired_boundary_repository.initialize_pipeline(
            "new-course",
            task_type,
            pipeline_nodes(task_type, Priority.NORMAL),
            submission_id=task.submission_id,
        )

    app = create_control_app(
        repository=retired_boundary_repository,
        operator_registry=UnavailableOperatorRegistry(),
        settings=PlatformSettings(
            course_root=tmp_path / "course",
            result_root=tmp_path / "result",
        ),
    )
    with TestClient(app) as client:
        response = client.get("/api/course-jobs/new-course")

    assert response.status_code == 200
    tasks = {item["task_type"]: item for item in response.json()["data"]["tasks"]}
    assert [node["node_code"] for node in tasks["PPT"]["nodes"]] == [
        "PPT_SLICE",
        "PPT_OCR",
    ]
    assert [node["node_code"] for node in tasks["ASR"]["nodes"]] == [
        "ASR_TRANSCRIPTION"
    ]
    serialized = response.text
    assert "PPT_KEYWORDS" not in serialized
    assert "COURSE_OVERVIEW" not in serialized
