from __future__ import annotations

import importlib
import inspect
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

from scripts.milestone_2b_case_catalog import load_case_catalog
from scripts.milestone_2b_case_runners.base import CaseContext
from scripts.milestone_2b_case_runners.safety import ResourceSpec, _case_execution_scope

PLATFORM_ROOT = Path(__file__).parents[1]
CATALOG_PATH = PLATFORM_ROOT / "deploy/milestone-2b-case-catalog.yaml"
LOAD_CASE_IDS = tuple(f"LOAD-{number:03d}" for number in range(10, 27))
CANONICAL_CASE_IDS = frozenset(f"LOAD-{number:03d}" for number in range(10, 17))
ISOLATED_CASE_IDS = frozenset(f"LOAD-{number:03d}" for number in range(17, 27))


def _database_snapshot(*, incomplete: int = 1) -> dict[str, Any]:
    return {
        "course_jobs": 1,
        "course_task_types": 1,
        "task_nodes": 2,
        "node_results": 0,
        "outbox_events": 1,
        "task_node_statuses": {"30": incomplete, "60": 2 - incomplete},
    }


def _canonical_scenario(load: Any, case_id: str) -> dict[str, Any]:
    target = load._RUNTIME_TARGETS[case_id]
    scenario = {
        "schema_version": 1,
        "case_id": case_id,
        "run_id": "run-1",
        "target": "local",
        "mode": "canonical_runtime",
        "mutation": {"case": case_id},
        "container": target.resource_name,
        "compose_file": target.compose_file,
        "compose_project": target.compose_project,
        "service": target.service,
        "release_root": str(PLATFORM_ROOT.resolve()),
    }
    if case_id in {"LOAD-011", "LOAD-012", "LOAD-013", "LOAD-014", "LOAD-016"}:
        scenario["course_task_id"] = f"m2b-run-1-{case_id.lower()}"
        scenario["database_scope"] = f"algorithm:course-task:m2b-run-1-{case_id.lower()}"
    if case_id == "LOAD-015":
        scenario["lease_capability"] = "facerec"
        scenario["redis_scope"] = "algorithm:operator-lease:facerec"
    return scenario


def _stub_canonical_runtime(monkeypatch: pytest.MonkeyPatch, load: Any) -> None:
    monkeypatch.setattr(
        load,
        "_resolve_container",
        lambda target: ("a" * 64, {"State": {"StartedAt": "before"}}),
    )
    monkeypatch.setattr(load, "_command", lambda *args, **kwargs: "")
    monkeypatch.setattr(load, "_inspect_state", lambda container_id: {"Running": True})
    monkeypatch.setattr(
        load,
        "_wait_until",
        lambda check, *, timeout, label: check(),
    )
    monkeypatch.setattr(load, "_require_running_healthy", lambda container_id: {"Running": True})
    monkeypatch.setattr(load, "_readiness_snapshot", lambda urls: {url: {} for url in urls})
    monkeypatch.setattr(
        load,
        "_write_runtime_recovery_receipt",
        lambda case_id, scenario, resolved: None,
    )


def _load_cases() -> tuple[Any, ...]:
    selected = tuple(
        case for case in load_case_catalog(CATALOG_PATH).cases if case.case_id in LOAD_CASE_IDS
    )
    assert tuple(case.case_id for case in selected) == LOAD_CASE_IDS
    return selected


def test_load_catalog_resolves_all_17_explicit_runner_functions() -> None:
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")
    resolved: list[object] = []

    for case in _load_cases():
        method_name = case.runner.split(".", 1)[1]
        runner = vars(load).get(method_name)
        assert "__getattr__" not in vars(load)
        assert inspect.iscoroutinefunction(runner), case.case_id
        assert runner.__name__ == method_name
        assert inspect.iscoroutinefunction(runner.cleanup), case.case_id
        spec = load.CASE_SPECS[case.case_id]
        assert (spec.title, spec.expected) == (case.title, case.expected)
        assert (spec.safety, spec.timeout_seconds) == (
            case.safety,
            case.timeout_seconds,
        )
        resolved.append(runner)

    assert len({id(runner) for runner in resolved}) == len(LOAD_CASE_IDS)


def test_load_resources_are_bound_to_canonical_or_current_case_namespace(
    tmp_path: Path,
) -> None:
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")
    release_root = tmp_path / "v1.0_260818" / ("1" * 40)
    release_root.mkdir(parents=True)
    context = CaseContext(release_root.resolve(), "run-1", "local")

    for case in _load_cases():
        resources = load._load_resources(context, case)
        if case.case_id in CANONICAL_CASE_IDS:
            assert resources
            assert resources[0].kind == "container"
            assert all(not resource.name.startswith("m2b-") for resource in resources)
            if case.case_id in {"LOAD-011", "LOAD-012", "LOAD-013", "LOAD-014", "LOAD-016"}:
                assert resources[-2] == ResourceSpec(
                    "database",
                    f"algorithm:course-task:m2b-run-1-{case.case_id.lower()}",
                )
                if case.case_id == "LOAD-011":
                    assert tuple(resource.name for resource in resources[:3]) == (
                        "asr-offline-gpu0",
                        "asr-offline-gpu1",
                        "asr-offline-gpu2",
                    )
                elif case.case_id == "LOAD-016":
                    assert tuple(resource.name for resource in resources[:2]) == (
                        "postgres",
                        "orchestrator-service",
                    )
            elif case.case_id == "LOAD-015":
                assert resources[-3] == ResourceSpec(
                    "redis_prefix",
                    "algorithm:operator-lease:facerec",
                )
                assert Path(resources[-2].name).name == "lease.json"
            assert tuple(
                resource.name for resource in resources if resource.kind == "container"
            ) == tuple(
                target.resource_name
                for target in load._runtime_recovery_targets(case.case_id)
            )
            assert resources[-1].kind == "filesystem"
            receipt_path = Path(resources[-1].name)
            assert receipt_path.name == "runtime-recovery.json"
            assert receipt_path.parent.name.startswith(
                f"m2b-5-run-1-{case.case_id.lower()}-"
            )
            receipt_path.parent.rmdir()
        else:
            try:
                assert resources == (
                    ResourceSpec(
                        "filesystem",
                        str(load._load_scratch_path(context, case)),
                    ),
                )
                assert Path(resources[0].name).name.startswith(
                    f"m2b-5-run-1-{case.case_id.lower()}-"
                )
            finally:
                Path(resources[0].name).rmdir()


@pytest.mark.parametrize("case_id", sorted(CANONICAL_CASE_IDS))
def test_canonical_load_checker_fails_closed_without_runtime_recovery_facts(
    case_id: str,
) -> None:
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")
    result = load.evaluate_scenario(
        case_id,
        {
            "schema_version": 1,
            "case_id": case_id,
            "run_id": "run-1",
            "target": "local",
            "mode": "canonical_runtime",
            "mutation": {"case": case_id},
        },
    )

    assert result["status"] == "失败"
    assert "恢复事实" in result["reason"]


@pytest.mark.parametrize("case_id", sorted(ISOLATED_CASE_IDS))
def test_isolated_load_checker_rejects_missing_case_scoped_mutation(
    case_id: str,
) -> None:
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")
    result = load.evaluate_scenario(
        case_id,
        {
            "schema_version": 1,
            "case_id": case_id,
            "run_id": "run-1",
            "target": "local",
            "mode": "controlled_input",
            "mutation": {"case": case_id},
        },
    )

    assert result["status"] == "失败"
    assert "隔离" in result["reason"] or "清理" in result["reason"]


def test_load_cleanup_contract_uses_only_current_case_temporary_prefix() -> None:
    process = importlib.import_module("scripts.milestone_2b_case_runners.process")
    temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)

    for case_id in LOAD_CASE_IDS:
        resources = process.foundation_cleanup_resources("load", case_id, "run-1")
        expected = (
            ResourceSpec(
                "filesystem",
                str(temporary_root / f"m2b-5-run-1-{case_id.lower()}-"),
            ),
        )
        if case_id in {"LOAD-011", "LOAD-012", "LOAD-013", "LOAD-014", "LOAD-016"}:
            expected += (
                ResourceSpec(
                    "database",
                    f"algorithm:course-task:m2b-run-1-{case_id.lower()}",
                ),
            )
        cleanup_containers = {
            "LOAD-010": ("facerec-gpu0",),
            "LOAD-011": (
                "asr-offline-gpu0",
                "asr-offline-gpu1",
                "asr-offline-gpu2",
            ),
            "LOAD-012": ("orchestrator-service",),
            "LOAD-013": ("control-service",),
            "LOAD-014": ("kafka",),
            "LOAD-015": ("redis",),
            "LOAD-016": ("postgres", "orchestrator-service"),
        }.get(case_id, ())
        expected += tuple(
            ResourceSpec("container", name) for name in cleanup_containers
        )
        assert resources == expected

    with pytest.raises(ValueError, match="load cleanup case"):
        process.foundation_cleanup_resources("load", "LOAD-009", "run-1")


@pytest.mark.parametrize(
    ("case_id", "expected_containers"),
    (
        (
            "LOAD-011",
            ("asr-offline-gpu0", "asr-offline-gpu1", "asr-offline-gpu2"),
        ),
        ("LOAD-016", ("postgres", "orchestrator-service")),
    ),
)
def test_crash_recovery_cleanup_authorizes_only_exact_case_containers(
    case_id: str,
    expected_containers: tuple[str, ...],
) -> None:
    process = importlib.import_module("scripts.milestone_2b_case_runners.process")

    resources = process.foundation_cleanup_resources("load", case_id, "run-1")

    assert tuple(resource.name for resource in resources if resource.kind == "container") == (
        expected_containers
    )


def test_private_input_writer_retries_short_writes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")
    real_write = os.write

    def short_write(descriptor: int, payload: bytes) -> int:
        return real_write(descriptor, payload[: max(1, len(payload) // 2)])

    monkeypatch.setattr(load.os, "write", short_write)
    path = tmp_path / "input.json"

    load._write_private_input(path, {"case_id": "LOAD-010", "value": "完整"})

    assert json.loads(path.read_text()) == {"case_id": "LOAD-010", "value": "完整"}


def test_database_fact_preservation_allows_worker_progress() -> None:
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")
    before = _database_snapshot(incomplete=1)
    after = {
        **before,
        "node_results": 1,
        "task_node_statuses": {"60": 2},
    }

    load._require_database_facts_preserved(before, after)


@pytest.mark.parametrize(
    "table",
    ("course_jobs", "course_task_types", "task_nodes", "node_results", "outbox_events"),
)
def test_database_fact_preservation_rejects_row_count_regression(table: str) -> None:
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")
    before = {**_database_snapshot(incomplete=1), table: 1}
    after = {**before, table: 0}

    with pytest.raises(ValueError, match="regressed"):
        load._require_database_facts_preserved(before, after)


def test_load_012_allows_persisted_worker_progress_during_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")
    _stub_canonical_runtime(monkeypatch, load)
    snapshots = iter(
        (
            _database_snapshot(incomplete=1),
            {
                **_database_snapshot(incomplete=0),
                "node_results": 1,
            },
        )
    )
    offsets = iter(({"course:0": 3}, {"course:0": 4}))
    course_before = {
        **load._empty_course_fact_snapshot("m2b-run-1-load-012"),
        "course_jobs": 1,
        "course_task_types": 1,
        "outbox_events": 1,
        "pending_outbox_events": 1,
    }
    course_after = {
        **course_before,
        "pending_outbox_events": 0,
        "published_outbox_events": 1,
        "task_nodes": 2,
    }
    monkeypatch.setattr(load, "_database_snapshot", lambda: next(snapshots))
    monkeypatch.setattr(load, "_kafka_offsets", lambda container_id: next(offsets))
    monkeypatch.setattr(load, "_prepare_course_fact", lambda case_id, scenario: course_before)
    monkeypatch.setattr(load, "_course_fact_snapshot", lambda task_id: course_after)
    monkeypatch.setattr(load, "_cleanup_course_fact", lambda task_id: None)

    observed = load._restart_and_recover("LOAD-012", _canonical_scenario(load, "LOAD-012"))

    assert observed["after_database"]["node_results"] == 1


def test_course_fact_preparation_starts_from_empty_scope_and_uses_control_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")
    snapshots = iter(
        (
            load._empty_course_fact_snapshot("m2b-run-1-load-013"),
            {
                **load._empty_course_fact_snapshot("m2b-run-1-load-013"),
                "course_jobs": 1,
                "course_task_types": 1,
                "outbox_events": 1,
                "pending_outbox_events": 1,
            },
        )
    )
    requests: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        load,
        "_course_fact_snapshot",
        lambda task_id: next(snapshots),
    )
    monkeypatch.setattr(
        load,
        "_post_json",
        lambda url, payload: requests.append((url, payload))
        or {"code": 0, "data": {"task_id": payload["task_id"]}},
    )

    prepared = load._prepare_course_fact("LOAD-013", _canonical_scenario(load, "LOAD-013"))

    assert prepared["course_jobs"] == 1
    assert requests == [
        (
            "http://127.0.0.1:18100/api/course-jobs",
            {
                "task_id": "m2b-run-1-load-013",
                "task_types": ["ASR"],
                "teacher_video_path": "http://127.0.0.1:9/m2b-run-1-load-013.wav",
            },
        )
    ]


def test_course_fact_preparation_rejects_preexisting_scoped_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")
    existing = {
        **load._empty_course_fact_snapshot("m2b-run-1-load-013"),
        "course_jobs": 1,
    }
    monkeypatch.setattr(load, "_course_fact_snapshot", lambda task_id: existing)

    with pytest.raises(ValueError, match="already exists"):
        load._prepare_course_fact("LOAD-013", _canonical_scenario(load, "LOAD-013"))


def test_course_fact_preparation_requires_only_current_task_scope_to_be_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")
    scoped_empty = load._empty_course_fact_snapshot("m2b-run-1-load-013")
    scoped_created = {
        **scoped_empty,
        "course_jobs": 1,
        "course_task_types": 1,
        "outbox_events": 1,
        "pending_outbox_events": 1,
    }
    snapshots = iter((scoped_empty, scoped_created))
    monkeypatch.setattr(load, "_course_fact_snapshot", lambda task_id: next(snapshots))
    monkeypatch.setattr(
        load,
        "_database_snapshot",
        lambda: pytest.fail("global business rows must not define scoped emptiness"),
    )
    monkeypatch.setattr(
        load,
        "_post_json",
        lambda url, payload: {"code": 0, "data": {"task_id": payload["task_id"]}},
    )

    prepared = load._prepare_course_fact("LOAD-013", _canonical_scenario(load, "LOAD-013"))

    assert prepared["task_id"] == "m2b-run-1-load-013"


def test_course_fact_cleanup_sql_is_exactly_scoped_to_task_id() -> None:
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")

    statements = load._course_cleanup_statements("m2b-run-1-load-013")

    assert len(statements) == 3
    assert all("LIKE" not in sql.upper() for sql, _ in statements)
    assert all(params == ("m2b-run-1-load-013",) for _, params in statements)
    assert "payload ->> 'task_id' = %s" in statements[0][0]
    assert "DELETE FROM course_task_types WHERE task_id = %s" in statements[1][0]
    assert "DELETE FROM course_jobs WHERE task_id = %s" in statements[2][0]


def test_scoped_recovery_requires_outbox_dag_and_offset_progress() -> None:
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")
    before = {
        **load._empty_course_fact_snapshot("m2b-run-1-load-014"),
        "course_jobs": 1,
        "course_task_types": 1,
        "outbox_events": 1,
        "pending_outbox_events": 1,
    }
    after = {
        **before,
        "pending_outbox_events": 0,
        "published_outbox_events": 1,
        "task_nodes": 2,
        "task_node_statuses": {"10": 1, "20": 1},
    }

    load._require_scoped_recovery_progress(
        "LOAD-014",
        before,
        after,
        before_offsets={},
        after_offsets={"course-task-commands:0": 1},
    )

    with pytest.raises(ValueError, match="offset"):
        load._require_scoped_recovery_progress(
            "LOAD-014",
            before,
            after,
            before_offsets={},
            after_offsets={},
        )

    with pytest.raises(ValueError, match="DAG"):
        load._require_scoped_recovery_progress(
            "LOAD-014",
            before,
            {**after, "task_nodes": 0},
            before_offsets={},
            after_offsets={"course-task-commands:0": 1},
        )


def test_load_012_rejects_unrelated_offset_progress_without_scoped_publication_and_dag() -> None:
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")
    before = {
        **load._empty_course_fact_snapshot("m2b-run-1-load-012"),
        "course_jobs": 1,
        "course_task_types": 1,
        "outbox_events": 1,
        "pending_outbox_events": 1,
    }
    after = {**before, "node_attempts": 1}

    with pytest.raises(ValueError, match="Outbox|DAG"):
        load._require_scoped_recovery_progress(
            "LOAD-012",
            before,
            after,
            before_offsets={"unrelated:0": 4},
            after_offsets={"unrelated:0": 5},
        )


def test_load_011_requires_nonempty_unfinished_asr_dag_before_kill() -> None:
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")

    with pytest.raises(ValueError, match="DAG"):
        load._require_unfinished_operator_work(
            "LOAD-011",
            load._empty_course_fact_snapshot("m2b-run-1-load-011"),
        )


def test_load_016_rejects_unrelated_progress_without_scoped_publication_and_dag() -> None:
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")
    before = {
        **load._empty_course_fact_snapshot("m2b-run-1-load-016"),
        "course_jobs": 1,
        "course_task_types": 1,
        "outbox_events": 1,
        "pending_outbox_events": 1,
    }

    with pytest.raises(ValueError, match="Outbox|DAG"):
        load._require_scoped_recovery_progress(
            "LOAD-016",
            before,
            {**before, "node_attempts": 1},
            before_offsets=None,
            after_offsets=None,
        )


@pytest.mark.parametrize(
    "case_id", ("LOAD-010", "LOAD-012", "LOAD-013", "LOAD-014", "LOAD-015")
)
def test_canonical_runtime_receipt_is_written_before_any_docker_mutation(
    case_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")
    scenario = _canonical_scenario(load, case_id)
    mutations: list[tuple[str, ...]] = []

    class ReceiptWritten(RuntimeError):
        pass

    monkeypatch.setattr(
        load,
        "_resolve_container",
        lambda target: ("a" * 64, {"State": {"StartedAt": "before"}}),
    )
    monkeypatch.setattr(
        load,
        "_write_runtime_recovery_receipt",
        lambda case_id, scenario, resolved: (_ for _ in ()).throw(ReceiptWritten()),
    )

    def command(argv: tuple[str, ...], **kwargs: Any) -> str:
        mutations.append(argv)
        raise AssertionError("Docker mutation ran before the runtime receipt")

    monkeypatch.setattr(load, "_command", command)
    monkeypatch.setattr(load, "_database_snapshot", lambda: _database_snapshot())
    monkeypatch.setattr(load, "_operator_instances", lambda: [])
    monkeypatch.setattr(load, "_kafka_offsets", lambda container_id: {})
    monkeypatch.setattr(load, "_require_instance", lambda instance_id, lifecycle: {})
    monkeypatch.setattr(load, "_inspect_state", lambda container_id: {"Running": True})
    monkeypatch.setattr(load, "_wait_until", lambda check, *, timeout, label: None)
    monkeypatch.setattr(load, "_cleanup_course_fact", lambda task_id: None)

    with pytest.raises(ReceiptWritten):
        load._restart_and_recover(case_id, scenario)

    assert mutations == []


def test_load_011_outages_all_asr_instances_before_submitting_pending_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")
    scenario = _canonical_scenario(load, "LOAD-011")
    scenario["containers"] = [
        "asr-offline-gpu0",
        "asr-offline-gpu1",
        "asr-offline-gpu2",
    ]
    scenario["services"] = list(scenario["containers"])
    ids = {
        service: f"{index}" * 64
        for index, service in enumerate(scenario["containers"], 1)
    }
    running = {container_id: True for container_id in ids.values()}
    events: list[str] = []
    pending = {
        **load._empty_course_fact_snapshot("m2b-run-1-load-011"),
        "course_jobs": 1,
        "course_task_types": 1,
        "outbox_events": 1,
        "pending_outbox_events": 1,
        "task_nodes": 2,
        "unfinished_operator_nodes": 1,
        "task_node_statuses": {"30": 1, "10": 1},
    }

    monkeypatch.setattr(
        load,
        "_resolve_container",
        lambda target: (ids[target.service], {"State": {"StartedAt": target.service}}),
    )

    def command(argv: tuple[str, ...], **kwargs: Any) -> str:
        operation, container_id = argv[1], argv[-1]
        events.append(f"{operation}:{container_id}")
        if operation == "kill":
            running[container_id] = False
        elif operation == "start":
            running[container_id] = True
        return ""

    def prepare(case_id: str, actual: dict[str, Any]) -> dict[str, Any]:
        assert not any(running.values())
        events.append("prepare")
        return pending

    monkeypatch.setattr(load, "_command", command)
    monkeypatch.setattr(load, "_prepare_course_fact", prepare)
    monkeypatch.setattr(load, "_course_fact_snapshot", lambda task_id: pending)
    monkeypatch.setattr(load, "_database_snapshot", lambda: _database_snapshot())
    monkeypatch.setattr(load, "_operator_instances", lambda: [])
    monkeypatch.setattr(load, "_require_instance", lambda instance_id, lifecycle: {})
    monkeypatch.setattr(load, "_require_online_ids", lambda expected_ids: [])
    monkeypatch.setattr(
        load, "_inspect_state", lambda container_id: {"Running": running[container_id]}
    )
    monkeypatch.setattr(
        load,
        "_require_running_healthy",
        lambda container_id: {"Running": running[container_id]},
    )
    monkeypatch.setattr(load, "_readiness_snapshot", lambda urls: {})
    monkeypatch.setattr(load, "_wait_until", lambda check, *, timeout, label: check())
    monkeypatch.setattr(load, "_cleanup_course_fact", lambda task_id: None)
    monkeypatch.setattr(
        load,
        "_write_runtime_recovery_receipt",
        lambda case_id, scenario, resolved: events.append("receipt"),
        raising=False,
    )

    observed = load._restart_and_recover("LOAD-011", scenario)

    assert events[:5] == [
        "receipt",
        f"kill:{ids['asr-offline-gpu0']}",
        f"kill:{ids['asr-offline-gpu1']}",
        f"kill:{ids['asr-offline-gpu2']}",
        "prepare",
    ]
    assert set(observed["container_ids"]) == set(ids.values())
    assert all(running.values())


def test_load_016_establishes_pending_work_before_postgres_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")
    scenario = _canonical_scenario(load, "LOAD-016")
    scenario["support_container"] = "orchestrator-service"
    scenario["support_service"] = "orchestrator-service"
    ids = {"postgres": "a" * 64, "orchestrator-service": "b" * 64}
    running = {container_id: True for container_id in ids.values()}
    events: list[str] = []
    pending = {
        **load._empty_course_fact_snapshot("m2b-run-1-load-016"),
        "course_jobs": 1,
        "course_task_types": 1,
        "outbox_events": 1,
        "pending_outbox_events": 1,
    }
    progressed = {
        **pending,
        "pending_outbox_events": 0,
        "published_outbox_events": 1,
        "task_nodes": 2,
        "unfinished_operator_nodes": 1,
        "task_node_statuses": {"30": 1, "10": 1},
    }

    monkeypatch.setattr(
        load,
        "_resolve_container",
        lambda target: (ids[target.service], {"State": {"StartedAt": target.service}}),
    )

    def command(argv: tuple[str, ...], **kwargs: Any) -> str:
        operation, container_id = argv[1], argv[-1]
        events.append(f"{operation}:{container_id}")
        if operation == "stop":
            running[container_id] = False
        elif operation in {"start", "restart"}:
            running[container_id] = True
        return ""

    def prepare(case_id: str, actual: dict[str, Any]) -> dict[str, Any]:
        assert not running[ids["orchestrator-service"]]
        assert running[ids["postgres"]]
        events.append("prepare")
        return pending

    monkeypatch.setattr(load, "_command", command)
    monkeypatch.setattr(load, "_prepare_course_fact", prepare)
    monkeypatch.setattr(load, "_course_fact_snapshot", lambda task_id: progressed)
    monkeypatch.setattr(load, "_database_snapshot", lambda: _database_snapshot())
    monkeypatch.setattr(
        load, "_inspect_state", lambda container_id: {"Running": running[container_id]}
    )
    monkeypatch.setattr(
        load,
        "_require_running_healthy",
        lambda container_id: {"Running": running[container_id]},
    )
    monkeypatch.setattr(load, "_readiness_snapshot", lambda urls: {})
    monkeypatch.setattr(load, "_wait_until", lambda check, *, timeout, label: check())
    monkeypatch.setattr(load, "_cleanup_course_fact", lambda task_id: None)
    monkeypatch.setattr(
        load,
        "_write_runtime_recovery_receipt",
        lambda case_id, scenario, resolved: events.append("receipt"),
        raising=False,
    )

    observed = load._restart_and_recover("LOAD-016", scenario)

    assert events[:5] == [
        "receipt",
        f"stop:{ids['orchestrator-service']}",
        "prepare",
        f"restart:{ids['postgres']}",
        f"start:{ids['orchestrator-service']}",
    ]
    assert observed["before_course_fact"]["pending_outbox_events"] == 1
    assert observed["after_course_fact"]["published_outbox_events"] == 1
    assert all(running.values())
    with pytest.raises(ValueError, match="unfinished"):
        load._require_unfinished_operator_work(
            "LOAD-011",
            {
                **load._empty_course_fact_snapshot("m2b-run-1-load-011"),
                "task_nodes": 2,
                "unfinished_operator_nodes": 0,
            },
        )


@pytest.mark.asyncio
async def test_outer_cleanup_restores_all_exact_load_011_receipted_containers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cleanup = importlib.import_module("scripts.milestone_2b_case_runners.cleanup")
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")
    process = importlib.import_module("scripts.milestone_2b_case_runners.process")
    temporary_root = tmp_path.resolve()
    receipt_directory = temporary_root / "m2b-5-run-1-load-011-crashed-checker"
    receipt_directory.mkdir(mode=0o700)
    receipt = receipt_directory / "runtime-recovery.json"
    container_ids = tuple(str(index) * 64 for index in range(1, 4))
    receipt.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "case_id": "LOAD-011",
                "run_id": "run-1",
                "containers": [
                    {
                        "resource_name": f"asr-offline-gpu{index}",
                        "container_id": container_ids[index],
                    }
                    for index in range(3)
                ],
            }
        ),
        encoding="utf-8",
    )
    receipt.chmod(0o600)
    restored: list[tuple[str, str]] = []
    monkeypatch.setattr(cleanup.tempfile, "gettempdir", lambda: str(temporary_root))
    monkeypatch.setattr(load.tempfile, "gettempdir", lambda: str(temporary_root))
    monkeypatch.setattr(process.tempfile, "gettempdir", lambda: str(temporary_root))
    monkeypatch.setattr(load, "_cleanup_course_fact", lambda task_id: None)
    monkeypatch.setattr(
        load,
        "_restore_receipted_container",
        lambda target, container_id, state: restored.append(
            (target.resource_name, container_id)
        ),
        raising=False,
    )
    monkeypatch.setattr(
        load,
        "_validate_receipted_container",
        lambda target, container_id: {"Running": False},
    )
    monkeypatch.setattr(
        load,
        "_require_running_healthy",
        lambda container_id: {"Running": True},
    )
    monkeypatch.setattr(load, "_require_online_ids", lambda expected_ids: [])
    monkeypatch.setattr(load, "_wait_until", lambda check, *, timeout, label: check())

    result = await cleanup.cleanup_foundation_resources("load", "LOAD-011", "run-1")

    assert result["status"] == "clean"
    assert restored == [
        (f"asr-offline-gpu{index}", container_ids[index]) for index in range(3)
    ]
    assert not receipt_directory.exists()


def test_multi_container_recovery_starts_all_targets_before_readiness_waits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")
    temporary_root = tmp_path.resolve()
    directory = temporary_root / "m2b-5-run-1-load-011-crashed-checker"
    directory.mkdir(mode=0o700)
    receipt = directory / "runtime-recovery.json"
    container_ids = tuple(str(index) * 64 for index in range(1, 4))
    receipt.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "case_id": "LOAD-011",
                "run_id": "run-1",
                "containers": [
                    {
                        "resource_name": f"asr-offline-gpu{index}",
                        "container_id": container_ids[index],
                    }
                    for index in range(3)
                ],
            }
        ),
        encoding="utf-8",
    )
    receipt.chmod(0o600)
    services = {
        container_id: f"asr-offline-gpu{index}"
        for index, container_id in enumerate(container_ids)
    }
    events: list[str] = []

    def command(argv: tuple[str, ...], **kwargs: Any) -> str:
        operation, container_id = argv[1], argv[-1]
        if operation == "inspect":
            return json.dumps(
                [
                    {
                        "Id": container_id,
                        "Config": {
                            "Labels": {
                                "com.docker.compose.project": "algorithm-operators",
                                "com.docker.compose.service": services[container_id],
                            }
                        },
                        "State": {"Running": False},
                    }
                ]
            )
        if operation == "start":
            events.append(f"start:{container_id}")
            return ""
        raise AssertionError(argv)

    monkeypatch.setattr(load.tempfile, "gettempdir", lambda: str(temporary_root))
    monkeypatch.setattr(load, "_command", command)
    monkeypatch.setattr(
        load,
        "_wait_until",
        lambda check, *, timeout, label: events.append(f"wait:{label}"),
    )

    load._cleanup_runtime_recovery_receipts("LOAD-011", "run-1")

    assert events[:3] == [f"start:{container_id}" for container_id in container_ids]


@pytest.mark.asyncio
async def test_load_cleanup_uses_multi_target_recovery_budget_above_30_seconds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")
    case = next(case for case in _load_cases() if case.case_id == "LOAD-011")
    release_root = tmp_path / "v1.0_260818" / ("1" * 40)
    release_root.mkdir(parents=True)
    observed_timeouts: list[float] = []

    async def run_command(**kwargs: Any) -> Any:
        observed_timeouts.append(kwargs["timeout_seconds"])
        return load.CommandResult(
            argv=kwargs["command"].argv,
            returncode=0,
            stdout=json.dumps(
                {
                    "status": "clean",
                    "errors": [],
                    "residual_temp_directories": [],
                }
            ).encode(),
            stderr=b"",
            stdout_truncated=False,
            stderr_truncated=False,
        )

    monkeypatch.setattr(load, "run_command", run_command)

    await load.cleanup(CaseContext(release_root, "run-1", "local"), case)

    assert observed_timeouts == [load._runtime_recovery_cleanup_timeout_seconds("LOAD-011")]
    assert observed_timeouts[0] > 30


@pytest.mark.asyncio
async def test_outer_cleanup_preserves_runtime_receipt_when_exact_restore_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cleanup = importlib.import_module("scripts.milestone_2b_case_runners.cleanup")
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")
    process = importlib.import_module("scripts.milestone_2b_case_runners.process")
    temporary_root = tmp_path.resolve()
    receipt_directory = temporary_root / "m2b-5-run-1-load-016-crashed-checker"
    receipt_directory.mkdir(mode=0o700)
    receipt = receipt_directory / "runtime-recovery.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "case_id": "LOAD-016",
                "run_id": "run-1",
                "containers": [
                    {"resource_name": "postgres", "container_id": "a" * 64},
                    {
                        "resource_name": "orchestrator-service",
                        "container_id": "b" * 64,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    receipt.chmod(0o600)
    monkeypatch.setattr(cleanup.tempfile, "gettempdir", lambda: str(temporary_root))
    monkeypatch.setattr(load.tempfile, "gettempdir", lambda: str(temporary_root))
    monkeypatch.setattr(process.tempfile, "gettempdir", lambda: str(temporary_root))
    monkeypatch.setattr(load, "_cleanup_course_fact", lambda task_id: None)
    monkeypatch.setattr(
        load,
        "_restore_receipted_container",
        lambda target, container_id, state: (_ for _ in ()).throw(
            ValueError("restore failed")
        ),
        raising=False,
    )
    monkeypatch.setattr(
        load,
        "_validate_receipted_container",
        lambda target, container_id: {"Running": False},
    )

    result = await cleanup.cleanup_foundation_resources("load", "LOAD-016", "run-1")

    assert result["status"] == "failed"
    assert receipt.exists()
    assert any("restore failed" in error for error in result["errors"])


def test_outer_cleanup_rejects_conflicting_receipts_before_restore(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")
    temporary_root = tmp_path.resolve()
    prefix = "m2b-5-run-1-load-016-"
    for suffix, postgres_id in (("first", "a" * 64), ("second", "c" * 64)):
        directory = temporary_root / f"{prefix}{suffix}"
        directory.mkdir(mode=0o700)
        receipt = directory / "runtime-recovery.json"
        receipt.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "case_id": "LOAD-016",
                    "run_id": "run-1",
                    "containers": [
                        {"resource_name": "postgres", "container_id": postgres_id},
                        {
                            "resource_name": "orchestrator-service",
                            "container_id": "b" * 64,
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        receipt.chmod(0o600)
    restored: list[tuple[str, str]] = []
    monkeypatch.setattr(load.tempfile, "gettempdir", lambda: str(temporary_root))
    monkeypatch.setattr(
        load,
        "_restore_receipted_container",
        lambda target, container_id: restored.append((target.resource_name, container_id)),
        raising=False,
    )

    with pytest.raises(ValueError, match="conflicting"):
        load._cleanup_runtime_recovery_receipts("LOAD-016", "run-1")

    assert restored == []


def test_outer_cleanup_validates_all_container_labels_before_any_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")
    temporary_root = tmp_path.resolve()
    directory = temporary_root / "m2b-5-run-1-load-016-crashed-checker"
    directory.mkdir(mode=0o700)
    receipt = directory / "runtime-recovery.json"
    container_ids = ("a" * 64, "b" * 64)
    receipt.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "case_id": "LOAD-016",
                "run_id": "run-1",
                "containers": [
                    {"resource_name": "postgres", "container_id": container_ids[0]},
                    {
                        "resource_name": "orchestrator-service",
                        "container_id": container_ids[1],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    receipt.chmod(0o600)
    starts: list[str] = []

    def command(argv: tuple[str, ...], **kwargs: Any) -> str:
        operation, container_id = argv[1], argv[-1]
        if operation == "inspect":
            service = (
                "postgres" if container_id == container_ids[0] else "wrong-service"
            )
            return json.dumps(
                [
                    {
                        "Id": container_id,
                        "Config": {
                            "Labels": {
                                "com.docker.compose.project": (
                                    "algorithm-scheduling-platform"
                                ),
                                "com.docker.compose.service": service,
                            }
                        },
                        "State": {"Running": False},
                    }
                ]
            )
        if operation == "start":
            starts.append(container_id)
            return ""
        raise AssertionError(argv)

    monkeypatch.setattr(load.tempfile, "gettempdir", lambda: str(temporary_root))
    monkeypatch.setattr(load, "_command", command)
    monkeypatch.setattr(load, "_wait_until", lambda check, *, timeout, label: None)

    with pytest.raises(ValueError, match="identity mismatch"):
        load._cleanup_runtime_recovery_receipts("LOAD-016", "run-1")

    assert starts == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case_id", "resource_name"),
    (
        ("LOAD-010", "facerec-gpu0"),
        ("LOAD-012", "orchestrator-service"),
        ("LOAD-013", "control-service"),
        ("LOAD-014", "kafka"),
        ("LOAD-015", "redis"),
    ),
)
async def test_outer_cleanup_restores_every_single_target_mutation_case(
    case_id: str,
    resource_name: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cleanup = importlib.import_module("scripts.milestone_2b_case_runners.cleanup")
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")
    process = importlib.import_module("scripts.milestone_2b_case_runners.process")
    temporary_root = tmp_path.resolve()
    directory = temporary_root / f"m2b-5-run-1-{case_id.lower()}-crashed-checker"
    directory.mkdir(mode=0o700)
    receipt = directory / "runtime-recovery.json"
    container_id = "a" * 64
    receipt.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "case_id": case_id,
                "run_id": "run-1",
                "containers": [
                    {
                        "resource_name": resource_name,
                        "container_id": container_id,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    receipt.chmod(0o600)
    restored: list[tuple[str, str]] = []
    monkeypatch.setattr(cleanup.tempfile, "gettempdir", lambda: str(temporary_root))
    monkeypatch.setattr(load.tempfile, "gettempdir", lambda: str(temporary_root))
    monkeypatch.setattr(process.tempfile, "gettempdir", lambda: str(temporary_root))
    monkeypatch.setattr(load, "_cleanup_course_fact", lambda task_id: None)
    monkeypatch.setattr(
        load,
        "_validate_receipted_container",
        lambda target, exact_id: {"Running": False},
    )
    monkeypatch.setattr(
        load,
        "_restore_receipted_container",
        lambda target, exact_id, state: restored.append(
            (target.resource_name, exact_id)
        ),
    )
    monkeypatch.setattr(load, "_wait_until", lambda check, *, timeout, label: None)

    result = await cleanup.cleanup_foundation_resources("load", case_id, "run-1")

    assert result["status"] == "clean"
    assert restored == [(resource_name, container_id)]
    assert not directory.exists()


def test_load_015_requires_real_lease_before_restart_and_zero_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")
    _stub_canonical_runtime(monkeypatch, load)
    instances = [{"instance_id": "facerec-gpu0", "lifecycle": "ONLINE"}]
    capacity = iter(
        (
            [{"instance_id": "facerec-gpu0", "active_lease_count": 0}],
            [{"instance_id": "facerec-gpu0", "active_lease_count": 1}],
            [{"instance_id": "facerec-gpu0", "active_lease_count": 0}],
        )
    )
    monkeypatch.setattr(load, "_operator_instances", lambda: instances)
    monkeypatch.setattr(load, "_require_online_ids", lambda expected_ids: instances)
    monkeypatch.setattr(load, "_http_json", lambda url: next(capacity))
    monkeypatch.setattr(load, "_write_lease_receipt", lambda scenario, lease: None)
    monkeypatch.setattr(
        load,
        "_acquire_case_lease",
        lambda case_id, scenario: {
            "lease_id": "lease-1",
            "instance_id": "facerec-gpu0",
            "capability": "facerec",
        },
    )
    monkeypatch.setattr(load, "_release_case_lease", lambda lease_id: 404)

    observed = load._restart_and_recover("LOAD-015", _canonical_scenario(load, "LOAD-015"))

    assert observed["before_active_lease_count"] == 1


def test_load_015_rejects_nonzero_initial_active_lease_before_acquire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")
    _stub_canonical_runtime(monkeypatch, load)
    acquired: list[str] = []
    monkeypatch.setattr(load, "_operator_instances", lambda: [])
    monkeypatch.setattr(load, "_http_json", lambda url: [{"active_lease_count": 1}])
    monkeypatch.setattr(
        load,
        "_acquire_case_lease",
        lambda case_id, scenario: acquired.append(case_id) or {},
    )

    with pytest.raises(ValueError, match="initial active lease"):
        load._restart_and_recover("LOAD-015", _canonical_scenario(load, "LOAD-015"))

    assert acquired == []


def test_load_015_releases_exact_lease_when_first_post_acquire_snapshot_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")
    _stub_canonical_runtime(monkeypatch, load)
    capacity_calls = 0
    released: list[str] = []

    def capacity(url: str) -> list[dict[str, int]]:
        nonlocal capacity_calls
        capacity_calls += 1
        if capacity_calls == 1:
            return [{"active_lease_count": 0}]
        raise ValueError("snapshot failed")

    scenario = _canonical_scenario(load, "LOAD-015")
    scenario["lease_receipt_path"] = str(tmp_path / "lease.json")
    monkeypatch.setattr(load, "_operator_instances", lambda: [])
    monkeypatch.setattr(load, "_http_json", capacity)
    monkeypatch.setattr(load, "_write_lease_receipt", lambda scenario, lease: None)
    monkeypatch.setattr(
        load,
        "_acquire_case_lease",
        lambda case_id, scenario: {
            "lease_id": "lease-exact",
            "instance_id": "facerec-gpu0",
            "capability": "facerec",
        },
    )
    monkeypatch.setattr(
        load,
        "_release_case_lease",
        lambda lease_id: released.append(lease_id) or 200,
    )

    with pytest.raises(ValueError, match="snapshot failed"):
        load._restart_and_recover("LOAD-015", scenario)

    assert released == ["lease-exact"]


@pytest.mark.asyncio
async def test_load_015_outer_cleanup_releases_only_receipted_exact_lease(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cleanup = importlib.import_module("scripts.milestone_2b_case_runners.cleanup")
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")
    temporary_root = tmp_path.resolve()
    receipt_directory = temporary_root / "m2b-5-run-1-load-015-lease-crash"
    receipt_directory.mkdir(mode=0o700)
    receipt = receipt_directory / "lease.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "case_id": "LOAD-015",
                "run_id": "run-1",
                "lease_id": "lease-exact",
                "instance_id": "facerec-gpu0",
                "capability": "facerec",
            }
        ),
        encoding="utf-8",
    )
    receipt.chmod(0o600)
    released: list[str] = []
    monkeypatch.setattr(cleanup.tempfile, "gettempdir", lambda: str(temporary_root))
    monkeypatch.setattr(
        load,
        "_release_case_lease",
        lambda lease_id: released.append(lease_id) or 200,
    )

    result = await cleanup.cleanup_foundation_resources("load", "LOAD-015", "run-1")

    assert result["status"] == "clean"
    assert released == ["lease-exact"]


@pytest.mark.asyncio
async def test_load_015_outer_cleanup_preserves_lease_receipt_when_release_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cleanup = importlib.import_module("scripts.milestone_2b_case_runners.cleanup")
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")
    process = importlib.import_module("scripts.milestone_2b_case_runners.process")
    temporary_root = tmp_path.resolve()
    directory = temporary_root / "m2b-5-run-1-load-015-lease-crash"
    directory.mkdir(mode=0o700)
    receipt = directory / "lease.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "case_id": "LOAD-015",
                "run_id": "run-1",
                "lease_id": "lease-exact",
                "instance_id": "facerec-gpu0",
                "capability": "facerec",
            }
        ),
        encoding="utf-8",
    )
    receipt.chmod(0o600)
    monkeypatch.setattr(cleanup.tempfile, "gettempdir", lambda: str(temporary_root))
    monkeypatch.setattr(load.tempfile, "gettempdir", lambda: str(temporary_root))
    monkeypatch.setattr(process.tempfile, "gettempdir", lambda: str(temporary_root))
    monkeypatch.setattr(
        load,
        "_release_case_lease",
        lambda lease_id: (_ for _ in ()).throw(ValueError("release failed")),
    )

    result = await cleanup.cleanup_foundation_resources("load", "LOAD-015", "run-1")

    assert result["status"] == "failed"
    assert receipt.exists()
    assert directory.name in result["residual_temp_directories"]


@pytest.mark.asyncio
async def test_load_015_outer_cleanup_accepts_case_scope_without_lease_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cleanup = importlib.import_module("scripts.milestone_2b_case_runners.cleanup")
    temporary_root = tmp_path.resolve()
    receipt_directory = temporary_root / "m2b-5-run-1-load-015-lease-not-acquired"
    receipt_directory.mkdir(mode=0o700)
    monkeypatch.setattr(cleanup.tempfile, "gettempdir", lambda: str(temporary_root))

    result = await cleanup.cleanup_foundation_resources("load", "LOAD-015", "run-1")

    assert result["status"] == "clean"


def test_load_015_acquires_a_real_lease_when_capacity_starts_at_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")
    requests: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        load,
        "_post_json",
        lambda url, payload: requests.append((url, payload))
        or {
            "lease_id": "lease-1",
            "instance_id": "facerec-gpu0",
            "capability": "facerec",
        },
    )

    lease = load._acquire_case_lease("LOAD-015", _canonical_scenario(load, "LOAD-015"))

    assert lease["lease_id"] == "lease-1"
    assert requests == [
        (
            "http://127.0.0.1:18100/internal/operator-instances/lease",
            {"capability": "facerec", "ttl_seconds": 3600},
        )
    ]


def test_deployment_harness_runs_case_batch_before_cleanup_and_aggregation() -> None:
    document = (PLATFORM_ROOT / "harness/scenarios/milestone-2b-deploy.md").read_text()
    batch = document.index("scripts/run_milestone_2b_case_batch.py")
    cleanup = document.index('docker stop "$container_id"', document.index("## 阶段 6"))
    aggregate = document.index("scripts/aggregate_milestone_2b_cases.py")

    assert batch < cleanup < aggregate
    batch_block = document[batch : document.index("```", batch)]
    assert "--phase deployment" in batch_block
    assert '--delegated-lock-holder-pid "$OPERATOR_LIFECYCLE_LOCK_PID"' in batch_block
    assert '--delegated-lock-path "$OPERATOR_LIFECYCLE_LOCK_PATH"' in batch_block
    assert "--require-cleanup" in batch_block
    assert "--require-all-selected" in batch_block


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    tuple(case for case in _load_cases() if case.case_id in ISOLATED_CASE_IDS),
    ids=lambda case: case.case_id,
)
async def test_each_isolated_load_runner_rejects_its_bad_evidence_and_cleans(
    case: Any,
    tmp_path: Path,
) -> None:
    load = importlib.import_module("scripts.milestone_2b_case_runners.load")
    release_root = tmp_path / "v1.0_260818" / ("1" * 40)
    release_root.mkdir(parents=True)
    context = CaseContext(release_root.resolve(), "run-1", "local")
    runner = vars(load)[case.runner.split(".", 1)[1]]

    async with _case_execution_scope(context, case.safety, None, case):
        outcome = await runner(context, case)

    assert outcome.status == "通过"
    assert outcome.reason == load.CASE_SPECS[case.case_id].reason
    evidence = json.loads((release_root / outcome.evidence[0]).read_text())
    checker = json.loads(evidence["payload"]["stdout"])
    assert checker["observed"]["canonical_evidence_modified"] is False
    assert checker["observed"]["result_directory_deleted"] is False

    cleanup_context = CaseContext(release_root.resolve(), "run-1", "local")
    async with _case_execution_scope(
        cleanup_context,
        case.safety,
        None,
        case,
    ):
        await runner.cleanup(cleanup_context, case)
    prefix = f"m2b-5-run-1-{case.case_id.lower()}-"
    temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
    assert not any(path.name.startswith(prefix) for path in temporary_root.iterdir())
