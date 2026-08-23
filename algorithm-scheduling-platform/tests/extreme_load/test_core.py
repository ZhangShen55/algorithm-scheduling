from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from scripts.extreme_load.core import (
    AsyncLoadRunner,
    GuardrailAbort,
    HttpClientPool,
    HttpRequestSpec,
    LoadHostSnapshot,
    NorthboundTargets,
    ResultCategory,
    WorkerReport,
    WorkerShard,
    assess_load_host,
    classify_response,
    derive_campaign_id,
    evidence_contains_sensitive_material,
    redact_for_evidence,
    validate_worker_reports,
)


def test_northbound_targets_allow_only_control_and_gateway_ports() -> None:
    targets = NorthboundTargets(
        control_origin="http://192.168.29.11:18100",
        gateway_origin="http://192.168.29.11:18103",
    )

    assert targets.control_url("/api/course-jobs").endswith(":18100/api/course-jobs")
    assert targets.gateway_url("/api/online/ocr/recognize").endswith(
        ":18103/api/online/ocr/recognize"
    )
    assert targets.gateway_websocket_url("/api/online/asr/stream").startswith(
        "ws://192.168.29.11:18103/"
    )
    with pytest.raises(ValueError, match="18100"):
        NorthboundTargets(
            control_origin="http://192.168.29.11:5432",
            gateway_origin="http://192.168.29.11:18103",
        )
    with pytest.raises(ValueError, match="18103"):
        NorthboundTargets(
            control_origin="http://192.168.29.11:18100",
            gateway_origin="http://192.168.29.11:8866",
        )
    with pytest.raises(ValueError, match="/api/course-jobs"):
        targets.control_url("/api/course-jobs-evil")
    with pytest.raises(ValueError, match="/api/course-jobs"):
        HttpRequestSpec(
            request_id="redis-shortcut",
            method="GET",
            url="http://192.168.29.11:18100/api/operator-instances",
        )


@pytest.mark.asyncio
async def test_async_runner_bounds_concurrency_and_records_safe_results() -> None:
    active = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.002)
        active -= 1
        return httpx.Response(200, json={"code": 0, "data": {"text": "敏感转写"}})

    requests = [
        HttpRequestSpec(
            request_id=f"request-{index}",
            method="POST",
            url="http://target.test:18100/api/course-jobs",
            json_body={"task_id": f"task-{index}", "image": "AA=="},
        )
        for index in range(20)
    ]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await AsyncLoadRunner(client, max_concurrency=3).run(requests)

    assert peak == 3
    assert len(results) == 20
    assert all(result.category is ResultCategory.SUCCESS for result in results)
    assert all(
        "敏感转写" not in json.dumps(result.evidence, ensure_ascii=False)
        for result in results
    )


@pytest.mark.asyncio
async def test_async_runner_rate_limits_and_stops_when_guardrail_aborts() -> None:
    sent = 0
    abort = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sent
        del request
        sent += 1
        if sent == 2:
            abort.set()
        return httpx.Response(200, json={"code": 0})

    requests = [
        HttpRequestSpec(
            request_id=f"request-{index}",
            method="GET",
            url=f"http://target.test:18100/api/course-jobs/task-{index}",
        )
        for index in range(20)
    ]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await AsyncLoadRunner(client, max_concurrency=1).run(
            requests,
            requests_per_second=1000,
            abort_event=abort,
        )

    assert sent == 2
    assert results[-1].category is ResultCategory.GUARDRAIL_ABORT
    assert len(results) < len(requests)


def test_result_classifier_separates_business_overload_and_transport_failures() -> None:
    assert classify_response(200, {"code": 0}) is ResultCategory.SUCCESS
    assert classify_response(200, {"code": 40001}) is ResultCategory.BUSINESS_REJECTED
    assert classify_response(200, {"code": 50301}) is ResultCategory.OVERLOAD
    assert classify_response(429, None) is ResultCategory.OVERLOAD
    assert classify_response(503, None) is ResultCategory.OVERLOAD
    assert classify_response(500, None) is ResultCategory.UNDEFINED_5XX
    assert classify_response(200, {"code": 50000}) is ResultCategory.UNDEFINED_5XX


def test_redaction_removes_media_text_embedding_and_credentials() -> None:
    safe = redact_for_evidence(
        {
            "image": "AA==",
            "photo": "AA==",
            "StoragePath": "data:image/png;base64,AA==",
            "text": "完整 ASR 文本",
            "value": ["完整 OCR 文本"],
            "embedding": [0.1, 0.2],
            "password": "secret",
            "task_id": "course-001",
        }
    )
    encoded = json.dumps(safe, ensure_ascii=False)

    assert "完整 ASR" not in encoded
    assert "完整 OCR" not in encoded
    assert "secret" not in encoded
    assert "AA==" not in encoded
    assert safe["task_id"] == "course-001"


def test_worker_shards_are_disjoint_and_reports_detect_missing_or_drift() -> None:
    items = tuple(f"request-{index}" for index in range(10))
    selected = [set(WorkerShard(index, 3).select(items)) for index in range(3)]
    assert set.union(*selected) == set(items)
    assert not (selected[0] & selected[1] or selected[1] & selected[2])

    reports = [
        WorkerReport(
            campaign_id="campaign-sharded",
            worker_id=f"worker-{index}",
            request_ids=tuple(sorted(part)),
            clock_offset_ms=0,
        )
        for index, part in enumerate(selected)
    ]
    assert validate_worker_reports(reports, expected_workers=3, max_clock_drift_ms=50).passed
    assert not validate_worker_reports(
        reports[:-1], expected_workers=3, max_clock_drift_ms=50
    ).passed
    drifted = reports[0].model_copy(update={"clock_offset_ms": 500})
    assert not validate_worker_reports([drifted, *reports[1:]], 3, 50).passed
    wrong_campaign = reports[0].model_copy(update={"campaign_id": "other-campaign"})
    assert not validate_worker_reports([wrong_campaign, *reports[1:]], 3, 50).passed


def test_load_host_preflight_keeps_generator_limits_separate() -> None:
    healthy = LoadHostSnapshot(
        cpu_percent=20,
        memory_percent=30,
        open_sockets=100,
        file_descriptor_soft_limit=4096,
        network_utilization_percent=25,
    )
    exhausted = healthy.model_copy(update={"open_sockets": 3900})

    assert assess_load_host(healthy).ready
    assert not assess_load_host(exhausted).ready
    assert assess_load_host(exhausted).classification == "load_generator_limit"


def test_guardrail_abort_is_a_distinct_non_platform_failure() -> None:
    error = GuardrailAbort("磁盘红线")
    assert error.category is ResultCategory.GUARDRAIL_ABORT


def test_redaction_gate_and_http_pool_are_bounded() -> None:
    raw = {"image": "AA==", "task_id": "task-1"}
    redacted = redact_for_evidence(raw)

    assert evidence_contains_sensitive_material(raw)
    assert not evidence_contains_sensitive_material(redacted)
    pool = HttpClientPool(max_connections=128, max_keepalive_connections=32)
    assert pool.pool_timeout_seconds > 0


def test_campaign_id_is_derived_reproducibly_from_release_and_seed() -> None:
    first = derive_campaign_id("release/2026-08-23", 260823)

    assert first == derive_campaign_id("release/2026-08-23", 260823)
    assert first != derive_campaign_id("release/2026-08-24", 260823)
