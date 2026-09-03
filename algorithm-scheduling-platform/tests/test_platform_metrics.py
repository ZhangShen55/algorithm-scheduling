from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from packages.platform_common.application import create_service_app
from packages.platform_common.config import PlatformSettings


def test_metrics_endpoint_exposes_required_platform_dimensions(tmp_path: Path) -> None:
    app = create_service_app(
        PlatformSettings(
            service_name="metrics-test",
            course_root=tmp_path / "course",
            result_root=tmp_path / "result",
        )
    )
    metrics = app.state.platform_metrics
    metrics.set_task_state("PPT", 50, 3)
    metrics.set_node_state("PPT_OCR", 30, 2)
    metrics.set_outbox_pending(4)
    metrics.record_outbox_publish("published")
    metrics.set_kafka_lag("visual-commands", "vision-worker", 0, 7)
    metrics.set_operator_instance(
        operator_code="vbas",
        lifecycle="ONLINE",
        model_ready=True,
        gpu_label="0",
        count=1,
    )
    metrics.set_active_leases("vbas", "vbas-gpu0", 1)
    metrics.record_capacity_lease_event(
        capability="teacher_behavior",
        outcome="acquired",
        instance_id="vbas-gpu0",
    )
    metrics.record_capacity_recovery_event(
        capacity_pool="offline",
        capability="teacher_behavior",
        instance_id="vbas-gpu0",
        stage="lease_acquire",
        exception_type="control_transient_failure",
        outcome="retrying",
    )
    metrics.observe_operator_request(
        operator_code="vbas",
        capability="teacher_behavior",
        instance_id="vbas-gpu0",
        elapsed_seconds=0.25,
        success=False,
    )
    metrics.record_postgres_transaction_event(
        operation="claim_ready_node",
        sqlstate="40P01",
        outcome="recovered",
    )
    metrics.update_disk_usage(tmp_path / "course", kind="course")

    with TestClient(app) as client:
        response = client.get("/metrics")

    body = response.text
    assert response.status_code == 200
    assert 'algorithm_task_state{status="50",task_type="PPT"} 3.0' in body
    assert 'algorithm_node_state{node_code="PPT_OCR",status="30"} 2.0' in body
    assert "algorithm_outbox_pending 4.0" in body
    assert 'algorithm_outbox_publish_total{outcome="published"} 1.0' in body
    assert "algorithm_kafka_consumer_lag" in body
    assert 'gpu="0"' in body
    assert "algorithm_operator_active_leases" in body
    assert (
        'algorithm_capacity_lease_events_total{capability="teacher_behavior",'
        'instance_id="vbas-gpu0",outcome="acquired"} 1.0' in body
    )
    assert "algorithm_capacity_recovery_events_total" in body
    assert 'capacity_pool="offline"' in body
    assert 'exception_type="control_transient_failure"' in body
    assert "algorithm_operator_request_latency_seconds" in body
    assert "algorithm_operator_request_errors_total" in body
    assert (
        'algorithm_postgres_transaction_events_total{operation="claim_ready_node",'
        'outcome="recovered",sqlstate="40P01"} 1.0' in body
    )
    assert 'kind="course"' in body


def test_metrics_reject_retired_text_analysis_operator_code(tmp_path: Path) -> None:
    app = create_service_app(
        PlatformSettings(
            service_name="metrics-test",
            course_root=tmp_path / "course",
            result_root=tmp_path / "result",
        )
    )
    metrics = app.state.platform_metrics

    with pytest.raises(ValueError, match="不支持的当前算子代码"):
        metrics.set_operator_instance(
            operator_code="text_analysis",
            lifecycle="OFFLINE",
            model_ready=False,
            gpu_label="",
            count=1,
        )
    with pytest.raises(ValueError, match="不支持的当前算子代码"):
        metrics.set_active_leases("text_analysis", "text-analysis-cpu0", 1)
    with pytest.raises(ValueError, match="不支持的当前算子代码"):
        metrics.observe_operator_request(
            operator_code="text_analysis",
            capability="extract_keywords",
            instance_id="text-analysis-cpu0",
            elapsed_seconds=0.1,
            success=False,
        )

    assert "text_analysis" not in metrics.render().decode("utf-8")
