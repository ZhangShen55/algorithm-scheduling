from __future__ import annotations

import pytest

from scripts.extreme_load.control_query import (
    ObservedCourseQuery,
    QueryMode,
    assess_priority_normal_checkpoint,
    build_negative_query_mix,
    build_query_requests,
    build_query_schedule,
    build_scheduled_query_requests,
    parse_course_query_response,
    query_qps_tiers,
    validate_control_readiness_response,
    validate_course_query_response,
    validate_monotonic_query_observations,
    validate_priority_claim_order,
)
from scripts.extreme_load.core import NorthboundTargets, ReproducibleIdentity

TARGETS = NorthboundTargets(
    control_origin="http://192.168.29.11:18100",
    gateway_origin="http://192.168.29.11:18103",
)


def _query_body(
    task_id: str,
    status: int,
    *,
    priority: str = "NORMAL",
    claimed_at: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    updated_at: str = "2026-08-23T00:00:00Z",
) -> dict[str, object]:
    node = {
        "node_code": "PPT_SLICE",
        "status": status,
        "priority": priority,
        "claimed_at": claimed_at,
        "started_at": started_at,
        "finished_at": finished_at,
        "updated_at": updated_at,
    }
    return {
        "code": 0,
        "data": {
            "task_id": task_id,
            "tasks": [
                {
                    "task_type": "PPT",
                    "status": status,
                    "nodes": [node],
                }
            ],
        },
    }


def test_query_requests_use_control_northbound_and_fixed_qps_tiers() -> None:
    requests = build_query_requests(
        TARGETS,
        task_ids=("course-1", "course-2"),
        qps=1000,
        duration_seconds=2,
    )

    assert len(requests) == 2000
    assert {request.url for request in requests} == {
        "http://192.168.29.11:18100/api/course-jobs/course-1",
        "http://192.168.29.11:18100/api/course-jobs/course-2",
    }
    assert query_qps_tiers() == (50, 100, 300, 1000)


def test_jittered_schedule_is_reproducible_and_herd_has_no_jitter() -> None:
    identity = ReproducibleIdentity("campaign-query", 9)
    jittered = build_query_schedule(
        identity,
        "QUERY-JITTER",
        task_count=100,
        polling_interval_seconds=2,
        mode=QueryMode.JITTERED,
    )
    replay = build_query_schedule(
        identity,
        "QUERY-JITTER",
        task_count=100,
        polling_interval_seconds=2,
        mode=QueryMode.JITTERED,
    )
    herd = build_query_schedule(
        identity,
        "QUERY-HERD",
        task_count=100,
        polling_interval_seconds=2,
        mode=QueryMode.HERD,
    )

    assert jittered == replay
    assert len(set(jittered.offsets_seconds)) > 1
    assert set(herd.offsets_seconds) == {0.0}


@pytest.mark.parametrize(("qps", "interval"), ((50, 2), (1000, 5)))
def test_scheduled_query_requests_model_virtual_pollers_at_requested_qps(
    qps: int,
    interval: int,
) -> None:
    identity = ReproducibleIdentity("campaign-query", 9)
    scheduled = build_scheduled_query_requests(
        TARGETS,
        identity,
        "QUERY-JITTER",
        ("course-1", "course-2"),
        qps=qps,
        duration_seconds=10,
        polling_interval_seconds=interval,
        mode=QueryMode.JITTERED,
    )
    replay = build_scheduled_query_requests(
        TARGETS,
        identity,
        "QUERY-JITTER",
        ("course-1", "course-2"),
        qps=qps,
        duration_seconds=10,
        polling_interval_seconds=interval,
        mode=QueryMode.JITTERED,
    )

    assert scheduled == replay
    assert len(scheduled) == qps * 10
    assert len({item.poller_id for item in scheduled}) == qps * interval
    assert min(item.scheduled_offset_seconds for item in scheduled) >= 0
    assert max(item.scheduled_offset_seconds for item in scheduled) < 10
    assert len({item.scheduled_offset_seconds for item in scheduled}) > 1


def test_herd_schedule_creates_interval_bursts_without_jitter() -> None:
    scheduled = build_scheduled_query_requests(
        TARGETS,
        ReproducibleIdentity("campaign-query", 9),
        "QUERY-HERD",
        ("course-1",),
        qps=50,
        duration_seconds=10,
        polling_interval_seconds=2,
        mode=QueryMode.HERD,
    )

    by_offset: dict[float, int] = {}
    for item in scheduled:
        by_offset[item.scheduled_offset_seconds] = (
            by_offset.get(item.scheduled_offset_seconds, 0) + 1
        )
    assert by_offset == {0.0: 100, 2.0: 100, 4.0: 100, 6.0: 100, 8.0: 100}


def test_scheduled_query_marks_large_asr_result_requests() -> None:
    scheduled = build_scheduled_query_requests(
        TARGETS,
        ReproducibleIdentity("campaign-query", 9),
        "QUERY-ASR",
        ("asr-course", "ppt-course"),
        qps=50,
        duration_seconds=2,
        polling_interval_seconds=2,
        mode=QueryMode.JITTERED,
        large_asr_task_ids=("asr-course",),
    )

    assert {item.request.work_type for item in scheduled} == {
        "course_query",
        "large_asr_result_query",
    }


def test_query_response_requires_integer_node_statuses_and_expected_shape() -> None:
    valid = {
        "code": 0,
        "data": {
            "task_id": "course-1",
            "tasks": [
                {
                    "task_type": "PPT",
                    "status": 50,
                    "nodes": [
                        {
                            "node_code": "PPT_SLICE",
                            "status": 60,
                            "priority": "NORMAL",
                        }
                    ],
                }
            ],
        },
    }

    assert validate_course_query_response(valid).valid
    invalid = {
        **valid,
        "data": {
            **valid["data"],
            "tasks": [{"task_type": "PPT", "status": "50", "nodes": []}],
        },
    }
    assert not validate_course_query_response(invalid).valid
    invalid_node_status = _query_body("course-1", 999)
    assert not validate_course_query_response(invalid_node_status).valid
    with pytest.raises(ValueError, match="qps"):
        build_query_requests(TARGETS, ("course-1",), qps=0, duration_seconds=1)


def test_negative_query_mix_is_reproducible_and_keeps_normal_requests() -> None:
    requests = build_query_requests(TARGETS, ("course-1",), qps=50, duration_seconds=1)
    mixed = build_negative_query_mix(requests, TARGETS, ratio=0.20, seed=5)

    assert mixed == build_negative_query_mix(requests, TARGETS, ratio=0.20, seed=5)
    assert sum(request.expected_business_rejection for request in mixed) == 10
    assert all(not request.expected_business_rejection for request in requests)


def test_control_readiness_requires_ready_status_and_all_dependency_checks() -> None:
    ready = validate_control_readiness_response(
        200,
        {
            "status": "ready",
            "checks": {
                "postgresql": {"ready": True},
                "redis": {"ready": True},
                "schema": {"ready": True},
            },
        },
    )
    degraded = validate_control_readiness_response(
        503,
        {
            "status": "not_ready",
            "checks": {
                "postgresql": {"ready": False},
                "redis": {"ready": True},
            },
        },
    )

    assert ready.ready
    assert ready.checked_dependencies == ("postgresql", "redis", "schema")
    assert not degraded.ready
    assert degraded.unready_dependencies == ("postgresql",)


def test_query_history_accepts_skipped_legal_states_and_rejects_terminal_regression() -> None:
    observations = tuple(
        ObservedCourseQuery(offset, f"request-{index}", parse_course_query_response(body))
        for index, (offset, body) in enumerate(
            (
                (0.0, _query_body("course-1", 10)),
                (
                    1.0,
                    _query_body(
                        "course-1",
                        50,
                        claimed_at="2026-08-23T00:00:01Z",
                        started_at="2026-08-23T00:00:02Z",
                        updated_at="2026-08-23T00:00:02Z",
                    ),
                ),
                (
                    2.0,
                    _query_body(
                        "course-1",
                        60,
                        claimed_at="2026-08-23T00:00:01Z",
                        started_at="2026-08-23T00:00:02Z",
                        finished_at="2026-08-23T00:00:03Z",
                        updated_at="2026-08-23T00:00:03Z",
                    ),
                ),
            )
        )
    )

    assert validate_monotonic_query_observations(observations).valid
    regressed = (
        *observations,
        ObservedCourseQuery(
            3.0,
            "request-regressed",
            parse_course_query_response(
                _query_body(
                    "course-1",
                    50,
                    claimed_at="2026-08-23T00:00:01Z",
                    started_at="2026-08-23T00:00:02Z",
                    updated_at="2026-08-23T00:00:04Z",
                )
            ),
        ),
    )
    verdict = validate_monotonic_query_observations(regressed)
    assert not verdict.valid
    assert "终态" in verdict.reason


def test_query_history_rejects_unreachable_integer_transition() -> None:
    observations = (
        ObservedCourseQuery(
            0.0,
            "request-1",
            parse_course_query_response(_query_body("course-1", 10)),
        ),
        ObservedCourseQuery(
            1.0,
            "request-2",
            parse_course_query_response(
                _query_body(
                    "course-1",
                    20,
                    updated_at="2026-08-23T00:00:01Z",
                )
            ),
        ),
    )

    verdict = validate_monotonic_query_observations(observations)
    assert not verdict.valid
    assert "不合法状态迁移" in verdict.reason


def test_priority_claim_order_uses_actual_timestamps_and_preserves_running_nodes() -> None:
    before = (
        parse_course_query_response(
            _query_body(
                "normal-running",
                50,
                claimed_at="2026-08-23T00:00:01Z",
                started_at="2026-08-23T00:00:02Z",
            )
        ),
        parse_course_query_response(_query_body("normal-waiting", 30)),
    )
    after = (
        parse_course_query_response(
            _query_body(
                "normal-running",
                60,
                claimed_at="2026-08-23T00:00:01Z",
                started_at="2026-08-23T00:00:02Z",
                finished_at="2026-08-23T00:00:10Z",
            )
        ),
        parse_course_query_response(
            _query_body(
                "normal-waiting",
                60,
                claimed_at="2026-08-23T00:00:05Z",
                started_at="2026-08-23T00:00:06Z",
                finished_at="2026-08-23T00:00:11Z",
            )
        ),
        parse_course_query_response(
            _query_body(
                "urgent-1",
                60,
                priority="URGENT",
                claimed_at="2026-08-23T00:00:03Z",
                started_at="2026-08-23T00:00:04Z",
                finished_at="2026-08-23T00:00:09Z",
            )
        ),
    )

    checkpoint = assess_priority_normal_checkpoint(
        before,
        ("normal-running", "normal-waiting"),
    )
    verdict = validate_priority_claim_order(
        before,
        after,
        normal_task_ids=("normal-running", "normal-waiting"),
        urgent_task_ids=("urgent-1",),
    )

    assert checkpoint.state == "ready"
    assert verdict.status == "passed"
    assert verdict.latest_urgent_claimed_at == "2026-08-23T00:00:03Z"
    assert verdict.earliest_overtaken_normal_claimed_at == "2026-08-23T00:00:05Z"


def test_priority_checkpoint_blocks_when_control_omits_claim_timestamps() -> None:
    checkpoint = assess_priority_normal_checkpoint(
        (
            parse_course_query_response(_query_body("normal-running", 50)),
            parse_course_query_response(_query_body("normal-waiting", 30)),
        ),
        ("normal-running", "normal-waiting"),
    )

    assert checkpoint.state == "blocked"
    assert "claimed_at/started_at" in checkpoint.reason


def test_priority_claim_order_fails_when_normal_is_claimed_before_urgent() -> None:
    before = (
        parse_course_query_response(
            _query_body(
                "normal-running",
                50,
                claimed_at="2026-08-23T00:00:01Z",
                started_at="2026-08-23T00:00:02Z",
            )
        ),
        parse_course_query_response(_query_body("normal-waiting", 30)),
    )
    after = (
        parse_course_query_response(
            _query_body(
                "normal-running",
                60,
                claimed_at="2026-08-23T00:00:01Z",
                started_at="2026-08-23T00:00:02Z",
                finished_at="2026-08-23T00:00:10Z",
            )
        ),
        parse_course_query_response(
            _query_body(
                "normal-waiting",
                60,
                claimed_at="2026-08-23T00:00:03Z",
                started_at="2026-08-23T00:00:04Z",
                finished_at="2026-08-23T00:00:10Z",
            )
        ),
        parse_course_query_response(
            _query_body(
                "urgent-1",
                60,
                priority="URGENT",
                claimed_at="2026-08-23T00:00:05Z",
                started_at="2026-08-23T00:00:06Z",
                finished_at="2026-08-23T00:00:11Z",
            )
        ),
    )

    verdict = validate_priority_claim_order(
        before,
        after,
        normal_task_ids=("normal-running", "normal-waiting"),
        urgent_task_ids=("urgent-1",),
    )

    assert verdict.status == "failed"
    assert "NORMAL" in verdict.reason
