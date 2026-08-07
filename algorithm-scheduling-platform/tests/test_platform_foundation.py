import json
import logging
from io import StringIO
from pathlib import Path

import packages.platform_common.logging as platform_logging
from packages.platform_common.config import PlatformSettings
from packages.platform_common.logging import JsonFormatter
from packages.platform_common.trace import trace_context
from packages.platform_contracts.responses import BusinessCode, BusinessResponse
from packages.platform_contracts.status import NodeStatus, Priority, TaskType, status_text


def test_status_contract_uses_approved_integer_codes_and_chinese_labels() -> None:
    assert [status.value for status in NodeStatus] == [0, 10, 20, 30, 40, 50, 60, 70, 80]
    assert status_text(NodeStatus.WAITING_OPERATOR) == "等待算子"
    assert TaskType.PPT.value == "PPT"
    assert Priority.NORMAL.value == "NORMAL"


def test_business_response_envelope_keeps_http_and_business_outcomes_separate() -> None:
    accepted = BusinessResponse.success({"task_id": "course-001"}, message="任务已接收")
    rejected = BusinessResponse.failure(BusinessCode.VALIDATION_ERROR, "缺少视频地址")

    assert accepted.model_dump() == {
        "code": 0,
        "message": "任务已接收",
        "data": {"task_id": "course-001"},
    }
    assert rejected.code == 40001
    assert rejected.message == "缺少视频地址"
    assert rejected.data is None


def test_settings_load_typed_values_from_platform_prefixed_environment(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_SERVICE_NAME", "test-control")
    monkeypatch.setenv("PLATFORM_LOG_LEVEL", "debug")
    monkeypatch.setenv("PLATFORM_COURSE_ROOT", "/tmp/test-course")
    monkeypatch.setenv("PLATFORM_RESULT_ROOT", "/tmp/test-result")

    settings = PlatformSettings()

    assert settings.service_name == "test-control"
    assert settings.log_level == "DEBUG"
    assert settings.course_root == Path("/tmp/test-course")
    assert settings.result_root == Path("/tmp/test-result")


def test_json_formatter_includes_trace_and_structured_context() -> None:
    output = StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(JsonFormatter(service_name="control-service"))
    logger = logging.getLogger("foundation-test")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    with trace_context("trace-001"):
        logger.info("节点进入队列", extra={"task_id": "course-001", "node": "PPT_SLICE"})

    payload = json.loads(output.getvalue())
    assert payload["service"] == "control-service"
    assert payload["level"] == "INFO"
    assert payload["event"] == "节点进入队列"
    assert payload["trace_id"] == "trace-001"
    assert payload["task_id"] == "course-001"
    assert payload["node"] == "PPT_SLICE"


def test_node_audit_log_links_task_attempt_instance_model_and_elapsed_time() -> None:
    assert hasattr(platform_logging, "log_node_audit")
    output = StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(JsonFormatter(service_name="orchestrator-service"))
    logger = logging.getLogger("node-audit-test")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    with trace_context("trace-audit-001"):
        platform_logging.log_node_audit(
            logger,
            event="节点执行完成",
            task_id="course-001",
            task_type="ASR",
            node="ASR_TRANSCRIPTION",
            attempt=2,
            instance_id="asr-offline-gpu1",
            model_version="paraformer-v1.1.8",
            elapsed_ms=1250.5,
            outcome="COMPLETED",
        )

    payload = json.loads(output.getvalue())
    assert payload["audit_type"] == "node_execution"
    assert payload["trace_id"] == "trace-audit-001"
    assert payload["task_id"] == "course-001"
    assert payload["task_type"] == "ASR"
    assert payload["node"] == "ASR_TRANSCRIPTION"
    assert payload["attempt"] == 2
    assert payload["instance_id"] == "asr-offline-gpu1"
    assert payload["model_version"] == "paraformer-v1.1.8"
    assert payload["elapsed_ms"] == 1250.5
    assert payload["outcome"] == "COMPLETED"
