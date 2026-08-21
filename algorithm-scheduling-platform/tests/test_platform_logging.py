from __future__ import annotations

import json
import logging

from packages.platform_common.logging import configure_logging, log_node_audit


def _flush_root() -> None:
    for handler in logging.getLogger().handlers:
        handler.flush()


def test_platform_logging_uses_file_and_stdout_with_trace_context(tmp_path, capsys) -> None:
    configure_logging(
        service_name="control-service",
        instance_id="control-local",
        project_root=tmp_path,
        logging_config={
            "directory": "logs",
            "file_name": "application.log",
            "max_file_size_mib": 1,
            "retention_days": 7,
        },
    )
    logging.getLogger("platform-test").info(
        "任务提交成功",
        extra={"task_id": "course-001", "request_body": "不要记录"},
    )
    _flush_root()

    path = tmp_path / "logs" / "control-local" / "application.log"
    file_line = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    stdout_line = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert {key: value for key, value in file_line.items() if key != "timestamp"} == {
        key: value for key, value in stdout_line.items() if key != "timestamp"
    }
    assert file_line["service"] == "control-service"
    assert file_line["instance_id"] == "control-local"
    assert file_line["task_id"] == "course-001"
    assert "request_body" not in file_line


def test_platform_node_audit_keeps_only_contract_fields(tmp_path) -> None:
    configure_logging(
        service_name="orchestrator-service",
        instance_id="worker-0",
        project_root=tmp_path,
    )
    log_node_audit(
        logging.getLogger("audit-test"),
        event="节点执行完成",
        task_id="course-001",
        task_type="PPT",
        node="PPT_SLICE",
        attempt=1,
        instance_id="ocr-0",
        model_version="v1",
        elapsed_ms=12.5,
        outcome="success",
    )
    _flush_root()
    payload = json.loads(
        (tmp_path / "logs" / "worker-0" / "application.log")
        .read_text(encoding="utf-8")
        .splitlines()[-1]
    )
    assert payload["task_id"] == "course-001"
    assert payload["task_type"] == "PPT"
    assert payload["node"] == "PPT_SLICE"
    assert payload["attempt"] == 1
    assert payload["elapsed_ms"] == 12.5
    assert payload["outcome"] == "success"
    assert "request" not in payload
    assert "response" not in payload


def test_platform_logging_reinitialization_does_not_duplicate_handlers(tmp_path) -> None:
    configure_logging(service_name="gateway", project_root=tmp_path)
    configure_logging(service_name="gateway", project_root=tmp_path)
    root = logging.getLogger()
    assert len(root.handlers) == 2
    assert len(logging.getLogger("uvicorn.error").handlers) == 0
    logging.getLogger("platform-idempotence").info("one event")
    _flush_root()
    lines = (
        tmp_path / "logs" / "local" / "application.log"
    ).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
