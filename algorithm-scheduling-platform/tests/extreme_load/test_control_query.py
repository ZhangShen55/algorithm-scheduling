from __future__ import annotations

import pytest

from scripts.extreme_load.control_query import (
    QueryMode,
    build_negative_query_mix,
    build_query_requests,
    build_query_schedule,
    query_qps_tiers,
    validate_course_query_response,
)
from scripts.extreme_load.core import NorthboundTargets, ReproducibleIdentity

TARGETS = NorthboundTargets(
    control_origin="http://192.168.29.11:18100",
    gateway_origin="http://192.168.29.11:18103",
)


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


def test_query_response_requires_integer_node_statuses_and_expected_shape() -> None:
    valid = {
        "code": 0,
        "data": {
            "task_id": "course-1",
            "tasks": [
                {
                    "task_type": "PPT",
                    "status": 50,
                    "nodes": [{"node_code": "PPT_SLICE", "status": 60}],
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
    with pytest.raises(ValueError, match="qps"):
        build_query_requests(TARGETS, ("course-1",), qps=0, duration_seconds=1)


def test_negative_query_mix_is_reproducible_and_keeps_normal_requests() -> None:
    requests = build_query_requests(TARGETS, ("course-1",), qps=50, duration_seconds=1)
    mixed = build_negative_query_mix(requests, TARGETS, ratio=0.20, seed=5)

    assert mixed == build_negative_query_mix(requests, TARGETS, ratio=0.20, seed=5)
    assert sum(request.expected_business_rejection for request in mixed) == 10
    assert all(not request.expected_business_rejection for request in requests)
