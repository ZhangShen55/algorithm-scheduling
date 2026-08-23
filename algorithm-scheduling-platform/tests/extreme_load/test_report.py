from __future__ import annotations

import json
import stat

import pytest

from scripts.extreme_load.aggregation import (
    CampaignAggregate,
    LoadBand,
    PerformanceSample,
    aggregate_evaluations,
    evaluate_case,
)
from scripts.extreme_load.metrics import GatewayCounters, MetricSummary
from scripts.extreme_load.report import (
    atomic_write_report,
    build_report_document,
    render_chinese_markdown,
    validate_public_payload,
)


def _sample() -> PerformanceSample:
    return PerformanceSample(
        total_requests=10,
        successful_requests=10,
        capacity_rejected=0,
        business_rejected=0,
        timeouts=0,
        connection_failures=0,
        unexpected_5xx=0,
        undefined_errors=0,
        latency_seconds=(0.1,) * 10,
        duration_seconds=1.0,
        queue_wait_p95_seconds=0.01,
        max_kafka_lag=2,
        peak_inflight=3,
        peak_active_leases=3,
        container_restarts=0,
    )


def _summary() -> CampaignAggregate:
    evaluation = evaluate_case(
        case_id="XL-BASE-001",
        load_band=LoadBand.BASELINE,
        sample=_sample(),
    )
    return aggregate_evaluations(("XL-BASE-001",), (evaluation,))


def test_report_contains_required_chinese_capacity_and_resource_fields() -> None:
    metrics = MetricSummary(
        sample_count=3,
        started_at="2026-08-23T00:00:00Z",
        finished_at="2026-08-23T00:00:02Z",
        gateway_delta=GatewayCounters(
            requests_total=10,
            lease_acquired_total=8,
            lease_rejected_total=2,
            lease_released_total=8,
        ),
        peak_inflight={"ocr-gpu0": 3},
        peak_active_leases={"ocr-gpu0": 3},
        max_kafka_lag=2,
        max_task_queue_depth=4,
        container_restart_delta={"online-gateway-service": 0},
        peak_gpu_utilization={"GPU-0": 75.0},
        peak_gpu_memory_bytes={"GPU-0": 500},
        gpu_process_names={"GPU-0": ("ocr",)},
        peak_container_cpu_percent={"online-gateway-service": 50.0},
        peak_container_memory_bytes={"online-gateway-service": 600},
        minimum_filesystem_free_bytes=200,
        minimum_host_memory_available_bytes=300,
        gateway_instance_request_delta={"ocr-gpu0": 8},
        peak_host_cpu_percent=65.0,
        host_network_receive_delta_bytes=1_000,
        host_network_transmit_delta_bytes=2_000,
        peak_host_open_sockets=300,
        peak_host_open_file_handles=400,
        recovery_seconds=1.5,
    )

    document = build_report_document(
        campaign_id="campaign-001",
        git_sha="a" * 40,
        summary=_summary(),
        metrics=metrics,
    )
    markdown = render_chinese_markdown(document)

    for text in (
        "极限负载 Campaign 报告",
        "P50",
        "P95",
        "P99",
        "吞吐",
        "Kafka Lag",
        "峰值 Inflight",
        "峰值活跃租约",
        "恢复时间",
        "预期过载",
        "容器 CPU 峰值",
        "实例请求累计差值",
        "未定义错误",
        "宿主机 CPU 峰值",
        "socket 峰值",
        "文件句柄峰值",
    ):
        assert text in markdown
    assert document["overall_status"] == "符合"


def test_atomic_report_write_is_private_and_write_once(tmp_path) -> None:  # type: ignore[no-untyped-def]
    target = tmp_path / "summary.json"
    atomic_write_report(target, json.dumps({"status": "符合"}, ensure_ascii=False))

    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert json.loads(target.read_text()) == {"status": "符合"}
    with pytest.raises(FileExistsError):
        atomic_write_report(target, "{}")


def test_atomic_report_rejects_a_symlink_parent(tmp_path) -> None:  # type: ignore[no-untyped-def]
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(OSError):
        atomic_write_report(linked_parent / "summary.json", "{}")

    assert not (real_parent / "summary.json").exists()


@pytest.mark.parametrize(
    "payload",
    (
        {"base64": "secret"},
        {"nested": {"embedding": [1, 2]}},
        {"password": "secret"},
        {"ocr_full_text": "完整文字"},
    ),
)
def test_public_report_rejects_sensitive_fields(payload: object) -> None:
    with pytest.raises(ValueError, match="敏感字段"):
        validate_public_payload(payload)
