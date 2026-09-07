from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from scripts.vbas_benchmark.load import LoadRunConfig, run_sustained_load
from scripts.vbas_benchmark.models import (
    BenchmarkGuardrails,
    BenchmarkMode,
    FixtureEntry,
    FixtureManifest,
    VbasTarget,
)


def _manifest() -> FixtureManifest:
    return FixtureManifest(
        1,
        (),
        (
            FixtureEntry("teacher.jpg", 1, 1, 1, "teacher", "a" * 64),
            FixtureEntry("student.jpg", 1, 1, 1, "student", "b" * 64),
        ),
    )


def _response(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    return httpx.Response(
        200,
        json={
            "StatusObject": {"StatusCode": 0},
            "DataList": [
                {"StatusObject": {"StatusCode": 0, "ImageId": item["ImageId"]}, "ResultList": []}
                for item in body["ImageList"]
            ],
        },
    )


@pytest.mark.asyncio
async def test_sustained_loader_keeps_independent_target_windows_full() -> None:
    targets = (
        VbasTarget("vbas-gpu0", "http://vbas0:8981", 0, "vbas0"),
        VbasTarget("vbas-gpu1", "http://vbas1:8981", 1, "vbas1"),
    )
    result = await run_sustained_load(
        targets=targets,
        manifest=_manifest(),
        fixture_root_in_container="/data/course/benchmark",
        config=LoadRunConfig(
            mode=BenchmarkMode.MIXED,
            concurrency_per_instance=2,
            batch_size=1,
            warmup_seconds=0.01,
            steady_seconds=0.02,
            min_steady_batches=10,
            max_total_seconds=1,
            sample_interval_seconds=0.001,
            guardrails=BenchmarkGuardrails(
                max_replenish_p95_seconds=1,
                min_inflight_ratio=0,
            ),
        ),
        transport=httpx.MockTransport(_response),
    )

    assert result.status == "passed"
    assert result.summary["steady_success_count"] >= 10
    assert set(result.summary["success_by_instance"]) == {"vbas-gpu0", "vbas-gpu1"}
    assert {record.stream for record in result.records} == {"teacher", "student"}
    assert result.summary["replenish_p95_seconds"] < 1


@pytest.mark.asyncio
async def test_sustained_loader_stops_on_business_error_guardrail() -> None:
    def failure(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"StatusObject": {"StatusCode": 1}, "DataList": []})

    result = await run_sustained_load(
        targets=(VbasTarget("vbas-gpu0", "http://vbas0:8981", 0, "vbas0"),),
        manifest=_manifest(),
        fixture_root_in_container="/data/course/benchmark",
        config=LoadRunConfig(
            mode=BenchmarkMode.STUDENT,
            concurrency_per_instance=1,
            batch_size=1,
            warmup_seconds=0.01,
            steady_seconds=1,
            min_steady_batches=100,
            max_total_seconds=2,
            sample_interval_seconds=0.001,
            guardrails=BenchmarkGuardrails(max_error_rate=0),
        ),
        transport=httpx.MockTransport(failure),
    )

    assert result.status == "failed"
    assert "错误率" in result.reason
    assert result.summary["categories"]["business_error"] > 0


@pytest.mark.asyncio
async def test_external_guardrail_terminates_and_drains_workers() -> None:
    calls = 0

    def probe() -> str | None:
        nonlocal calls
        calls += 1
        return "采集器中断" if calls >= 2 else None

    result = await run_sustained_load(
        targets=(VbasTarget("vbas-gpu0", "http://vbas0:8981", 0, "vbas0"),),
        manifest=_manifest(),
        fixture_root_in_container="/data/course/benchmark",
        config=LoadRunConfig(
            mode=BenchmarkMode.TEACHER,
            concurrency_per_instance=1,
            batch_size=1,
            warmup_seconds=0.01,
            steady_seconds=1,
            min_steady_batches=100,
            max_total_seconds=2,
            sample_interval_seconds=0.001,
            guardrails=BenchmarkGuardrails(max_replenish_p95_seconds=1),
        ),
        transport=httpx.MockTransport(_response),
        guardrail_probe=probe,
    )

    assert result.status == "failed"
    assert result.reason == "采集器中断"
    assert not [
        task
        for task in asyncio.all_tasks()
        if task.get_name().startswith("vbas-benchmark-")
    ]
