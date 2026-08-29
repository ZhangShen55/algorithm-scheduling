from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from sqlalchemy import Engine, text

from packages.platform_common.repository import CourseRepository, TaskTypeWrite
from packages.platform_contracts.status import NodeStatus, TaskType

if TYPE_CHECKING:
    from conftest import Milestone1Postgres


def _clean(repository: CourseRepository, engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE outbox_events, course_jobs "
                "RESTART IDENTITY CASCADE"
            )
        )


def test_asr_defaults_and_same_parameters_reuse(
    milestone1_postgres: Milestone1Postgres,
) -> None:
    repository = CourseRepository(milestone1_postgres.engine)
    _clean(repository, milestone1_postgres.engine)
    first = repository.create_task_types(
        task_id="asr-defaults",
        writes=[TaskTypeWrite(task_type=TaskType.ASR)],
    )[0]
    second = repository.create_task_types(
        task_id="asr-defaults",
        writes=[TaskTypeWrite(task_type=TaskType.ASR)],
    )[0]

    assert first.created is True
    assert second.created is False
    assert first.run_id == second.run_id
    assert first.effective_params == {
        "language": "auto",
        "showSpk": False,
        "showEmotion": False,
        "showRoleIdentify": False,
        "wordTimestamps": False,
        "hotWords": [],
    }
    with milestone1_postgres.engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM outbox_events")
        ).scalar_one() == 1


def test_asr_parameter_change_creates_independent_run_and_nodes(
    milestone1_postgres: Milestone1Postgres,
) -> None:
    repository = CourseRepository(milestone1_postgres.engine)
    _clean(repository, milestone1_postgres.engine)
    first = repository.create_task_types(
        task_id="asr-switch",
        writes=[TaskTypeWrite(task_type=TaskType.ASR)],
    )[0]
    repository.initialize_pipeline(
        "asr-switch", TaskType.ASR, [], run_id=first.run_id
    )
    second = repository.create_task_types(
        task_id="asr-switch",
        writes=[
            TaskTypeWrite(
                task_type=TaskType.ASR,
                effective_params={"showSpk": True, "showEmotion": True},
            )
        ],
    )[0]

    assert second.created is True
    assert second.run_id != first.run_id
    assert len(repository.list_asr_runs(second.id)) == 2
    with milestone1_postgres.engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM outbox_events")
        ).scalar_one() == 2


def test_asr_same_parameters_concurrent_submissions_create_one_run(
    milestone1_postgres: Milestone1Postgres,
) -> None:
    repository = CourseRepository(milestone1_postgres.engine)
    _clean(repository, milestone1_postgres.engine)

    def submit() -> tuple[str, bool]:
        record = repository.create_task_types(
            task_id="asr-concurrent",
            writes=[
                TaskTypeWrite(
                    task_type=TaskType.ASR,
                    effective_params={"showSpk": True},
                )
            ],
        )[0]
        return str(record.run_id), record.created

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: submit(), range(8)))

    assert len({run_id for run_id, _ in results}) == 1
    assert sum(created for _, created in results) == 1
    with milestone1_postgres.engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM task_type_runs")
        ).scalar_one() == 1
        assert connection.execute(
            text("SELECT count(*) FROM outbox_events")
        ).scalar_one() == 1


def test_failed_asr_run_can_be_recreated(
    milestone1_postgres: Milestone1Postgres,
) -> None:
    repository = CourseRepository(milestone1_postgres.engine)
    _clean(repository, milestone1_postgres.engine)
    first = repository.create_task_types(
        task_id="asr-retry",
        writes=[TaskTypeWrite(task_type=TaskType.ASR)],
    )[0]
    repository.update_task_type_state(first.id, NodeStatus.FAILED, "ASR 处理失败")
    second = repository.create_task_types(
        task_id="asr-retry",
        writes=[TaskTypeWrite(task_type=TaskType.ASR)],
    )[0]

    assert second.created is True
    assert second.run_id != first.run_id
    assert len(repository.list_asr_runs(first.id)) == 2
