from __future__ import annotations

import fcntl
import hashlib
import importlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import socket
import stat
import subprocess
import threading
import time
import urllib.request
import uuid
import wave
from base64 import b64decode
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml  # type: ignore[import-untyped]

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PLATFORM_ROOT.parent
DEPLOY = PLATFORM_ROOT / "deploy"
SCRIPTS = DEPLOY / "scripts"
PYTHON = PLATFORM_ROOT / ".venv" / "bin" / "python"
SHA = "a" * 40
TAG = "v1.0_260812"

REGISTRATION_SPEC = importlib.util.spec_from_file_location(
    "task9_verify_operator_registration", SCRIPTS / "verify_operator_registration.py"
)
assert REGISTRATION_SPEC is not None and REGISTRATION_SPEC.loader is not None
REGISTRATION_MODULE = importlib.util.module_from_spec(REGISTRATION_SPEC)
REGISTRATION_SPEC.loader.exec_module(REGISTRATION_MODULE)
load_registration_expected = REGISTRATION_MODULE.load_expected
registration_atomic_json = REGISTRATION_MODULE.atomic_json

SMOKE_SPEC = importlib.util.spec_from_file_location(
    "task9_run_operator_smoke", SCRIPTS / "run_operator_smoke.py"
)
assert SMOKE_SPEC is not None and SMOKE_SPEC.loader is not None
SMOKE_MODULE = importlib.util.module_from_spec(SMOKE_SPEC)
SMOKE_SPEC.loader.exec_module(SMOKE_MODULE)
load_and_stage_fixtures = SMOKE_MODULE.load_and_stage_fixtures

RENDERER_MODULE = importlib.import_module("scripts.render_milestone_2b_report")
publish_report_transaction = RENDERER_MODULE.publish_report_transaction
render_report = RENDERER_MODULE.render


def _release(tmp_path: Path) -> Path:
    root = tmp_path / "reports" / "milestone-2b" / "releases" / TAG / SHA
    for category in ("registration", "smoke", "summary"):
        (root / category).mkdir(parents=True, mode=0o700, exist_ok=True)
    return root


class _Server:
    def __init__(
        self,
        responses: dict[str, tuple[int, Any]],
        post_handler: Any | None = None,
        delay_seconds: float = 0,
    ) -> None:
        self.responses = responses
        self.post_handler = post_handler
        self.delay_seconds = delay_seconds
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if owner.delay_seconds:
                    time.sleep(owner.delay_seconds)
                if self.path in owner.responses:
                    status, body = owner.responses[self.path]
                elif owner.post_handler is not None:
                    status, body = owner.post_handler(self.path, self.headers, b"")
                else:
                    status, body = 404, {"detail": "missing"}
                payload = json.dumps(body, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self) -> None:  # noqa: N802
                if owner.delay_seconds:
                    time.sleep(owner.delay_seconds)
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                if owner.post_handler is None:
                    status, payload_value = 404, {"detail": "missing"}
                else:
                    status, payload_value = owner.post_handler(self.path, self.headers, body)
                payload = json.dumps(payload_value, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_DELETE(self) -> None:  # noqa: N802
                self.do_POST()

            def log_message(self, *_: object) -> None:
                return

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        host, port = self.httpd.server_address
        return f"http://{host}:{port}"

    def __exit__(self, *_: object) -> None:
        self.httpd.shutdown()
        self.thread.join(timeout=3)
        self.httpd.server_close()


@contextmanager
def _face_servers(handler: Any) -> Iterator[list[str]]:
    with ExitStack() as stack:
        yield [stack.enter_context(_Server({}, handler)) for _ in range(3)]


def _expected_instances() -> list[dict[str, Any]]:
    contracts = {
        "asr-offline": ("asr_offline", ["asr_offline"], 8083, 1),
        "asr-online": ("asr_online", ["asr_online"], 8084, 1),
        "ocr": ("ocr", ["ocr"], 8866, 1),
        "vbas": ("vbas", ["student_behavior", "teacher_behavior"], 8981, 1),
        "facerec": ("facerec", ["recognize"], 8000, 1),
        "screen-det": ("screen_det", ["detect_all"], 8880, 1),
        "ppt-slice": ("ppt_slice", ["ppt_slice"], 9001, 15),
        "text-analysis": (
            "text_analysis",
            ["course_overviews", "extract_keywords"],
            8000,
            4,
        ),
    }
    result = []
    for prefix, (code, caps, port, capacity) in contracts.items():
        suffix = "gpu" if prefix not in {"ppt-slice", "text-analysis"} else "cpu"
        for index in range(3):
            labels = {"gpu": str(index)} if suffix == "gpu" else {}
            result.append(
                {
                    "instance_id": f"{prefix}-{suffix}{index}",
                    "operator_code": code,
                    "capabilities": caps,
                    "service_url": f"http://{prefix}-{suffix}{index}:{port}",
                    "declared_capacity": capacity,
                    "labels": labels,
                    "lifecycle": "ONLINE",
                    "inflight": 0,
                    "model_ready": True,
                    "last_heartbeat_at": "2026-08-12T00:00:01Z",
                }
            )
    return result


def _compose_with_environment_override(
    tmp_path: Path, variable: str, value: Any
) -> Path:
    document = yaml.safe_load(
        (DEPLOY / "docker-compose.operators.yml").read_text(encoding="utf-8")
    )
    document["services"]["asr-offline-gpu0"]["environment"][variable] = value
    output = tmp_path / "docker-compose.operators.yml"
    output.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return output


@pytest.mark.parametrize(
    "value",
    (
        "http://",
        "ftp://asr-offline-gpu0:8083",
        "   ",
        "http://:8083",
        "http://asr-offline-gpu0",
        "http://asr-offline-gpu0:0",
        "http://asr-offline-gpu0:65536",
        "http://bad host:8083",
    ),
)
def test_registration_verifier_rejects_invalid_compose_service_url(
    tmp_path: Path, value: str
) -> None:
    compose = _compose_with_environment_override(
        tmp_path, "PLATFORM_SERVICE_URL", value
    )

    with pytest.raises(ValueError, match="Compose service URL 无效"):
        load_registration_expected(compose)


@pytest.mark.parametrize(
    "value",
    (1.5, 1.0, True, float("inf"), b"1", "1.5", " 1 ", "+1"),
)
def test_registration_verifier_rejects_non_strict_compose_capacity(
    tmp_path: Path, value: Any
) -> None:
    compose = _compose_with_environment_override(
        tmp_path, "PLATFORM_DECLARED_CAPACITY", value
    )

    with pytest.raises(ValueError, match="Compose 声明容量无效"):
        load_registration_expected(compose)


def _events(instances: list[dict[str, Any]]) -> dict[str, tuple[int, Any]]:
    responses: dict[str, tuple[int, Any]] = {"/ops/operator-instances": (200, instances)}
    for item in instances:
        responses[f"/ops/operator-instances/{item['instance_id']}/events?limit=100"] = (
            200,
            [
                {"event_type": "REGISTERED"},
                {"event_type": "HEARTBEAT_SUMMARY", "event_payload": {"model_ready": True}},
            ],
        )
    return responses


def _run_registration(
    tmp_path: Path,
    url: str,
    *,
    timeout: str = "1",
    extra_arguments: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(SCRIPTS / "verify-operator-registration"),
            "--control-url",
            url,
            "--release-tag",
            TAG,
            "--git-sha",
            SHA,
            "--reports-root",
            str(tmp_path / "reports"),
            "--timeout-seconds",
            timeout,
            "--poll-seconds",
            "0.01",
            "--request-timeout-seconds",
            "0.2",
            *extra_arguments,
        ],
        cwd=PLATFORM_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_registration_verifier_accepts_explicit_gpu_profile_subset(
    tmp_path: Path,
) -> None:
    instances = [
        item
        for item in _expected_instances()
        if item["instance_id"].endswith("gpu0")
    ]
    with _Server(_events(instances)) as url:
        completed = _run_registration(
            tmp_path,
            url,
            extra_arguments=("--profile", "gpu0"),
        )

    assert completed.returncode == 0, completed.stderr
    output = (
        _release(tmp_path)
        / "registration"
        / "operator-registration-profile-gpu0.json"
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["selection"] == {"mode": "profile", "values": ["gpu0"]}
    assert report["summary"] == {"expected": 6, "observed": 6, "valid": 6}


def test_registration_verifier_accepts_instance_subset_among_other_rows(
    tmp_path: Path,
) -> None:
    instances = _expected_instances()
    with _Server(_events(instances)) as url:
        completed = _run_registration(
            tmp_path,
            url,
            extra_arguments=("--instance", "ocr-gpu0"),
        )

    assert completed.returncode == 0, completed.stderr
    output = (
        _release(tmp_path)
        / "registration"
        / "operator-registration-instance-ocr-gpu0.json"
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["selection"] == {"mode": "instance", "values": ["ocr-gpu0"]}
    assert report["summary"] == {"expected": 1, "observed": 1, "valid": 1}


def test_registration_instance_subset_rejects_rogue_instance(
    tmp_path: Path,
) -> None:
    instances = _expected_instances()
    instances.append(
        {
            **instances[0],
            "instance_id": "rogue-gpu0",
            "service_url": "http://rogue-gpu0:9999",
        }
    )
    with _Server(_events(instances)) as url:
        completed = _run_registration(
            tmp_path,
            url,
            timeout="0.08",
            extra_arguments=("--instance", "ocr-gpu0"),
        )
    assert completed.returncode != 0
    assert "rogue-gpu0" in completed.stderr


def test_registration_verifier_accepts_exact_ready_heartbeat_topology(tmp_path: Path) -> None:
    instances = _expected_instances()
    with _Server(_events(instances)) as url:
        completed = _run_registration(tmp_path, url)

    assert completed.returncode == 0, completed.stderr
    output = _release(tmp_path) / "registration" / "operator-registration.json"
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "通过"
    assert report["release_tag"] == TAG
    assert report["git_sha"] == SHA
    assert report["summary"] == {"expected": 24, "observed": 24, "valid": 24}
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_registration_report_publish_is_idempotent_but_rejects_divergent_rerun(
    tmp_path: Path,
) -> None:
    output = tmp_path / "registration.json"
    first = {"status": "通过", "sequence": 1}
    divergent = {"status": "失败", "sequence": 2}

    registration_atomic_json(output, first)
    original = output.read_bytes()
    registration_atomic_json(output, first)
    with pytest.raises(ValueError, match="已存在|write-once"):
        registration_atomic_json(output, divergent)

    assert output.read_bytes() == original


def test_registration_report_concurrent_divergent_writers_keep_first_bytes(
    tmp_path: Path,
) -> None:
    output = tmp_path / "registration.json"
    payloads = [
        {"writer": "first", "sequence": 1},
        {"writer": "second", "sequence": 2},
    ]

    def publish(payload: dict[str, Any]) -> tuple[str, bytes]:
        try:
            registration_atomic_json(output, payload)
        except ValueError:
            return "rejected", b""
        return "published", output.read_bytes()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(publish, payloads))

    assert sorted(status for status, _ in results) == ["published", "rejected"]
    published_bytes = next(data for status, data in results if status == "published")
    assert output.read_bytes() == published_bytes
    assert list(output.parent.glob(f".{output.name}.*.tmp")) == []


@pytest.mark.parametrize(
    ("mutate", "reason"),
    (
        (lambda rows: rows.pop(), "缺失"),
        (lambda rows: rows.append(dict(rows[0])), "重复"),
        (lambda rows: rows[0].update(operator_code="unexpected"), "operator_code"),
        (lambda rows: rows[0].update(capabilities=["unexpected"]), "capability"),
        (lambda rows: rows[0].update(lifecycle="OFFLINE"), "ONLINE"),
        (lambda rows: rows[0].update(model_ready=False), "model_ready"),
        (lambda rows: rows[0].update(declared_capacity=0), "容量"),
        (lambda rows: rows[0]["labels"].update(gpu="2"), "GPU"),
    ),
)
def test_registration_verifier_rejects_invalid_topology(
    tmp_path: Path, mutate: Any, reason: str
) -> None:
    instances = _expected_instances()
    mutate(instances)
    with _Server(_events(instances)) as url:
        completed = _run_registration(tmp_path, url, timeout="0.05")

    assert completed.returncode != 0
    assert reason in completed.stderr
    output = _release(tmp_path) / "registration" / "operator-registration.json"
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "失败"


@pytest.mark.parametrize(
    ("instance_id", "field", "value", "reason"),
    (
        ("asr-offline-gpu0", "service_url", "http://wrong-host:8083", "service_url"),
        ("ocr-gpu1", "service_url", "http://ocr-gpu1:9999", "service_url"),
        ("ppt-slice-cpu0", "declared_capacity", 2, "声明容量"),
        ("text-analysis-cpu0", "declared_capacity", 1, "声明容量"),
    ),
)
def test_registration_verifier_rejects_compose_contract_drift(
    tmp_path: Path,
    instance_id: str,
    field: str,
    value: Any,
    reason: str,
) -> None:
    instances = _expected_instances()
    instance = next(item for item in instances if item["instance_id"] == instance_id)
    instance[field] = value
    with _Server(_events(instances)) as url:
        completed = _run_registration(tmp_path, url, timeout="0.05")

    assert completed.returncode != 0
    assert reason in completed.stderr


def test_registration_verifier_requires_heartbeat_event(tmp_path: Path) -> None:
    instances = _expected_instances()
    responses = _events(instances)
    responses[f"/ops/operator-instances/{instances[0]['instance_id']}/events?limit=100"] = (
        200,
        [{"event_type": "REGISTERED"}],
    )
    with _Server(responses) as url:
        completed = _run_registration(tmp_path, url, timeout="1")

    assert completed.returncode != 0
    assert "首次心跳" in completed.stderr


@pytest.mark.parametrize("first_status", (200, 503))
def test_registration_retries_transient_list_state_until_ready(
    tmp_path: Path, first_status: int
) -> None:
    instances = _expected_instances()
    calls = 0

    def handler(path: str, _headers: Any, _raw: bytes) -> tuple[int, Any]:
        nonlocal calls
        if path == "/ops/operator-instances":
            calls += 1
            if calls == 1:
                return (
                    (200, instances[:-1])
                    if first_status == 200
                    else (503, {"detail": "starting"})
                )
            return 200, instances
        if path.endswith("/events?limit=100"):
            return 200, [
                {
                    "event_type": "HEARTBEAT_SUMMARY",
                    "event_payload": {"model_ready": True},
                }
            ]
        return 404, {"detail": "missing"}

    with _Server({}, handler) as url:
        completed = _run_registration(tmp_path, url, timeout="1")
    assert completed.returncode == 0, completed.stderr
    assert calls >= 2


def test_registration_persistent_protocol_failure_is_bounded_and_keeps_cause(
    tmp_path: Path,
) -> None:
    with _Server({}, lambda *_: (503, {"detail": "starting"})) as url:
        started = time.monotonic()
        completed = _run_registration(tmp_path, url, timeout="0.08")
        elapsed = time.monotonic() - started
    assert completed.returncode != 0
    assert elapsed < 1
    assert "503" in completed.stderr and "全局超时" in completed.stderr


def test_registration_exception_report_keeps_the_typed_envelope(
    tmp_path: Path,
) -> None:
    instances = _expected_instances()
    responses = _events(instances)
    first_instance = sorted(item["instance_id"] for item in instances)[0]
    responses[f"/ops/operator-instances/{first_instance}/events?limit=100"] = (
        200,
        [{"event_type": "HEARTBEAT_SUMMARY", "event_payload": ["invalid"]}],
    )

    with _Server(responses) as url:
        completed = _run_registration(tmp_path, url, timeout="0.08")

    assert completed.returncode == 2
    report = json.loads(
        (
            _release(tmp_path)
            / "registration"
            / "operator-registration.json"
        ).read_text(encoding="utf-8")
    )
    assert report["schema_version"] == 1
    assert report["evidence_type"] == "operator_registration"
    assert report["mock"] is False
    assert report["target"] == "operator-registry"
    assert report["status"] == "失败"
    assert report["release_tag"] == TAG
    assert report["git_sha"] == SHA
    assert report["started_at"]
    assert report["finished_at"]
    assert report["selection"] == {"mode": "full", "values": []}
    assert report["summary"] == {"expected": 24, "observed": 24, "valid": 0}
    assert report["issues"]


def test_renderer_render_emits_schema_v2_and_escapes_markdown_cells() -> None:
    case = {
        "case_id": "INF-001",
        "case_kind": "operator_smoke",
        "status": "失败",
        "target": "ocr|<script>alert(1)</script>",
        "reason": "bad|<img src=x>\nnext",
        "mock": False,
    }
    envelope = {
        "release_tag": TAG,
        "git_sha": SHA,
        "plan_sha256": "b" * 64,
        "coverage": {
            "smoke_full": {"expected": 1, "observed": 1, "passed": 0}
        },
    }

    cases_snapshot = RENDERER_MODULE.EvidenceSnapshot(
        relative_path="summary/cases.json",
        type="cases_envelope",
        size=123,
        sha256="c" * 64,
        content=b"{}",
        payload=envelope,
    )

    document, markdown = render_report(
        envelope, [case], [], "失败", cases_snapshot
    )

    assert document["schema_version"] == 2
    assert document["cases_input"] == {"bytes": 123, "sha256": "c" * 64}
    assert document["overall_status"] == "失败"
    assert "验收结论" in markdown
    assert "<script>" not in markdown and "<img" not in markdown
    assert "ocr\\|&lt;script&gt;" in markdown


TRANSACTION_JSON = b'{"schema_version":2,"overall_status":"through"}\n'
TRANSACTION_MARKDOWN = b"# report\n"
TRANSACTION_SUBPROCESS = """
import sys
from pathlib import Path
from scripts.render_milestone_2b_report import publish_report_transaction

release = Path(sys.argv[1])
summary = release / "summary"
publish_report_transaction(
    release,
    (
        (summary / "report.json", b'{"schema_version":2,"overall_status":"through"}\\n'),
        (summary / "report.md", b"# report\\n"),
    ),
    expected_root_identity=tuple(map(int, sys.argv[2].split(":"))),
)
raise SystemExit(3)
"""


def _transaction_outputs(
    release: Path,
    *,
    json_content: bytes = TRANSACTION_JSON,
    markdown_content: bytes = TRANSACTION_MARKDOWN,
) -> tuple[tuple[Path, bytes], tuple[Path, bytes]]:
    summary = release / "summary"
    return (
        (summary / "report.json", json_content),
        (summary / "report.md", markdown_content),
    )


def _publish_renderer_transaction(
    tmp_path: Path,
    *,
    json_content: bytes = TRANSACTION_JSON,
    markdown_content: bytes = TRANSACTION_MARKDOWN,
) -> None:
    release = _release(tmp_path)
    publish_report_transaction(
        release,
        _transaction_outputs(
            release,
            json_content=json_content,
            markdown_content=markdown_content,
        ),
        expected_root_identity=RENDERER_MODULE.release_root_identity(release),
    )


def _run_renderer_transaction_subprocess(
    release: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(PYTHON),
            "-c",
            TRANSACTION_SUBPROCESS,
            str(release),
            ":".join(map(str, RENDERER_MODULE.release_root_identity(release))),
        ],
        cwd=PLATFORM_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _transaction_journal_payload(
    transaction_id: str = "a" * 32,
    *,
    published: tuple[bool, bool] = (False, False),
    temporaries: tuple[str, ...] = (),
) -> dict[str, Any]:
    names = ("report.json", "report.md")
    contents = (TRANSACTION_JSON, TRANSACTION_MARKDOWN)
    return {
        "schema_version": 1,
        "transaction_id": transaction_id,
        "state": "publishing",
        "outputs": {
            name: {
                "sha256": hashlib.sha256(content).hexdigest(),
                "published": is_published,
            }
            for name, content, is_published in zip(
                names, contents, published, strict=True
            )
        },
        "temporaries": {
            name: f".{name}.{transaction_id}.tmp" for name in temporaries
        },
    }


def _write_transaction_journal(release: Path, payload: object) -> Path:
    journal = release / "summary/.report-transaction.journal"
    journal.write_text(json.dumps(payload, allow_nan=True), encoding="utf-8")
    journal.chmod(0o600)
    return journal


def test_renderer_snapshot_rejects_release_root_rebind_between_files(
    tmp_path: Path,
) -> None:
    release = _release(tmp_path)
    source = release / "summary/cases.json"
    source.write_text('{"schema_version":1}\n', encoding="utf-8")
    source.chmod(0o600)
    root_identity = RENDERER_MODULE.release_root_identity(release)
    first = RENDERER_MODULE._snapshot_release_json(
        release,
        "summary/cases.json",
        snapshot_type="cases_envelope",
        expected_root_identity=root_identity,
    )
    assert first.payload == {"schema_version": 1}

    displaced = release.with_name(f"{release.name}-displaced")
    release.rename(displaced)
    (release / "summary").mkdir(parents=True, mode=0o700)
    replacement = release / "summary/cases.json"
    replacement.write_text('{"schema_version":1}\n', encoding="utf-8")
    replacement.chmod(0o600)

    with pytest.raises(ValueError, match="root.*锚"):
        RENDERER_MODULE._snapshot_release_json(
            release,
            "summary/cases.json",
            snapshot_type="cases_envelope",
            expected_root_identity=root_identity,
        )


def test_registration_entrypoint_is_executable() -> None:
    path = SCRIPTS / "verify-operator-registration"
    assert path.is_file()
    assert os.access(path, os.X_OK)
    completed = subprocess.run(
        [str(path), "--help"], cwd=PLATFORM_ROOT, text=True, capture_output=True, check=False
    )
    assert completed.returncode == 0, completed.stderr


def test_smoke_entrypoint_and_manifest_are_strict() -> None:
    path = SCRIPTS / "run-operator-smoke"
    assert path.is_file()
    assert os.access(path, os.X_OK)
    completed = subprocess.run(
        [str(path), "--help"], cwd=PLATFORM_ROOT, text=True, capture_output=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
    manifest = json.loads((DEPLOY / "operator-smoke-cases.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert {case["operator_code"] for case in manifest["cases"]} == {
        "asr_offline",
        "asr_online",
        "ocr",
        "vbas",
        "facerec",
        "screen_det",
        "ppt_slice",
        "text_analysis",
    }
    assert all("checks" in case and case["checks"] for case in manifest["cases"])


@pytest.mark.parametrize(
    ("entrypoint", "python_script"),
    (
        ("verify-operator-registration", "verify_operator_registration.py"),
        ("run-operator-smoke", "run_operator_smoke.py"),
    ),
)
def test_deploy_entrypoint_falls_back_to_path_python3_without_project_venv(
    tmp_path: Path, entrypoint: str, python_script: str
) -> None:
    platform = tmp_path / "platform"
    scripts = platform / "deploy" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(SCRIPTS / entrypoint, scripts / entrypoint)
    (scripts / python_script).write_text(
        "import argparse\nargparse.ArgumentParser().parse_args()\n",
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "python3").symlink_to(PYTHON)

    completed = subprocess.run(
        [str(scripts / entrypoint), "--help"],
        cwd=platform,
        env={**os.environ, "PATH": f"{fake_bin}:/usr/bin:/bin", "DEPLOY_PYTHON": ""},
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "usage:" in completed.stdout


@pytest.mark.parametrize(
    ("entrypoint", "python_script"),
    (
        ("verify-operator-registration", "verify_operator_registration.py"),
        ("run-operator-smoke", "run_operator_smoke.py"),
    ),
)
def test_deploy_entrypoint_prefers_explicit_deploy_python(
    tmp_path: Path, entrypoint: str, python_script: str
) -> None:
    platform = tmp_path / "platform"
    scripts = platform / "deploy" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(SCRIPTS / entrypoint, scripts / entrypoint)
    (scripts / python_script).write_text(
        "import argparse\nargparse.ArgumentParser().parse_args()\n",
        encoding="utf-8",
    )
    project_bin = platform / ".venv" / "bin"
    project_bin.mkdir(parents=True)
    (project_bin / "python").symlink_to(PYTHON)
    marker = tmp_path / "deploy-python-used"
    deploy_python = tmp_path / "deploy-python"
    deploy_python.write_text(
        f"#!/bin/bash\nprintf used > {shlex.quote(str(marker))}\n"
        f"exec {shlex.quote(str(PYTHON))} \"$@\"\n",
        encoding="utf-8",
    )
    deploy_python.chmod(0o755)

    completed = subprocess.run(
        [str(scripts / entrypoint), "--help"],
        cwd=platform,
        env={**os.environ, "DEPLOY_PYTHON": str(deploy_python)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert marker.read_text(encoding="utf-8") == "used"


@pytest.mark.parametrize("entrypoint", ("verify-operator-registration", "run-operator-smoke"))
def test_deploy_entrypoint_reports_when_no_python_interpreter_is_available(
    tmp_path: Path, entrypoint: str
) -> None:
    platform = tmp_path / "platform"
    scripts = platform / "deploy" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(SCRIPTS / entrypoint, scripts / entrypoint)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    dirname = shutil.which("dirname")
    assert dirname is not None
    (fake_bin / "dirname").symlink_to(dirname)

    completed = subprocess.run(
        ["/bin/bash", str(scripts / entrypoint), "--help"],
        cwd=platform,
        env={**os.environ, "PATH": str(fake_bin), "DEPLOY_PYTHON": ""},
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 127
    assert "no usable Python interpreter" in completed.stderr


@pytest.mark.parametrize("entrypoint", ("verify-operator-registration", "run-operator-smoke"))
@pytest.mark.parametrize("invalid_kind", ("missing", "non_executable", "directory"))
def test_deploy_entrypoint_rejects_invalid_explicit_deploy_python_without_fallback(
    tmp_path: Path, entrypoint: str, invalid_kind: str
) -> None:
    platform = tmp_path / "platform"
    scripts = platform / "deploy" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(SCRIPTS / entrypoint, scripts / entrypoint)
    project_bin = platform / ".venv" / "bin"
    project_bin.mkdir(parents=True)
    (project_bin / "python").symlink_to(PYTHON)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "python3").symlink_to(PYTHON)

    deploy_python = tmp_path / "explicit-python"
    if invalid_kind == "non_executable":
        deploy_python.write_text("not executable\n", encoding="utf-8")
    elif invalid_kind == "directory":
        deploy_python.mkdir()

    completed = subprocess.run(
        [str(scripts / entrypoint), "--help"],
        cwd=platform,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "DEPLOY_PYTHON": str(deploy_python),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 127
    assert "DEPLOY_PYTHON is not an executable file" in completed.stderr


def test_gpu_acceptance_docs_contain_complete_executable_commands() -> None:
    readme = (DEPLOY / "README.md").read_text(encoding="utf-8")
    scenario = (
        PLATFORM_ROOT / "harness/scenarios/milestone-2b-deploy.md"
    ).read_text(encoding="utf-8")
    verification = (PLATFORM_ROOT / "harness/verification.md").read_text(
        encoding="utf-8"
    )
    for required in (
        "--release-tag",
        "--git-sha",
        "--reports-root",
        "--fixture-manifest",
        "--external-fixture-root",
        "--fixture-target-root",
        "--result-root",
        "--endpoints-json",
        "--operator",
        "--instance",
        "--run-id",
        "auto",
        "--repeat",
        "--hold-seconds",
        "--instance-id",
        "--callback-listen-host",
        "--callback-advertise-base-url",
    ):
        assert required in readme
    assert "run-operator-smoke is delivered by the later" not in readme
    assert "http://192.168.29.11:19090" not in scenario
    assert "docker network inspect algorithm-platform" in scenario
    assert '--callback-listen-host "$ALGORITHM_PLATFORM_GATEWAY"' in scenario
    assert (
        '--callback-advertise-base-url "http://${ALGORITHM_PLATFORM_GATEWAY}:19090"'
        in scenario
    )
    assert "Harness-only" in scenario
    assert "Smoke 结束后" in scenario and "关闭监听" in scenario
    assert "algorithm-platform" in verification and "19090" in verification

    assert "algorithm-operators" in scenario
    assert "algorithm-scheduling-platform" not in scenario.split(
        "com.docker.compose.project", 1
    )[1].split("service_name=", 1)[0]
    assert scenario.count("deploy/scripts/preflight operators --profile gpu0") == 1
    assert '--instance "$instance_id"' in scenario
    assert scenario.count("deploy/scripts/verify-operator-registration") == 2
    full_endpoints_arg = (
        "--endpoints-json "
        "/root/workspace/.algorithm-scheduling-fixtures/v1.0_260812/endpoints-full.json"
    )
    assert scenario.count(full_endpoints_arg) == 1
    assert "基础设施与平台容器也已存在且健康" not in scenario

    cpu_smoke = scenario.index("for operator_instance in")
    facerec_check = scenario.index("--instance facerec-gpu0")
    full_smoke = scenario.index(full_endpoints_arg)
    assert cpu_smoke < facerec_check < full_smoke

    assert "contains two independent offline ASR" not in readme
    assert "GPU 0/GPU 1" not in readme
    assert "24" in readme and "GPU 2" in readme
    assert "\n  ... \\" not in readme
    trigger_line = next(
        line
        for line in scenario.splitlines()
        if line.startswith("[") and "run-operator-smoke" in line
    )
    trigger_argv = json.loads(trigger_line)
    for option, value in (
        ("--operator", "asr_offline"),
        ("--instance", "asr-offline-gpu0"),
        ("--run-id", "auto"),
        ("--repeat", "1"),
        ("--hold-seconds", "30"),
    ):
        option_index = trigger_argv.index(option)
        assert trigger_argv[option_index + 1] == value
    assert '--instance-id "$instance_id"' in scenario


def test_stage45_docs_resolve_compose_container_ids_and_use_the_real_result_root() -> None:
    readme = (DEPLOY / "README.md").read_text(encoding="utf-8")
    scenario = (
        PLATFORM_ROOT / "harness/scenarios/milestone-2b-deploy.md"
    ).read_text(encoding="utf-8")

    for document in (readme, scenario):
        assert 'mapfile -t container_ids < <(' in document
        assert "docker compose -f deploy/docker-compose.operators.yml" in document
        assert 'ps --all --no-trunc -q "$service_name"' in document
        assert '[[ "${#container_ids[@]}" -ne 1 ]]' in document
        assert '--container "$container_id"' in document
        assert 'docker stop "$container_id"' in document
        assert 'docker restart "$container_id"' in document
        assert "/data/result/_harness" not in document
        assert "--container asr-offline-gpu0" not in document
        assert "docker stop asr-offline-gpu0" not in document
        assert "docker restart asr-offline-gpu0" not in document

    assert '"--result-root", "/data/result"' in readme
    assert '"--result-root", "/data/result"' in scenario
    assert "--result-root /data/result" in scenario
    assert "docker inspect -f '{{.State.Running}}' \"$face_instance\"" not in scenario


def test_canonical_scenario_uses_atomic_stop_only_operator_ledger() -> None:
    scenario = (
        PLATFORM_ROOT / "harness/scenarios/milestone-2b-deploy.md"
    ).read_text(encoding="utf-8")
    readme = (DEPLOY / "README.md").read_text(encoding="utf-8")

    for required in (
        "set -euo pipefail",
        'mktemp "$LEDGER_DIR/.baseline-operator-container-ids.XXXXXX"',
        'mktemp "$LEDGER_DIR/.current-operator-container-ids.XXXXXX"',
        'mktemp "$LEDGER_DIR/.new-operator-container-ids.XXXXXX"',
        '[[ ! "$container_id" =~ ^[0-9a-f]{64}$ ]]',
        "docker inspect -f '{{.Id}}'",
        'mv -f -- "$BASELINE_TMP" "$BASELINE_OPERATOR_IDS"',
        'mv -f -- "$NEW_TMP" "$NEW_OPERATOR_IDS"',
        'grep -Fqx -- "$container_id" "$BASELINE_OPERATOR_IDS"',
        'docker stop "$container_id"',
        "start_operator_profile()",
        'local profile="$1" up_status=0',
        'up -d || up_status=$?',
        "if ! refresh_new_operator_ledger; then",
        'if ! snapshot_current_operator_ids "$CURRENT_TMP"; then',
        'if ! comm -23 "$CURRENT_TMP" "$BASELINE_OPERATOR_IDS" >"$NEW_TMP"; then',
        'if ! mv -f -- "$NEW_TMP" "$NEW_OPERATOR_IDS"; then',
        'grep -Fqx -- "$container_id" "$BASELINE_OPERATOR_IDS" || grep_status=$?',
        'case "$grep_status" in',
        "禁止执行 cleanup",
        "待 Docker 恢复后基于 baseline 重新刷新",
        'return "$up_status"',
        "validate_operator_identity()",
        "com.docker.compose.project",
        "com.docker.compose.service",
    ):
        assert required in scenario

    for profile in ("gpu0", "gpu1", "gpu2", "cpu"):
        assert f"start_operator_profile {profile}" in scenario

    assert '>"$BASELINE_OPERATOR_IDS"' not in scenario
    assert '>"$NEW_OPERATOR_IDS"' not in scenario
    assert 'docker rm "$container_id"' not in scenario
    assert "partial-up" in readme
    assert "无论 Compose 成功或失败" in readme
    assert "禁止执行 cleanup" in readme
    assert scenario.count('if ! validate_operator_identity "$container_id"; then') == 2
    refresh_body = scenario.split("refresh_new_operator_ledger()", 1)[1].split(
        "start_operator_profile()", 1
    )[0]
    assert refresh_body.index("validate_operator_identity") < refresh_body.index(
        'mv -f -- "$NEW_TMP" "$NEW_OPERATOR_IDS"'
    )


def _extract_scenario_bash_block(heading: str) -> str:
    scenario = (
        PLATFORM_ROOT / "harness/scenarios/milestone-2b-deploy.md"
    ).read_text(encoding="utf-8")
    section = scenario.split(heading, 1)[1]
    match = re.search(r"```bash\n(?P<script>.*?)\n```", section, re.DOTALL)
    assert match is not None, f"{heading} 后缺少 Bash 代码块"
    return match.group("script")


def _extract_scenario_bash_blocks_before(heading: str) -> list[str]:
    scenario = (
        PLATFORM_ROOT / "harness/scenarios/milestone-2b-deploy.md"
    ).read_text(encoding="utf-8")
    prefix = scenario.split(heading, 1)[0]
    return re.findall(r"```bash\n(.*?)\n```", prefix, re.DOTALL)


def test_canonical_strict_mode_starts_with_release_variables_and_has_one_session_contract(
) -> None:
    scenario = (
        PLATFORM_ROOT / "harness/scenarios/milestone-2b-deploy.md"
    ).read_text(encoding="utf-8")
    blocks = _extract_scenario_bash_blocks_before(
        "### 本次远端执行结果（2026-08-12）"
    )

    assert blocks[0].splitlines()[0] == "set -euo pipefail"
    assert "阶段 1 到阶段 6" in scenario
    assert "同一 Bash 会话" in scenario
    stage_three = _extract_scenario_bash_block("## 阶段 3：平台和逐卡算子拓扑")
    assert "set -euo pipefail" not in stage_three
    assert "config --services" in stage_three
    assert 'grep -Fqx -- "$service_name" "$OPERATOR_SERVICE_ALLOWLIST_TMP"' in stage_three
    assert "asr-offline-gpu[012]|" not in stage_three


def test_platform_compose_waits_for_health_before_runtime_preflight() -> None:
    scenario = (
        PLATFORM_ROOT / "harness/scenarios/milestone-2b-deploy.md"
    ).read_text(encoding="utf-8")
    stage_three = _extract_scenario_bash_block(
        "## 阶段 3：平台和逐卡算子拓扑"
    )

    platform_up = (
        'docker compose -f deploy/docker-compose.platform.yml up -d --build '
        '--wait --wait-timeout "${PLATFORM_WAIT_TIMEOUT_SECONDS:-180}"'
    )
    runtime_preflight = (
        'deploy/scripts/preflight runtime --git-sha "$EXPECTED_GIT_SHA"'
    )

    assert platform_up in stage_three
    assert stage_three.index(platform_up) < stage_three.index(runtime_preflight)
    assert (
        'PLATFORM_WAIT_TIMEOUT_SECONDS="${PLATFORM_WAIT_TIMEOUT_SECONDS:-180}"'
        in scenario
    )
    assert '[[ "$PLATFORM_WAIT_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]]' in scenario


def test_platform_health_wait_is_consistent_in_operational_docs() -> None:
    platform_up = (
        'docker compose -f deploy/docker-compose.platform.yml up -d --build '
        '--wait --wait-timeout "${PLATFORM_WAIT_TIMEOUT_SECONDS:-180}"'
    )
    documents = (
        PLATFORM_ROOT / "README.md",
        PLATFORM_ROOT / "deploy/README.md",
        PLATFORM_ROOT / "deploy/单机运维与恢复手册.md",
        PLATFORM_ROOT / "harness/verification.md",
    )

    for document in documents:
        content = document.read_text(encoding="utf-8")
        assert platform_up in content, document
        assert (
            "docker compose -f deploy/docker-compose.platform.yml up -d --build\n"
            not in content
        ), document


@pytest.mark.parametrize(
    "timeout",
    ("0", "-1", " 1", "1.5", "--wait", "3601", "9" * 100),
)
def test_release_variables_reject_invalid_platform_wait_timeout(timeout: str) -> None:
    release_variables = _extract_scenario_bash_blocks_before(
        "### 本次远端执行结果（2026-08-12）"
    )[0]
    environment = {**os.environ, "PLATFORM_WAIT_TIMEOUT_SECONDS": timeout}

    completed = subprocess.run(
        ["bash", "-c", release_variables],
        cwd=PLATFORM_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "PLATFORM_WAIT_TIMEOUT_SECONDS" in completed.stderr


def _prepare_early_phase_shell(tmp_path: Path, failing_command: str) -> tuple[Path, dict[str, str]]:
    project_root = tmp_path / "project"
    scripts = project_root / "deploy/scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(SCRIPTS / "operator_lifecycle.py", scripts / "operator_lifecycle.py")
    release_root = (
        project_root
        / "deploy/reports/milestone-2b/releases"
        / TAG
        / SHA
    )
    for category in ("preflight", "container-maintenance"):
        (release_root / category).mkdir(parents=True)

    fake_command = """#!/usr/bin/env bash
command_name="${0##*/}"
printf '%s\\n' "$command_name" >>"$COMMAND_LOG"
if [[ "$command_name" == "$FAILING_COMMAND" ]]; then
  exit "$FAILURE_STATUS"
fi
exit 0
"""
    for command in (
        "verify-model-assets",
        "prepare-report-directory",
        "preflight",
        "snapshot-existing-containers",
        "pause-existing-containers",
        "stage-model-assets",
        "build-images",
    ):
        _write_executable(scripts / command, fake_command)

    fake_bin = tmp_path / "early-bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "python3",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'python3:%s\\n' "$*" >>"$COMMAND_LOG"
if [[ "$#" -eq 3 && "$1" == "-m" && "$2" == "venv" ]]; then
  venv_root="$3"
  mkdir -p "$venv_root/bin"
  cat >"$venv_root/bin/python" <<'PYTHON'
#!/usr/bin/env bash
set -euo pipefail
printf 'venv-python:%s\\n' "$*" >>"$COMMAND_LOG"
if [[ "$#" -eq 4 && "$1" == "-m" && "$2" == "pip" \
  && "$3" == "install" && "$4" == "." ]]; then
  exit 0
fi
if [[ "$#" -eq 1 && "$1" == "-" ]]; then
  cat >/dev/null
  runtime_evidence='{"python_executable":"/fake/.venv/bin/python",'
  runtime_evidence+='"python_version":"3.11.0","dependencies":{'
  runtime_evidence+='"httpx":"0.28.1","PyYAML":"6.0.3","websockets":"17.0.1"}}'
  printf '%s\\n' "$runtime_evidence"
  exit 0
fi
if [[ "${1:-}" == "deploy/scripts/operator_lifecycle.py" ]]; then
  exec "$REAL_PYTHON" "$@"
fi
exit 64
PYTHON
  chmod 0755 "$venv_root/bin/python"
  exit 0
fi
exit 64
""",
    )
    _write_executable(
        fake_bin / "git",
        f"#!/usr/bin/env bash\nprintf '%s\\n' '{SHA}'\n",
    )
    _write_executable(fake_bin / "flock", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(
        fake_bin / "docker",
        """#!/usr/bin/env bash
printf 'docker:%s\\n' "$*" >>"$COMMAND_LOG"
if [[ "${1:-}" == "inspect" ]]; then
  printf '%s\\n' '{}'
fi
exit 0
""",
    )
    command_log = tmp_path / "early-commands.log"
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "COMMAND_LOG": str(command_log),
        "FAILING_COMMAND": failing_command,
        "FAILURE_STATUS": "41",
        "REAL_PYTHON": str(PYTHON),
    }
    return project_root, environment


@pytest.mark.parametrize(
    ("failing_command", "forbidden_later_command"),
    (
        ("preflight", "snapshot-existing-containers"),
        ("stage-model-assets", "build-images"),
    ),
)
def test_canonical_early_phases_stop_at_first_failure(
    tmp_path: Path,
    failing_command: str,
    forbidden_later_command: str,
) -> None:
    blocks = _extract_scenario_bash_blocks_before(
        "### 本次远端执行结果（2026-08-12）"
    )
    project_root, environment = _prepare_early_phase_shell(tmp_path, failing_command)

    completed = subprocess.run(
        ["bash", "-c", "\n".join(blocks)],
        cwd=project_root,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 41, completed.stderr
    commands = (tmp_path / "early-commands.log").read_text(encoding="utf-8").splitlines()
    assert any(command.startswith("python3:-m venv ") for command in commands)
    assert "venv-python:-m pip install ." in commands
    assert "venv-python:-" in commands
    assert failing_command in commands
    assert forbidden_later_command not in commands


def _container_id(index: int) -> str:
    return f"{index:064x}"


def _operator_containers(
    compose_path: Path = DEPLOY / "docker-compose.operators.yml",
) -> tuple[dict[str, list[str]], dict[str, dict[str, Any]]]:
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    project = compose["name"]
    profiles: dict[str, list[str]] = {}
    containers: dict[str, dict[str, Any]] = {}
    for index, (service, specification) in enumerate(
        sorted(compose["services"].items()), start=1
    ):
        container_id = _container_id(index)
        published_ports = [
            int(str(port).rsplit(":", 2)[-2])
            for port in specification.get("ports", [])
        ]
        containers[container_id] = {
            "project": project,
            "service": service,
            "published_ports": published_ports,
        }
        for profile in specification.get("profiles", []):
            profiles.setdefault(profile, []).append(container_id)
    return profiles, containers


def test_fake_lifecycle_topology_is_derived_from_operator_compose(tmp_path: Path) -> None:
    compose = yaml.safe_load((DEPLOY / "docker-compose.operators.yml").read_text(encoding="utf-8"))
    compose_services = compose["services"]
    derived_profiles, derived_containers = _operator_containers(
        DEPLOY / "docker-compose.operators.yml"
    )

    assert {metadata["service"] for metadata in derived_containers.values()} == set(
        compose_services
    )
    assert {
        profile: {
            service
            for service in compose_services
            if profile in compose_services[service]["profiles"]
        }
        for profile in {item for spec in compose_services.values() for item in spec["profiles"]}
    } == {
        profile: {derived_containers[container_id]["service"] for container_id in ids}
        for profile, ids in derived_profiles.items()
    }

    extra_service = "fixture-only-cpu3"
    compose["services"][extra_service] = {
        **compose["services"]["ppt-slice-cpu0"],
        "profiles": ["fixture"],
        "environment": {
            **compose["services"]["ppt-slice-cpu0"]["environment"],
            "PLATFORM_INSTANCE_ID": extra_service,
        },
    }
    compose_path = tmp_path / "operators.yml"
    compose_path.write_text(
        yaml.safe_dump(compose, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    profiles_with_extra, containers_with_extra = _operator_containers(compose_path)
    assert profiles_with_extra["fixture"]
    assert extra_service in {
        metadata["service"] for metadata in containers_with_extra.values()
    }


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _prepare_fake_lifecycle(
    tmp_path: Path,
    *,
    up_status: dict[str, int] | None = None,
    mutation: tuple[str, str] | None = None,
    failure_injection: dict[str, Any] | None = None,
    initial_profiles: tuple[str, ...] = (),
    include_baseline: bool = True,
    replace_profiles: tuple[str, ...] = (),
) -> tuple[Path, Path, dict[str, str], str, dict[str, list[str]]]:
    project_root = tmp_path / "project"
    scripts = project_root / "deploy/scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(SCRIPTS / "operator_lifecycle.py", scripts / "operator_lifecycle.py")
    _write_executable(
        scripts / "preflight",
        """#!/usr/bin/env bash
printf '%s' "${AUTHORIZED_OCCUPIED_ENDPOINTS:-}" >"$FAKE_DOCKER_STATE/authorized-endpoints.log"
if [[ -n "${EXPECTED_AUTHORIZED_ENDPOINTS:-}" && \
  "${AUTHORIZED_OCCUPIED_ENDPOINTS:-}" != "$EXPECTED_AUTHORIZED_ENDPOINTS" ]]; then
  exit 74
fi
if [[ -n "${FAKE_UNAUTHORIZED_OCCUPIED_ENDPOINT:-}" && \
  " ${AUTHORIZED_OCCUPIED_ENDPOINTS:-} " != *" $FAKE_UNAUTHORIZED_OCCUPIED_ENDPOINT "* ]]; then
  exit 75
fi
exit 0
""",
    )
    _write_executable(
        scripts / "restore-existing-containers",
        """#!/usr/bin/env bash
printf '%s\n' "$@" >"$FAKE_DOCKER_STATE/restore-arguments.log"
exit 0
""",
    )
    _write_executable(
        scripts / "snapshot-existing-containers",
        """#!/usr/bin/env bash
printf '%s:%s\n' "${0##*/}" "$*" >>"$FAKE_DOCKER_STATE/maintenance.log"
if [[ -e "$1" || -L "$1" ]]; then
  exit 72
fi
exit 0
""",
    )
    _write_executable(
        scripts / "pause-existing-containers",
        """#!/usr/bin/env bash
printf '%s:%s\n' "${0##*/}" "$*" >>"$FAKE_DOCKER_STATE/maintenance.log"
if [[ -e "${1}.paused.jsonl" || -L "${1}.paused.jsonl" ]]; then
  exit 73
fi
exit 0
""",
    )

    profiles, containers = _operator_containers()
    compose_container_ids = {
        container_id for profile_ids in profiles.values() for container_id in profile_ids
    }
    compose_services = sorted(
        containers[container_id]["service"] for container_id in compose_container_ids
    )
    baseline_id = _container_id(999)
    ocr_metadata = next(
        metadata
        for metadata in containers.values()
        if metadata["service"] == "ocr-gpu0"
    )
    containers[baseline_id] = dict(ocr_metadata)
    replacements_on_up: dict[str, dict[str, str]] = {}
    replacement_index = 2000
    for profile in replace_profiles:
        replacements_on_up[profile] = {}
        for old_container_id in profiles[profile]:
            replacement_index += 1
            new_container_id = _container_id(replacement_index)
            replacements_on_up[profile][old_container_id] = new_container_id
            containers[new_container_id] = dict(containers[old_container_id])
    if mutation is not None:
        mutation_kind, profile = mutation
        target_id = profiles[profile][0]
        if mutation_kind == "inspect":
            containers[target_id]["inspect_fails"] = True
        elif mutation_kind == "project":
            containers[target_id]["project"] = "untrusted-project"
            containers[target_id]["force_compose_ps"] = True
        elif mutation_kind == "service":
            containers[target_id]["service"] = "untrusted-service"
        else:  # pragma: no cover - test helper misuse
            raise AssertionError(f"未知 mutation: {mutation_kind}")

    state_dir = tmp_path / "docker-state"
    state_dir.mkdir()
    initial_ids = [
        container_id
        for profile in initial_profiles
        for container_id in profiles[profile]
    ]
    if include_baseline:
        initial_ids.append(baseline_id)
    state = {
        "current": initial_ids,
        "profiles": profiles,
        "containers": containers,
        "compose_services": compose_services,
        "replacements_on_up": replacements_on_up,
        "up_status": up_status or {},
        "compose_documents": {
            "docker-compose.platform.yml": {
                "name": "algorithm-scheduling-platform",
                "services": {},
            },
            "docker-compose.operators.yml": {
                "name": "algorithm-operators",
                "services": {
                    metadata["service"]: {
                        "ports": [
                            {
                                "host_ip": "127.0.0.1",
                                "published": str(port),
                                "protocol": "tcp",
                                "target": port,
                            }
                            for port in metadata["published_ports"]
                        ]
                    }
                    for metadata in containers.values()
                    if metadata["project"] == "algorithm-operators"
                },
            },
        },
        **(failure_injection or {}),
    }
    (state_dir / "state.json").write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8"
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = """#!/usr/bin/env python3
import json
import os
import pathlib
import sys

state_dir = pathlib.Path(os.environ["FAKE_DOCKER_STATE"])
state_path = state_dir / "state.json"
state = json.loads(state_path.read_text(encoding="utf-8"))
args = sys.argv[1:]
with (state_dir / "calls.log").open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(args) + "\\n")

def save() -> None:
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    temporary.replace(state_path)

if args and args[0] == "compose":
    compose_file = pathlib.Path(args[args.index("-f") + 1]).name
    if "config" in args and "--format" in args:
        print(json.dumps(state["compose_documents"][compose_file]))
        raise SystemExit(0)
    if "config" in args and "--services" in args:
        print("\\n".join(state["compose_services"]))
        raise SystemExit(0)
    if "up" in args:
        if "--profile" in args:
            profile = args[args.index("--profile") + 1]
            for container_id in state["profiles"].get(profile, []):
                replacement_id = state["replacements_on_up"].get(profile, {}).get(
                    container_id, container_id
                )
                if container_id != replacement_id and container_id in state["current"]:
                    state["current"].remove(container_id)
                if replacement_id not in state["current"]:
                    state["current"].append(replacement_id)
            save()
            raise SystemExit(int(state["up_status"].get(profile, 0)))
        raise SystemExit(0)
    if "ps" in args:
        if "-q" in args:
            state["compose_ps_calls"] = int(state.get("compose_ps_calls", 0)) + 1
            save()
            if state["compose_ps_calls"] == state.get("compose_ps_fail_on_call"):
                raise SystemExit(67)
            expected_project = state["compose_documents"][compose_file]["name"]
            requested_services = set(args[args.index("-q") + 1 :])
            print(
                "\\n".join(
                    container_id
                    for container_id in state["current"]
                    if (
                        (
                            not requested_services
                            or state["containers"][container_id]["service"]
                            in requested_services
                        )
                        and (
                            state["containers"][container_id]["project"]
                            == expected_project
                            or state["containers"][container_id].get("force_compose_ps")
                        )
                    )
                )
            )
        raise SystemExit(0)

if args and args[0] == "inspect":
    if "-f" not in args and args[-1] != "ocr-v6-amd":
        records = []
        for container_id in args[1:]:
            metadata = state["containers"].get(container_id)
            if metadata is None or metadata.get("inspect_fails"):
                raise SystemExit(1)
            records.append(
                {
                    "Id": container_id,
                    "State": {"Running": container_id in state["current"]},
                    "Config": {
                        "Labels": {
                            "com.docker.compose.project": metadata["project"],
                            "com.docker.compose.service": metadata["service"],
                        }
                    },
                    "NetworkSettings": {
                        "Ports": metadata.get("inspect_ports") or {
                            f"{port}/tcp": [
                                {"HostIp": "127.0.0.1", "HostPort": str(port)}
                            ]
                            for port in metadata["published_ports"]
                        }
                    },
                }
            )
        print(json.dumps(records))
        raise SystemExit(0)
    container_id = args[-1]
    if container_id == "ocr-v6-amd" and "-f" not in args:
        print("{}")
        raise SystemExit(0)
    metadata = state["containers"].get(container_id)
    if metadata is None or metadata.get("inspect_fails"):
        raise SystemExit(1)
    template = args[args.index("-f") + 1]
    if template == "{{.Id}}":
        print(container_id)
    elif "com.docker.compose.project" in template:
        print(metadata["project"])
    elif "com.docker.compose.service" in template:
        print(metadata["service"])
    else:
        raise SystemExit(2)
    raise SystemExit(0)

if args and args[0] == "stop":
    with (state_dir / "stops.log").open("a", encoding="utf-8") as stream:
        stream.write(args[1] + "\\n")
    raise SystemExit(0)

raise SystemExit(2)
"""
    _write_executable(fake_bin / "docker", fake_docker)
    fake_comm = """#!/usr/bin/env python3
import json
import os
import pathlib
import sys

state_dir = pathlib.Path(os.environ["FAKE_DOCKER_STATE"])
state_path = state_dir / "state.json"
state = json.loads(state_path.read_text(encoding="utf-8"))
state["comm_calls"] = int(state.get("comm_calls", 0)) + 1
temporary = state_path.with_suffix(".tmp")
temporary.write_text(json.dumps(state), encoding="utf-8")
temporary.replace(state_path)
if state["comm_calls"] == state.get("comm_fail_on_call"):
    raise SystemExit(68)
if len(sys.argv) != 4 or sys.argv[1] != "-23":
    raise SystemExit(2)
left = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8").splitlines()
right = set(pathlib.Path(sys.argv[3]).read_text(encoding="utf-8").splitlines())
for line in left:
    if line not in right:
        print(line)
"""
    _write_executable(fake_bin / "comm", fake_comm)
    fake_mktemp = """#!/usr/bin/env python3
import json
import os
import pathlib
import sys
import tempfile

state_dir = pathlib.Path(os.environ["FAKE_DOCKER_STATE"])
state_path = state_dir / "state.json"
state = json.loads(state_path.read_text(encoding="utf-8"))
template = pathlib.Path(sys.argv[-1])
pattern = str(state.get("mktemp_fail_pattern", ""))
if pattern and pattern in str(template):
    state["mktemp_matching_calls"] = int(state.get("mktemp_matching_calls", 0)) + 1
temporary = state_path.with_suffix(".tmp")
temporary.write_text(json.dumps(state), encoding="utf-8")
temporary.replace(state_path)
if pattern and pattern in str(template) and (
    state["mktemp_matching_calls"] == state.get("mktemp_fail_on_matching_call")
):
    raise SystemExit(69)
prefix = template.name.removesuffix("XXXXXX")
descriptor, path = tempfile.mkstemp(prefix=prefix, dir=template.parent)
os.close(descriptor)
print(path)
"""
    _write_executable(fake_bin / "mktemp", fake_mktemp)
    fake_flock = """#!/usr/bin/env python3
import fcntl
import sys

if len(sys.argv) != 3 or sys.argv[1] != "-n":
    raise SystemExit(2)
try:
    fcntl.flock(int(sys.argv[2]), fcntl.LOCK_EX | fcntl.LOCK_NB)
except (BlockingIOError, OSError):
    raise SystemExit(1)
"""
    _write_executable(fake_bin / "flock", fake_flock)

    report_root = tmp_path / "reports"
    release_root = report_root / "milestone-2b" / "releases" / TAG / SHA
    (release_root / "container-maintenance").mkdir(parents=True)
    (release_root / "preflight").mkdir()
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_DOCKER_STATE": str(state_dir),
        "RELEASE_ROOT": str(release_root),
        "EXPECTED_GIT_SHA": SHA,
        "RELEASE_TAG": TAG,
        "REPORT_ROOT": str(report_root),
        "SNAPSHOT": str(tmp_path / "snapshot.json"),
        "PAUSED_LEDGER": str(tmp_path / "snapshot.json.paused.jsonl"),
        "PREVIOUS_RELEASE_ROOT": "",
        "DEPLOY_PYTHON": str(PYTHON),
    }
    return project_root, release_root, environment, baseline_id, profiles


def _run_prepared_lifecycle(
    project_root: Path,
    environment: dict[str, str],
    script: str,
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    release_block = _extract_scenario_bash_blocks_before(
        "## 阶段 1：服务器预检、快照和暂停"
    )[0]
    lifecycle_functions = release_block.split(
        "validate_previous_release_root()", 1
    )[1]
    lifecycle_functions = "validate_previous_release_root()" + lifecycle_functions
    completed = subprocess.run(
        [
            "bash",
            "-c",
            "\n".join(
                (
                    _extract_scenario_bash_blocks_before(
                        "## 阶段 1：服务器预检、快照和暂停"
                    )[0].splitlines()[0],
                    "OPERATOR_LIFECYCLE_LOCK_PID=",
                    "OPERATOR_LIFECYCLE_LOCK_CONTROL_FD=",
                    "OPERATOR_LIFECYCLE_LOCK_READY_FD=",
                    lifecycle_functions,
                    "acquire_operator_lifecycle_lock",
                    script,
                )
            ),
        ],
        cwd=project_root,
        env=environment,
        text=True,
        errors="replace",
        capture_output=True,
        check=False,
    )
    state_dir = Path(environment["FAKE_DOCKER_STATE"])
    state = json.loads((state_dir / "state.json").read_text(encoding="utf-8"))
    return completed, state


def _run_lifecycle_script(
    tmp_path: Path,
    script: str,
    **fixture_options: Any,
) -> tuple[subprocess.CompletedProcess[str], Path, dict[str, Any], str, dict[str, list[str]]]:
    project_root, release_root, environment, baseline_id, profiles = (
        _prepare_fake_lifecycle(tmp_path, **fixture_options)
    )
    completed, state = _run_prepared_lifecycle(project_root, environment, script)
    return completed, release_root, state, baseline_id, profiles


def _ledger_ids(release_root: Path, ledger_name: str) -> list[str]:
    ledger = release_root / "container-maintenance" / ledger_name
    return ledger.read_text(encoding="utf-8").splitlines()


def _write_operator_ledgers(
    release_root: Path, baseline_ids: list[str], new_ids: list[str]
) -> None:
    ledger_dir = release_root / "container-maintenance"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    for ledger_name, container_ids in (
        ("baseline-operator-container-ids.txt", baseline_ids),
        ("new-operator-container-ids.txt", new_ids),
    ):
        content = "".join(f"{container_id}\n" for container_id in container_ids)
        (ledger_dir / ledger_name).write_text(content, encoding="utf-8")


def _previous_release_root(environment: dict[str, str]) -> Path:
    return (
        Path(environment["REPORT_ROOT"])
        / "milestone-2b"
        / "releases"
        / TAG
        / ("b" * 40)
    )


def _release_root_for_sha(environment: dict[str, str], git_sha: str) -> Path:
    return (
        Path(environment["REPORT_ROOT"])
        / "milestone-2b"
        / "releases"
        / TAG
        / git_sha
    )


def _maintenance_paths(release_root: Path) -> tuple[Path, Path, Path]:
    ledger_dir = release_root / "container-maintenance"
    snapshot = ledger_dir / "existing-containers.jsonl"
    paused = ledger_dir / "existing-containers.jsonl.paused.jsonl"
    provenance = ledger_dir / "operator-maintenance-provenance.json"
    return snapshot, paused, provenance


def _write_maintenance_ledgers(release_root: Path) -> tuple[Path, Path]:
    snapshot, paused, _ = _maintenance_paths(release_root)
    snapshot.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    snapshot.write_text('{"name":"ocr-v6-amd"}\n', encoding="utf-8")
    paused.write_text(
        '{"name":"ocr-v6-amd","was_running":true}\n', encoding="utf-8"
    )
    return snapshot, paused


def _write_maintenance_provenance(
    release_root: Path,
    *,
    source_release_root: Path,
    authoritative_snapshot: Path,
    authoritative_paused: Path,
) -> Path:
    _, _, provenance = _maintenance_paths(release_root)
    provenance.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    provenance.write_text(
        json.dumps(
            {
                "authoritative_paused_ledger": str(authoritative_paused),
                "authoritative_snapshot": str(authoritative_snapshot),
                "source_git_sha": source_release_root.name,
                "source_release_root": str(source_release_root),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    provenance.chmod(0o400)
    return provenance


def _prepare_cross_sha_operator_ledger_chain(
    environment: dict[str, str],
    *,
    baseline_ids: list[str],
    new_ids: list[str],
) -> tuple[Path, Path, Path, Path, Path]:
    authority_root = _release_root_for_sha(environment, "d" * 40)
    authority_snapshot, authority_paused = _write_maintenance_ledgers(authority_root)
    ledger_root = _release_root_for_sha(environment, "c" * 40)
    _write_operator_ledgers(ledger_root, baseline_ids, new_ids)
    ledger_provenance = _write_maintenance_provenance(
        ledger_root,
        source_release_root=authority_root,
        authoritative_snapshot=authority_snapshot,
        authoritative_paused=authority_paused,
    )
    immediate_root = _previous_release_root(environment)
    immediate_provenance = _write_maintenance_provenance(
        immediate_root,
        source_release_root=ledger_root,
        authoritative_snapshot=authority_snapshot,
        authoritative_paused=authority_paused,
    )
    return (
        authority_root,
        ledger_root,
        immediate_root,
        ledger_provenance,
        immediate_provenance,
    )


def _run_operator_ledger_resolver(
    environment: dict[str, str], previous_root: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(PYTHON),
            str(SCRIPTS / "operator_lifecycle.py"),
            "resolve-operator-ledgers",
            "--report-root",
            environment["REPORT_ROOT"],
            "--release-tag",
            TAG,
            "--previous-release-root",
            str(previous_root),
        ],
        cwd=PLATFORM_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _prepare_deep_maintenance_state_chain(
    environment: dict[str, str], baseline_id: str
) -> tuple[Path, Path]:
    ledger_root = _release_root_for_sha(environment, "d" * 40)
    authoritative_snapshot, authoritative_paused = _write_maintenance_ledgers(
        ledger_root
    )
    _write_operator_ledgers(ledger_root, [baseline_id], [])
    intermediate_root = _release_root_for_sha(environment, "c" * 40)
    _write_maintenance_provenance(
        intermediate_root,
        source_release_root=ledger_root,
        authoritative_snapshot=authoritative_snapshot,
        authoritative_paused=authoritative_paused,
    )
    immediate_root = _previous_release_root(environment)
    _write_maintenance_provenance(
        immediate_root,
        source_release_root=intermediate_root,
        authoritative_snapshot=authoritative_snapshot,
        authoritative_paused=authoritative_paused,
    )
    return immediate_root, intermediate_root


def _release_tag_lock_path(environment: dict[str, str]) -> Path:
    return (
        Path(environment["REPORT_ROOT"])
        / "milestone-2b"
        / "releases"
        / TAG
        / ".operator-lifecycle.lock"
    )


def _stage_three_initialization() -> str:
    stage_three = _extract_scenario_bash_block("阶段 3：平台和逐卡算子拓扑")
    return stage_three.split('EXPECTED_GIT_SHA="$EXPECTED_GIT_SHA"', 1)[0]


def _stage_one_and_three_initialization() -> str:
    stage_one = _extract_scenario_bash_block(
        "## 阶段 1：服务器预检、快照和暂停"
    )
    return f"{stage_one}\n{_stage_three_initialization()}"


def _docker_calls(environment: dict[str, str]) -> list[list[str]]:
    calls_path = Path(environment["FAKE_DOCKER_STATE"]) / "calls.log"
    if not calls_path.exists():
        return []
    return [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]


def test_new_release_inherits_previous_baseline_and_immediately_refreshes_new_ledger(
    tmp_path: Path,
) -> None:
    project_root, release_root, environment, _, profiles = _prepare_fake_lifecycle(
        tmp_path,
        initial_profiles=("gpu0",),
        include_baseline=False,
    )
    previous_root = _previous_release_root(environment)
    previous_new = sorted(profiles["gpu0"])
    _write_operator_ledgers(previous_root, [], previous_new)
    environment["PREVIOUS_RELEASE_ROOT"] = str(previous_root)

    completed, _ = _run_prepared_lifecycle(
        project_root, environment, _stage_three_initialization()
    )

    assert completed.returncode == 0, completed.stderr
    assert _ledger_ids(release_root, "baseline-operator-container-ids.txt") == []
    assert _ledger_ids(release_root, "new-operator-container-ids.txt") == previous_new


def test_new_release_rejects_previous_new_when_it_is_not_current_minus_baseline(
    tmp_path: Path,
) -> None:
    project_root, release_root, environment, _, profiles = _prepare_fake_lifecycle(
        tmp_path,
        initial_profiles=("gpu0",),
        include_baseline=False,
    )
    previous_root = _previous_release_root(environment)
    _write_operator_ledgers(previous_root, [], sorted(profiles["gpu0"][:-1]))
    environment["PREVIOUS_RELEASE_ROOT"] = str(previous_root)

    completed, _ = _run_prepared_lifecycle(
        project_root, environment, _stage_three_initialization()
    )

    assert completed.returncode != 0
    assert "previous new ledger" in completed.stderr
    ledger_dir = release_root / "container-maintenance"
    assert not (ledger_dir / "baseline-operator-container-ids.txt").exists()
    assert not (ledger_dir / "new-operator-container-ids.txt").exists()


@pytest.mark.parametrize("existing_ledger", ("baseline", "new"))
def test_current_release_rejects_partial_operator_ledger(
    tmp_path: Path,
    existing_ledger: str,
) -> None:
    project_root, release_root, environment, baseline_id, _ = _prepare_fake_lifecycle(
        tmp_path
    )
    ledger_dir = release_root / "container-maintenance"
    ledger_name = f"{existing_ledger}-operator-container-ids.txt"
    content = f"{baseline_id}\n" if existing_ledger == "baseline" else ""
    (ledger_dir / ledger_name).write_text(content, encoding="utf-8")

    completed, _ = _run_prepared_lifecycle(
        project_root, environment, _stage_three_initialization()
    )

    assert completed.returncode != 0
    assert "partial ledger" in completed.stderr
    assert not any(call and call[0] == "compose" for call in _docker_calls(environment))


def test_current_release_complete_ledgers_resume_without_replacing_baseline(
    tmp_path: Path,
) -> None:
    project_root, release_root, environment, baseline_id, profiles = (
        _prepare_fake_lifecycle(tmp_path, initial_profiles=("gpu0",))
    )
    previous_new = sorted(profiles["gpu0"])
    _write_operator_ledgers(release_root, [baseline_id], previous_new)

    completed, _ = _run_prepared_lifecycle(
        project_root, environment, _stage_three_initialization()
    )

    assert completed.returncode == 0, completed.stderr
    assert _ledger_ids(release_root, "baseline-operator-container-ids.txt") == [
        baseline_id
    ]
    assert _ledger_ids(release_root, "new-operator-container-ids.txt") == previous_new


def test_same_sha_full_phase_one_and_three_reuses_active_current_maintenance_ledgers(
    tmp_path: Path,
) -> None:
    project_root, release_root, environment, baseline_id, _ = _prepare_fake_lifecycle(
        tmp_path
    )
    current_snapshot, current_paused = _write_maintenance_ledgers(release_root)
    _write_operator_ledgers(release_root, [baseline_id], [])

    completed, state = _run_prepared_lifecycle(
        project_root, environment, _stage_one_and_three_initialization()
    )

    assert completed.returncode == 0, completed.stderr
    expected_endpoints = sorted(
        f"127.0.0.1:{port}"
        for port in state["containers"][baseline_id]["published_ports"]
    )
    assert (
        Path(environment["FAKE_DOCKER_STATE"]) / "authorized-endpoints.log"
    ).read_text(encoding="utf-8") == " ".join(expected_endpoints)
    assert not (Path(environment["FAKE_DOCKER_STATE"]) / "maintenance.log").exists()
    assert current_snapshot.read_text(encoding="utf-8") == '{"name":"ocr-v6-amd"}\n'
    assert "was_running" in current_paused.read_text(encoding="utf-8")
    assert _ledger_ids(release_root, "baseline-operator-container-ids.txt") == [
        baseline_id
    ]


def test_third_sha_uses_previous_operator_ledgers_and_original_maintenance_authority(
    tmp_path: Path,
) -> None:
    project_root, release_root, environment, baseline_id, _ = _prepare_fake_lifecycle(
        tmp_path
    )
    authority_root = _release_root_for_sha(environment, "c" * 40)
    authority_snapshot, authority_paused = _write_maintenance_ledgers(authority_root)
    previous_root = _previous_release_root(environment)
    _write_operator_ledgers(previous_root, [baseline_id], [])
    _write_maintenance_provenance(
        previous_root,
        source_release_root=authority_root,
        authoritative_snapshot=authority_snapshot,
        authoritative_paused=authority_paused,
    )
    environment["PREVIOUS_RELEASE_ROOT"] = str(previous_root)

    first_run, _ = _run_prepared_lifecycle(
        project_root, environment, _stage_one_and_three_initialization()
    )
    second_run, _ = _run_prepared_lifecycle(
        project_root, environment, _stage_one_and_three_initialization()
    )

    assert first_run.returncode == 0, first_run.stderr
    assert second_run.returncode == 0, second_run.stderr
    assert _ledger_ids(release_root, "baseline-operator-container-ids.txt") == [
        baseline_id
    ]
    _, _, provenance = _maintenance_paths(release_root)
    assert stat.S_IMODE(provenance.stat().st_mode) == 0o400
    assert json.loads(provenance.read_text(encoding="utf-8")) == {
        "authoritative_paused_ledger": str(authority_paused),
        "authoritative_snapshot": str(authority_snapshot),
        "source_git_sha": "b" * 40,
        "source_release_root": str(previous_root),
    }
    assert not (Path(environment["FAKE_DOCKER_STATE"]) / "maintenance.log").exists()


def test_fourth_sha_resolves_nearest_complete_operator_ledgers_through_provenance(
    tmp_path: Path,
) -> None:
    project_root, release_root, environment, baseline_id, _ = _prepare_fake_lifecycle(
        tmp_path
    )
    (
        authority_root,
        ledger_root,
        immediate_root,
        ledger_provenance,
        immediate_provenance,
    ) = _prepare_cross_sha_operator_ledger_chain(
        environment,
        baseline_ids=[baseline_id],
        new_ids=[],
    )
    original_ledger_provenance = ledger_provenance.read_bytes()
    original_immediate_provenance = immediate_provenance.read_bytes()
    environment["PREVIOUS_RELEASE_ROOT"] = str(immediate_root)

    completed, _ = _run_prepared_lifecycle(
        project_root, environment, _stage_one_and_three_initialization()
    )

    assert completed.returncode == 0, completed.stderr
    assert _ledger_ids(release_root, "baseline-operator-container-ids.txt") == [
        baseline_id
    ]
    assert _ledger_ids(release_root, "new-operator-container-ids.txt") == []
    assert ledger_provenance.read_bytes() == original_ledger_provenance
    assert immediate_provenance.read_bytes() == original_immediate_provenance
    _, _, current_provenance = _maintenance_paths(release_root)
    assert stat.S_IMODE(current_provenance.stat().st_mode) == 0o400
    assert json.loads(current_provenance.read_text(encoding="utf-8")) == {
        "authoritative_paused_ledger": str(
            _maintenance_paths(authority_root)[1]
        ),
        "authoritative_snapshot": str(_maintenance_paths(authority_root)[0]),
        "source_git_sha": immediate_root.name,
        "source_release_root": str(immediate_root),
    }
    assert ledger_root != immediate_root
    assert not (Path(environment["FAKE_DOCKER_STATE"]) / "maintenance.log").exists()


def test_cross_sha_operator_ledger_resolution_preserves_current_minus_previous_gate(
    tmp_path: Path,
) -> None:
    project_root, release_root, environment, _, _ = _prepare_fake_lifecycle(tmp_path)
    authority_root, _, immediate_root, _, _ = _prepare_cross_sha_operator_ledger_chain(
        environment,
        baseline_ids=[],
        new_ids=[],
    )
    environment["PREVIOUS_RELEASE_ROOT"] = str(immediate_root)

    completed, _ = _run_prepared_lifecycle(
        project_root, environment, _stage_one_and_three_initialization()
    )

    assert completed.returncode != 0
    assert "current - previous baseline" in completed.stderr
    ledger_dir = release_root / "container-maintenance"
    assert not (ledger_dir / "baseline-operator-container-ids.txt").exists()
    assert not (ledger_dir / "new-operator-container-ids.txt").exists()
    _, _, current_provenance = _maintenance_paths(release_root)
    current_payload = json.loads(current_provenance.read_text(encoding="utf-8"))
    assert current_payload["source_release_root"] == str(immediate_root)
    assert current_payload["authoritative_snapshot"] == str(
        _maintenance_paths(authority_root)[0]
    )


def test_operator_ledger_resolver_rejects_partial_ancestor(tmp_path: Path) -> None:
    _, _, environment, baseline_id, _ = _prepare_fake_lifecycle(tmp_path)
    authority_root = _release_root_for_sha(environment, "d" * 40)
    authority_snapshot, authority_paused = _write_maintenance_ledgers(authority_root)
    partial_root = _release_root_for_sha(environment, "c" * 40)
    partial_dir = partial_root / "container-maintenance"
    partial_dir.mkdir(parents=True)
    (partial_dir / "baseline-operator-container-ids.txt").write_text(
        f"{baseline_id}\n", encoding="utf-8"
    )
    immediate_root = _previous_release_root(environment)
    _write_maintenance_provenance(
        immediate_root,
        source_release_root=partial_root,
        authoritative_snapshot=authority_snapshot,
        authoritative_paused=authority_paused,
    )

    completed = _run_operator_ledger_resolver(environment, immediate_root)

    assert completed.returncode != 0
    assert "partial" in completed.stderr


def test_operator_ledger_resolver_rejects_provenance_cycle(tmp_path: Path) -> None:
    _, _, environment, _, _ = _prepare_fake_lifecycle(tmp_path)
    authority_root = _release_root_for_sha(environment, "d" * 40)
    authority_snapshot, authority_paused = _write_maintenance_ledgers(authority_root)
    first_root = _previous_release_root(environment)
    second_root = _release_root_for_sha(environment, "c" * 40)
    _write_maintenance_provenance(
        first_root,
        source_release_root=second_root,
        authoritative_snapshot=authority_snapshot,
        authoritative_paused=authority_paused,
    )
    _write_maintenance_provenance(
        second_root,
        source_release_root=first_root,
        authoritative_snapshot=authority_snapshot,
        authoritative_paused=authority_paused,
    )

    completed = _run_operator_ledger_resolver(environment, first_root)

    assert completed.returncode != 0
    assert "operator ledger provenance cycle" in completed.stderr


def test_operator_ledger_resolver_rejects_chain_without_ledger_ancestor(
    tmp_path: Path,
) -> None:
    _, _, environment, _, _ = _prepare_fake_lifecycle(tmp_path)
    authority_root = _release_root_for_sha(environment, "d" * 40)
    authority_snapshot, authority_paused = _write_maintenance_ledgers(authority_root)
    empty_root = _release_root_for_sha(environment, "c" * 40)
    (empty_root / "container-maintenance").mkdir(parents=True)
    immediate_root = _previous_release_root(environment)
    _write_maintenance_provenance(
        immediate_root,
        source_release_root=empty_root,
        authoritative_snapshot=authority_snapshot,
        authoritative_paused=authority_paused,
    )

    completed = _run_operator_ledger_resolver(environment, immediate_root)

    assert completed.returncode != 0
    assert "no complete operator ledger ancestor" in completed.stderr


def test_operator_ledger_resolver_rejects_deep_partial_maintenance_state(
    tmp_path: Path,
) -> None:
    _, _, environment, baseline_id, _ = _prepare_fake_lifecycle(tmp_path)
    immediate_root, intermediate_root = _prepare_deep_maintenance_state_chain(
        environment, baseline_id
    )
    intermediate_snapshot, _, _ = _maintenance_paths(intermediate_root)
    intermediate_snapshot.write_text('{"name":"partial"}\n', encoding="utf-8")

    completed = _run_operator_ledger_resolver(environment, immediate_root)

    assert completed.returncode != 0
    assert "maintenance snapshot/paused ledger state is partial" in completed.stderr


def test_operator_ledger_resolver_rejects_deep_direct_and_provenance_ambiguity(
    tmp_path: Path,
) -> None:
    _, _, environment, baseline_id, _ = _prepare_fake_lifecycle(tmp_path)
    immediate_root, intermediate_root = _prepare_deep_maintenance_state_chain(
        environment, baseline_id
    )
    _write_maintenance_ledgers(intermediate_root)

    completed = _run_operator_ledger_resolver(environment, immediate_root)

    assert completed.returncode != 0
    assert "maintenance state is ambiguous" in completed.stderr


def test_same_sha_existing_provenance_rejects_rebinding_to_another_previous_release(
    tmp_path: Path,
) -> None:
    project_root, release_root, environment, baseline_id, _ = _prepare_fake_lifecycle(
        tmp_path
    )
    authority_root = _release_root_for_sha(environment, "c" * 40)
    authority_snapshot, authority_paused = _write_maintenance_ledgers(authority_root)
    original_previous = _previous_release_root(environment)
    original_provenance = _write_maintenance_provenance(
        release_root,
        source_release_root=original_previous,
        authoritative_snapshot=authority_snapshot,
        authoritative_paused=authority_paused,
    ).read_bytes()
    _write_operator_ledgers(release_root, [baseline_id], [])
    other_previous = _release_root_for_sha(environment, "d" * 40)
    _write_maintenance_ledgers(other_previous)
    _write_operator_ledgers(other_previous, [baseline_id], [])
    environment["PREVIOUS_RELEASE_ROOT"] = str(other_previous)

    completed, _ = _run_prepared_lifecycle(
        project_root, environment, _stage_one_and_three_initialization()
    )

    assert completed.returncode != 0
    assert "provenance" in completed.stderr
    _, _, provenance = _maintenance_paths(release_root)
    assert provenance.read_bytes() == original_provenance


def test_release_tag_lock_rejects_concurrent_sha_before_any_compose_command(
    tmp_path: Path,
) -> None:
    project_root, _, environment, _, _ = _prepare_fake_lifecycle(tmp_path)
    lock_path = (
        Path(environment["REPORT_ROOT"])
        / "milestone-2b"
        / "releases"
        / TAG
        / ".operator-lifecycle.lock"
    )
    lock_path.touch(mode=0o600)

    with lock_path.open("w", encoding="utf-8") as lock_stream:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        completed, _ = _run_prepared_lifecycle(
            project_root, environment, _stage_three_initialization()
        )

    assert completed.returncode != 0
    assert "another SHA" in completed.stderr
    assert not any(call and call[0] == "compose" for call in _docker_calls(environment))


@pytest.mark.parametrize("unsafe_kind", ("symlink", "directory", "bad-mode"))
def test_release_tag_lock_rejects_unsafe_existing_path_without_mutating_target(
    tmp_path: Path,
    unsafe_kind: str,
) -> None:
    project_root, _, environment, _, _ = _prepare_fake_lifecycle(tmp_path)
    lock_path = _release_tag_lock_path(environment)
    target = tmp_path / "lock-target"
    if unsafe_kind == "symlink":
        target.write_text("do-not-truncate\n", encoding="utf-8")
        lock_path.symlink_to(target)
    elif unsafe_kind == "directory":
        lock_path.mkdir()
    else:
        lock_path.write_text("do-not-change-mode\n", encoding="utf-8")
        lock_path.chmod(0o640)

    completed, _ = _run_prepared_lifecycle(project_root, environment, ":")

    assert completed.returncode != 0
    if unsafe_kind == "symlink":
        assert target.read_text(encoding="utf-8") == "do-not-truncate\n"
    elif unsafe_kind == "bad-mode":
        assert lock_path.read_text(encoding="utf-8") == "do-not-change-mode\n"
        assert stat.S_IMODE(lock_path.stat().st_mode) == 0o640


def test_release_tag_lock_preserves_existing_regular_file_contents(
    tmp_path: Path,
) -> None:
    project_root, _, environment, _, _ = _prepare_fake_lifecycle(tmp_path)
    lock_path = _release_tag_lock_path(environment)
    lock_path.write_text("persistent-lock-metadata\n", encoding="utf-8")
    lock_path.chmod(0o600)

    completed, _ = _run_prepared_lifecycle(project_root, environment, ":")

    assert completed.returncode == 0, completed.stderr
    assert lock_path.read_text(encoding="utf-8") == "persistent-lock-metadata\n"


def test_cross_sha_host_preflight_authorizes_only_running_compose_container_ports(
    tmp_path: Path,
) -> None:
    project_root, _, environment, _, profiles = _prepare_fake_lifecycle(
        tmp_path,
        initial_profiles=("gpu0",),
        include_baseline=False,
    )
    previous_root = _previous_release_root(environment)
    _write_maintenance_ledgers(previous_root)
    environment["PREVIOUS_RELEASE_ROOT"] = str(previous_root)
    state_path = Path(environment["FAKE_DOCKER_STATE"]) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    expected_endpoints = sorted(
        f"127.0.0.1:{port}"
        for container_id in profiles["gpu0"]
        for port in state["containers"][container_id]["published_ports"]
    )
    environment["EXPECTED_AUTHORIZED_ENDPOINTS"] = " ".join(expected_endpoints)
    stage_one = _extract_scenario_bash_block(
        "## 阶段 1：服务器预检、快照和暂停"
    )

    completed, _ = _run_prepared_lifecycle(project_root, environment, stage_one)

    assert completed.returncode == 0, completed.stderr
    authorized = (
        Path(environment["FAKE_DOCKER_STATE"]) / "authorized-endpoints.log"
    ).read_text(encoding="utf-8")
    assert authorized == environment["EXPECTED_AUTHORIZED_ENDPOINTS"]


def test_cross_sha_port_authority_scopes_shared_project_and_accepts_wildcard_dual_stack(
    tmp_path: Path,
) -> None:
    project_root, _, environment, _, _ = _prepare_fake_lifecycle(
        tmp_path,
        include_baseline=False,
    )
    previous_root = _previous_release_root(environment)
    _write_maintenance_ledgers(previous_root)
    environment["PREVIOUS_RELEASE_ROOT"] = str(previous_root)
    environment["EXPECTED_AUTHORIZED_ENDPOINTS"] = "0.0.0.0:18100 [::]:18100"
    state_path = Path(environment["FAKE_DOCKER_STATE"]) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    control_id = _container_id(4000)
    sibling_id = _container_id(4001)
    state["containers"][control_id] = {
        "project": "algorithm-scheduling-platform",
        "service": "control-service",
        "published_ports": [18100],
        "inspect_ports": {
            "18100/tcp": [
                {"HostIp": "0.0.0.0", "HostPort": "18100"},
                {"HostIp": "::", "HostPort": "18100"},
            ]
        },
    }
    state["containers"][sibling_id] = {
        "project": "algorithm-scheduling-platform",
        "service": "sibling-service",
        "published_ports": [19000],
    }
    state["compose_documents"]["docker-compose.platform.yml"]["services"] = {
        "control-service": {
            "ports": [
                {
                    "published": "18100",
                    "protocol": "tcp",
                    "target": 18100,
                }
            ]
        }
    }
    state["current"] = [control_id, sibling_id]
    state_path.write_text(json.dumps(state), encoding="utf-8")
    stage_one = _extract_scenario_bash_block(
        "## 阶段 1：服务器预检、快照和暂停"
    )

    completed, _ = _run_prepared_lifecycle(project_root, environment, stage_one)

    assert completed.returncode == 0, completed.stderr
    authorized = (
        Path(environment["FAKE_DOCKER_STATE"]) / "authorized-endpoints.log"
    ).read_text(encoding="utf-8")
    assert authorized == environment["EXPECTED_AUTHORIZED_ENDPOINTS"]
    inspect_calls = [call for call in _docker_calls(environment) if call[:1] == ["inspect"]]
    assert ["inspect", control_id] in inspect_calls
    assert all(sibling_id not in call for call in inspect_calls)


def test_cross_sha_host_preflight_rejects_non_authoritative_compose_container(
    tmp_path: Path,
) -> None:
    project_root, _, environment, _, profiles = _prepare_fake_lifecycle(
        tmp_path,
        include_baseline=False,
    )
    previous_root = _previous_release_root(environment)
    _write_maintenance_ledgers(previous_root)
    environment["PREVIOUS_RELEASE_ROOT"] = str(previous_root)
    state_path = Path(environment["FAKE_DOCKER_STATE"]) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    rogue_id = _container_id(3000)
    state["containers"][rogue_id] = {
        **state["containers"][profiles["gpu0"][0]],
        "project": "untrusted-project",
        "force_compose_ps": True,
    }
    state["current"] = [rogue_id]
    state_path.write_text(json.dumps(state), encoding="utf-8")
    stage_one = _extract_scenario_bash_block(
        "## 阶段 1：服务器预检、快照和暂停"
    )

    completed, _ = _run_prepared_lifecycle(project_root, environment, stage_one)

    assert completed.returncode != 0
    assert "Compose" in completed.stderr
    assert not (
        Path(environment["FAKE_DOCKER_STATE"]) / "authorized-endpoints.log"
    ).exists()


def test_cross_sha_host_preflight_still_rejects_extra_occupied_required_port(
    tmp_path: Path,
) -> None:
    project_root, _, environment, _, _ = _prepare_fake_lifecycle(
        tmp_path,
        initial_profiles=("gpu0",),
        include_baseline=False,
    )
    previous_root = _previous_release_root(environment)
    _write_maintenance_ledgers(previous_root)
    environment["PREVIOUS_RELEASE_ROOT"] = str(previous_root)
    environment["FAKE_UNAUTHORIZED_OCCUPIED_ENDPOINT"] = "127.0.0.2:18101"
    stage_one = _extract_scenario_bash_block(
        "## 阶段 1：服务器预检、快照和暂停"
    )

    completed, _ = _run_prepared_lifecycle(project_root, environment, stage_one)

    assert completed.returncode == 75
    authorized = (
        Path(environment["FAKE_DOCKER_STATE"]) / "authorized-endpoints.log"
    ).read_text(encoding="utf-8")
    assert "127.0.0.2:18101" not in authorized.split()


def test_fresh_host_preflight_does_not_authorize_occupied_ports(tmp_path: Path) -> None:
    project_root, _, environment, _, _ = _prepare_fake_lifecycle(
        tmp_path,
        include_baseline=False,
    )
    stage_one = _extract_scenario_bash_block(
        "## 阶段 1：服务器预检、快照和暂停"
    )

    completed, _ = _run_prepared_lifecycle(project_root, environment, stage_one)

    assert completed.returncode == 0, completed.stderr
    assert (
        Path(environment["FAKE_DOCKER_STATE"]) / "authorized-endpoints.log"
    ).read_text(encoding="utf-8") == ""


def test_inherited_ledger_refresh_tracks_compose_replacement_ids_without_removing(
    tmp_path: Path,
) -> None:
    project_root, release_root, environment, _, profiles = _prepare_fake_lifecycle(
        tmp_path,
        initial_profiles=("gpu0",),
        include_baseline=False,
        replace_profiles=("gpu0",),
    )
    previous_root = _previous_release_root(environment)
    _write_operator_ledgers(previous_root, [], sorted(profiles["gpu0"]))
    environment["PREVIOUS_RELEASE_ROOT"] = str(previous_root)
    stage_three = _extract_scenario_bash_block("阶段 3：平台和逐卡算子拓扑")

    completed, state = _run_prepared_lifecycle(project_root, environment, stage_three)

    assert completed.returncode == 0, completed.stderr
    replacements = state["replacements_on_up"]["gpu0"]
    expected_new = sorted(
        [*replacements.values()]
        + [
            container_id
            for profile in ("gpu1", "gpu2", "cpu")
            for container_id in profiles[profile]
        ]
    )
    assert _ledger_ids(release_root, "baseline-operator-container-ids.txt") == []
    assert _ledger_ids(release_root, "new-operator-container-ids.txt") == expected_new
    assert all(old_id not in state["current"] for old_id in replacements)
    assert not any(call and call[0] == "rm" for call in _docker_calls(environment))


def test_previous_active_pause_is_not_resnapshotted_and_restore_uses_authoritative_path(
    tmp_path: Path,
) -> None:
    project_root, release_root, environment, _, _ = _prepare_fake_lifecycle(tmp_path)
    previous_root = _previous_release_root(environment)
    previous_ledger_dir = previous_root / "container-maintenance"
    previous_ledger_dir.mkdir(parents=True, mode=0o700)
    previous_snapshot = previous_ledger_dir / "existing-containers.jsonl"
    previous_paused = previous_ledger_dir / "existing-containers.jsonl.paused.jsonl"
    previous_snapshot.write_text('{"name":"ocr-v6-amd"}\n', encoding="utf-8")
    active_pause = '{"name":"ocr-v6-amd","was_running":true}\n'
    previous_paused.write_text(active_pause, encoding="utf-8")
    environment["PREVIOUS_RELEASE_ROOT"] = str(previous_root)
    _write_operator_ledgers(release_root, [], [])
    stage_one = _extract_scenario_bash_block(
        "## 阶段 1：服务器预检、快照和暂停"
    )
    cleanup = _extract_scenario_bash_block(
        "## 阶段 6：反例、压力、恢复和报告渲染"
    )
    ledger_dir = release_root / "container-maintenance"
    cleanup_support = f"""
BASELINE_OPERATOR_IDS={shlex.quote(str(ledger_dir / 'baseline-operator-container-ids.txt'))}
NEW_OPERATOR_IDS={shlex.quote(str(ledger_dir / 'new-operator-container-ids.txt'))}
validate_operator_id_file() {{ return 0; }}
validate_operator_ledger_file() {{ return 0; }}
validate_operator_identity() {{ return 0; }}
assert_not_in_baseline() {{ return 0; }}
"""

    completed, _ = _run_prepared_lifecycle(
        project_root,
        environment,
        f"{stage_one}\n{cleanup_support}\n{cleanup}",
    )

    assert completed.returncode == 0, completed.stderr
    assert previous_paused.read_text(encoding="utf-8") == active_pause
    assert not (Path(environment["FAKE_DOCKER_STATE"]) / "maintenance.log").exists()
    restore_arguments = (
        Path(environment["FAKE_DOCKER_STATE"]) / "restore-arguments.log"
    ).read_text(encoding="utf-8").splitlines()
    assert restore_arguments == [str(previous_snapshot), str(previous_paused)]
    provenance = ledger_dir / "operator-maintenance-provenance.json"
    assert stat.S_IMODE(provenance.stat().st_mode) == 0o400
    assert json.loads(provenance.read_text(encoding="utf-8")) == {
        "authoritative_paused_ledger": str(previous_paused),
        "authoritative_snapshot": str(previous_snapshot),
        "source_git_sha": "b" * 40,
        "source_release_root": str(previous_root),
    }


def test_operator_profile_partial_up_publishes_difference_then_returns_original_status(
    tmp_path: Path,
) -> None:
    stage_three = _extract_scenario_bash_block("## 阶段 3：平台和逐卡算子拓扑")

    completed, release_root, _, baseline_id, profiles = _run_lifecycle_script(
        tmp_path,
        stage_three,
        up_status={"gpu0": 23},
    )

    assert completed.returncode == 23, completed.stderr
    assert _ledger_ids(
        release_root, "baseline-operator-container-ids.txt"
    ) == [baseline_id]
    assert _ledger_ids(release_root, "new-operator-container-ids.txt") == sorted(
        profiles["gpu0"]
    )


@pytest.mark.parametrize("mutation", ("inspect", "project", "service"))
def test_operator_profile_refresh_failure_preserves_published_ledgers_and_never_stops(
    tmp_path: Path,
    mutation: str,
) -> None:
    stage_three = _extract_scenario_bash_block("## 阶段 3：平台和逐卡算子拓扑")

    completed, release_root, _, baseline_id, _ = _run_lifecycle_script(
        tmp_path,
        stage_three,
        mutation=(mutation, "gpu0"),
    )

    assert completed.returncode != 0
    assert _ledger_ids(
        release_root, "baseline-operator-container-ids.txt"
    ) == [baseline_id]
    assert _ledger_ids(release_root, "new-operator-container-ids.txt") == []
    assert not (tmp_path / "docker-state/stops.log").exists()


@pytest.mark.parametrize(
    "failure_injection",
    (
        {"compose_ps_fail_on_call": 2},
        {"comm_fail_on_call": 1},
        {
            "mktemp_fail_pattern": ".new-operator-container-ids.",
            "mktemp_fail_on_matching_call": 2,
        },
    ),
    ids=("compose-ps", "comm", "mktemp"),
)
def test_refresh_tool_failure_preserves_ledgers_cleans_temps_and_never_stops(
    tmp_path: Path,
    failure_injection: dict[str, Any],
) -> None:
    stage_three = _extract_scenario_bash_block("## 阶段 3：平台和逐卡算子拓扑")

    completed, release_root, _, baseline_id, _ = _run_lifecycle_script(
        tmp_path,
        stage_three,
        failure_injection=failure_injection,
    )

    assert completed.returncode != 0
    assert _ledger_ids(
        release_root, "baseline-operator-container-ids.txt"
    ) == [baseline_id]
    assert _ledger_ids(release_root, "new-operator-container-ids.txt") == []
    assert not (tmp_path / "docker-state/stops.log").exists()
    ledger_dir = release_root / "container-maintenance"
    assert not list(ledger_dir.glob(".*-operator-container-ids.*"))


def test_operator_cleanup_stops_exact_valid_new_set_without_removing_containers(
    tmp_path: Path,
) -> None:
    stage_three = _extract_scenario_bash_block("## 阶段 3：平台和逐卡算子拓扑")
    cleanup = _extract_scenario_bash_block(
        "## 阶段 6：反例、压力、恢复和报告渲染"
    )

    completed, release_root, state, baseline_id, profiles = _run_lifecycle_script(
        tmp_path,
        f"{stage_three}\n{cleanup}",
    )

    assert completed.returncode == 0, completed.stderr
    expected_new = sorted(
        container_id
        for profile_ids in profiles.values()
        for container_id in profile_ids
    )
    assert _ledger_ids(release_root, "new-operator-container-ids.txt") == expected_new
    stops = (tmp_path / "docker-state/stops.log").read_text(
        encoding="utf-8"
    ).splitlines()
    assert stops == expected_new
    assert baseline_id not in stops
    assert set(state["current"]) == {baseline_id, *expected_new}
    calls = (tmp_path / "docker-state/calls.log").read_text(encoding="utf-8")
    assert '"rm"' not in calls


def test_noncanonical_docs_do_not_offer_direct_operator_profile_up_commands() -> None:
    direct_up = re.compile(
        r"(?m)^\s*docker\s+compose\s+-f\s+deploy/docker-compose\.operators\.yml\s+"
        r"--profile\s+\S+\s+up\s+-d\s*$"
    )
    for document in (
        DEPLOY / "README.md",
        PLATFORM_ROOT / "harness/verification.md",
        DEPLOY / "单机运维与恢复手册.md",
    ):
        content = document.read_text(encoding="utf-8")
        normalized = re.sub(r"\\\n\s*", " ", content)
        assert direct_up.search(normalized) is None, f"发现 direct-up 旁路: {document}"


def _heading_section(document: str, heading: str) -> str:
    tail = document.split(f"{heading}\n", maxsplit=1)[1]
    next_heading = re.search(r"(?m)^## ", tail)
    return tail if next_heading is None else tail[: next_heading.start()]


def _canonical_report_gate_bash(section: str) -> str:
    blocks = re.findall(r"```bash\n(.*?)\n```", section, re.DOTALL)
    matches = [
        block
        for block in blocks
        if "scripts/aggregate_milestone_2b_cases.py" in block
        and "scripts/render_milestone_2b_report.py" in block
    ]
    assert len(matches) == 1, "阶段 6 必须只有一个 canonical 聚合/渲染 Bash 段"
    return matches[0]


def _documented_command_tokens(block: str, script: str) -> list[str]:
    normalized = re.sub(r"\\\n[ \t]*", " ", block)
    matches = []
    for raw_line in normalized.splitlines():
        line = raw_line.strip()
        if line.startswith("if "):
            line = line.removeprefix("if ")
        if line.startswith(f".venv/bin/python {script} "):
            matches.append(shlex.split(line))
    assert len(matches) == 1, f"缺少唯一 canonical 命令: {script}"
    return matches[0]


def _assert_stage6_renderer_status_gate(gate: str, renderer: str) -> None:
    normalized = re.sub(r"\\\n[ \t]*", " ", gate)
    renderer_gate = re.search(
        rf"(?ms)^if (?P<command>\.venv/bin/python {re.escape(renderer)} [^\n]+)\n"
        r"then\n(?P<success>.*?)^else\n(?P<failure>.*?)^fi\n"
        r"(?P<before_case>.*?)^(?P<case>case \"\$report_status\" in\n.*\nesac)$",
        normalized,
    )
    assert renderer_gate is not None, "renderer 必须由 if/then/else/fi 捕获返回码"
    assert shlex.split(renderer_gate["command"]) == [
        ".venv/bin/python",
        renderer,
        "--input",
        "$RELEASE_ROOT/summary/cases.json",
        "--release-root",
        "$RELEASE_ROOT",
        "--output-json",
        "$RELEASE_ROOT/summary/report.json",
        "--output-markdown",
        "$RELEASE_ROOT/summary/report.md",
    ]
    assert renderer_gate["success"].strip() == "report_status=0"
    assert renderer_gate["failure"].strip() == "report_status=$?"
    assert renderer_gate["before_case"].strip() == "set -e"

    case_gate = re.fullmatch(
        r"(?ms)case \"\$report_status\" in\n"
        r"  0\)\n(?P<success>.*?)^    ;;\n"
        r"  3\)\n(?P<acceptance_failed>.*?)^    ;;\n"
        r"  \*\)\n(?P<error>.*?)^    ;;\n"
        r"esac",
        renderer_gate["case"],
    )
    assert case_gate is not None, "report_status 必须具有 0、3 和其他三个分支"

    success = case_gate["success"]
    assert (
        '.venv/bin/python - "$RELEASE_ROOT/summary/report.json" <<\'PY\''
        in success
    )
    assert 'print(json.load(stream)["overall_status"])' in success
    overall_gate = re.search(
        r'(?ms)^    if \[\[ "\$report_overall_status" != "通过" \]\]; then\n'
        r"(?P<failure>.*?)^    fi$",
        success,
    )
    assert overall_gate is not None, "0 分支必须只接受 overall_status=通过"
    assert "overall_status" in overall_gate["failure"]
    assert re.search(r"(?m)^      exit 1$", overall_gate["failure"])
    assert re.search(r"(?m)^    exit 3$", case_gate["acceptance_failed"])
    assert re.search(r'(?m)^    exit "\$report_status"$', case_gate["error"])


def test_stage6_docs_use_canonical_aggregation_and_renderer_gate() -> None:
    scenario = (
        PLATFORM_ROOT / "harness/scenarios/milestone-2b-deploy.md"
    ).read_text(encoding="utf-8")
    stage6 = _heading_section(
        scenario, "## 阶段 6：反例、压力、恢复和报告渲染"
    )
    gate = _canonical_report_gate_bash(stage6)
    aggregator = "scripts/aggregate_milestone_2b_cases.py"
    renderer = "scripts/render_milestone_2b_report.py"

    assert gate.index(aggregator) < gate.index(renderer)
    assert _documented_command_tokens(gate, aggregator) == [
        ".venv/bin/python",
        aggregator,
        "--release-root",
        "$RELEASE_ROOT",
        "--operator-compose",
        "deploy/docker-compose.operators.yml",
        "--smoke-manifest",
        "deploy/operator-smoke-cases.json",
        "--report-plan",
        "deploy/milestone-2b-report-plan.json",
        "--output",
        "$RELEASE_ROOT/summary/cases.json",
    ]
    _assert_stage6_renderer_status_gate(gate, renderer)
    assert "报告已生成但验收未通过" in gate
    assert "生成报告不等于验收通过" in stage6


def test_report_readme_locks_complete_canonical_evidence_inventory() -> None:
    readme = (DEPLOY / "reports/README.md").read_text(encoding="utf-8")
    compose = yaml.safe_load(
        (DEPLOY / "docker-compose.operators.yml").read_text(encoding="utf-8")
    )
    gpu_instances = sorted(
        service["environment"]["PLATFORM_INSTANCE_ID"]
        for service in compose["services"].values()
        if "PLATFORM_GPU_ID" in service["environment"]
    )
    assert len(gpu_instances) == 18
    facerec_instances = [
        instance for instance in gpu_instances if instance.startswith("facerec-")
    ]
    assert len(facerec_instances) == 3
    facerec_digest = hashlib.sha256(
        "\n".join(facerec_instances).encode("utf-8")
    ).hexdigest()[:12]
    smoke_manifest = json.loads(
        (DEPLOY / "operator-smoke-cases.json").read_text(encoding="utf-8")
    )
    operator_codes = sorted(case["operator_code"] for case in smoke_manifest["cases"])
    assert len(operator_codes) == 8

    canonical_paths = [
        "registration/operator-registration.json",
        *[
            f"registration/operator-registration-profile-{profile}.json"
            for profile in ("gpu0", "gpu1", "gpu2", "cpu")
        ],
        *[
            f"registration/operator-registration-instance-{instance}.json"
            for instance in gpu_instances
        ],
        f"registration/operator-registration-instances-{facerec_digest}.json",
        *[f"gpu-instances/{instance}.json" for instance in gpu_instances],
        *[f"recovery/{instance}-stopped.json" for instance in gpu_instances],
        "smoke/cases.json",
        *[f"smoke/{operator_code}.json" for operator_code in operator_codes],
        "smoke/instances/{instance_id}/runs/{run_id}/cases.json",
        "negative/cases.json",
        "load/cases.json",
        "summary/cases.json",
        "summary/report.json",
        "summary/report.md",
    ]
    for path in canonical_paths:
        assert path in readme, f"reports README 缺少 canonical 路径: {path}"

    for contract_text in (
        "0600",
        "权限",
        "write-once",
        "schema_version",
        "envelope",
        "243",
        "真实",
        "Mock",
        "overall_status",
        "SHA-256 证据摘要",
        "不输出证据原文",
    ):
        assert contract_text in readme, f"reports README 缺少合同说明: {contract_text}"


def test_historical_task16_uses_canonical_aggregation_and_renderer_parameters() -> None:
    plan = (
        WORKSPACE_ROOT
        / "docs/superpowers/plans/2026-08-12-里程碑2B三卡部署验证实施计划.md"
    ).read_text(encoding="utf-8")
    task16 = plan.split("### Task 16: 清理、恢复、验收和分支交付", maxsplit=1)[1]
    step4 = task16.split("- [ ] **Step 4: 汇总报告并更新事实文档**", maxsplit=1)[
        1
    ].split("- [ ] **Step 5:", maxsplit=1)[0]
    block = re.search(r"```bash\n(.*?)\n```", step4, re.DOTALL)
    assert block is not None
    commands = block.group(1)

    assert _documented_command_tokens(
        commands, "scripts/aggregate_milestone_2b_cases.py"
    ) == [
        ".venv/bin/python",
        "scripts/aggregate_milestone_2b_cases.py",
        "--release-root",
        "$RELEASE_ROOT",
        "--operator-compose",
        "deploy/docker-compose.operators.yml",
        "--smoke-manifest",
        "deploy/operator-smoke-cases.json",
        "--report-plan",
        "deploy/milestone-2b-report-plan.json",
        "--output",
        "$RELEASE_ROOT/summary/cases.json",
    ]
    assert _documented_command_tokens(
        commands, "scripts/render_milestone_2b_report.py"
    ) == [
        ".venv/bin/python",
        "scripts/render_milestone_2b_report.py",
        "--input",
        "$RELEASE_ROOT/summary/cases.json",
        "--release-root",
        "$RELEASE_ROOT",
        "--output-json",
        "$RELEASE_ROOT/summary/report.json",
        "--output-markdown",
        "$RELEASE_ROOT/summary/report.md",
    ]
    assert "--input deploy/reports/milestone-2b" not in task16
    assert "--output harness/reports/milestone-2b-summary.md" not in task16


def test_deploy_readme_invalidates_old_sha_images_and_evidence() -> None:
    readme = (DEPLOY / "README.md").read_text(encoding="utf-8")

    assert "新最终 SHA" in readme
    assert "四个平台和八个算子镜像" in readme
    assert "重新构建或重标" in readme
    assert "重新取证" in readme


def _fixture_manifest(tmp_path: Path) -> Path:
    source_root = tmp_path / "fixtures"
    source_root.mkdir(mode=0o700)
    fixtures: list[dict[str, Any]] = []
    for fixture_id, content, suffix in (
        ("asr_offline_audio", b"\x01\x00" * 1600, ".wav"),
        ("asr_online_audio", b"\x01\x00" * 1600, ".wav"),
        ("ocr_image", b"fake-jpeg", ".jpg"),
        ("vbas_image", b"fake-vbas", ".jpg"),
        ("facerec_image", b"fake-face", ".png"),
        ("screen_det_image", b"fake-screen", ".jpg"),
        ("ppt_video", b"fake-mp4-long-enough", ".mp4"),
    ):
        source = source_root / f"{fixture_id}{suffix}"
        if suffix == ".wav":
            with wave.open(str(source), "wb") as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(16000)
                stream.writeframes(content)
            content = source.read_bytes()
        else:
            source.write_bytes(content)
        fixtures.append(
            {
                "fixture_id": fixture_id,
                "source_kind": "external",
                "source": source.name,
                "server_target": str(tmp_path / "staged" / source.name),
                "bytes": len(content),
                "sha256": __import__("hashlib").sha256(content).hexdigest(),
            }
        )
    manifest = tmp_path / "fixtures.json"
    manifest.write_text(
        json.dumps({"schema_version": 1, "fixtures": fixtures, "missing_fixtures": []}),
        encoding="utf-8",
    )
    manifest.chmod(0o600)
    return manifest


class _WebSocketServer:
    def __init__(self) -> None:
        self.ready = threading.Event()
        self.stop = threading.Event()
        self.port = 0

        def run() -> None:
            import asyncio

            import websockets

            async def handler(socket: Any) -> None:
                chunk = await socket.recv()
                assert isinstance(chunk, bytes) and chunk
                await socket.send(json.dumps({"text": "实时测试文本", "finished": False}))

            async def serve() -> None:
                async with websockets.serve(handler, "127.0.0.1", 0) as server:
                    self.port = server.sockets[0].getsockname()[1]
                    self.ready.set()
                    while not self.stop.is_set():
                        await asyncio.sleep(0.01)

            asyncio.run(serve())

        self.thread = threading.Thread(target=run, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        assert self.ready.wait(3)
        return f"ws://127.0.0.1:{self.port}"

    def __exit__(self, *_: object) -> None:
        self.stop.set()
        self.thread.join(timeout=3)


def _smoke_handler(
    tmp_path: Path,
    *,
    ppt_callback: bool = True,
    fail_ocr: bool = False,
    wrong_face: bool = False,
    missing_managed_face: bool = False,
    cleanup_delete_fails: bool = False,
    cleanup_leaves_person: bool = False,
    vbas_failed: bool = False,
    screen_failed: bool = False,
    ppt_layout: str = "valid",
) -> Any:
    state: dict[str, Any] = {"created": False, "number": None}

    def handler(path: str, headers: Any, raw: bytes) -> tuple[int, Any]:
        for prefix in ("/b", "/c"):
            if path.startswith(prefix + "/") or path.startswith(prefix + "?"):
                path = path[len(prefix) :]
                break
        content_type = headers.get("Content-Type", "")
        if path == "/v1.1.8/seacraft_asr":
            assert "multipart/form-data" in content_type
            return 200, {"code": 0, "text": "完整敏感转写", "segments": [{"segment_text": "片段"}]}
        body = json.loads(raw or b"{}")
        if path == "/ocr/prediction":
            if fail_ocr:
                return 500, {"detail": "injected"}
            assert len(body["key"]) == len(body["value"]) == 1
            b64decode(body["value"][0])
            return 200, {
                "err_no": 0,
                "err_msg": "",
                "key": body["key"],
                "value": ['[{"text":"课程"}]'],
            }
        if path in {"/ImageDetect/student/v1.0.0", "/ImageDetect/teacher/v1.0.0"}:
            assert len(body["ImageList"]) == 1
            status_code = 500 if vbas_failed else 0
            return 200, {
                "StatusObject": {
                    "StatusCode": status_code,
                    "ImageIdList": ["smoke-frame"],
                },
                "DataList": [
                    {
                        "StatusObject": {
                            "StatusCode": status_code,
                            "ImageId": "smoke-frame",
                        },
                        "ResultList": [],
                    }
                ],
            }
        if path == "/detect_all":
            assert body["image"]
            failed = ["screen"] if screen_failed else []
            return 200, {
                "code": 200,
                "executed_modules": ["tilt", "screen", "quality_abnormal", "occlusion"],
                "failed_modules": failed,
                "problem_types": [],
                "tilt": {"code": 200, "result": {"is_tilted": False}},
                "screen": {"code": 500 if screen_failed else 200, "detections": []},
                "quality_abnormal": {"code": 200, "is_abnormal": False},
                "occlusion": {"code": 200, "is_occluded": False},
            }
        if path == "/persons":
            state["created"] = True
            state["number"] = body["number"]
            return 200, {"status_code": 200, "message": "created", "data": {"photo_path": ""}}
        if path == "/recognize":
            assert state["created"]
            return 200, {
                "status_code": 200,
                "message": "matched",
                "data": {
                    "match": [
                        {"number": "wrong-person" if wrong_face else body["targets"][0]}
                    ],
                    "embedding": [1, 2, 3],
                },
            }
        if path.startswith("/persons?"):
            managed_number = (
                state["number"]
                if state["created"] or cleanup_leaves_person
                else "existing-person"
            )
            return 200, {
                "status_code": 200,
                "message": "listed",
                "data": {
                    "persons": [
                        {
                            "number": (
                                "existing-person" if missing_managed_face else managed_number
                            )
                        }
                    ]
                },
            }
        if path == "/persons/delete":
            if cleanup_delete_fails:
                return 500, {"detail": "cleanup failed"}
            state["created"] = False
            return 200, {"status_code": 200, "message": "deleted", "data": {"deleted_count": 1}}
        if path == "/v1/extract_keywords":
            return 200, {"model": "fake", "result": {"keywords": ["课堂"]}}
        if path == "/v1/course_overviews":
            return 200, {"model": "fake", "result": {"overview": {"full_overview": "概览"}}}
        if path == "/LocalVideoPPTSliceTasks/v1.0.0":
            manifest = tmp_path / "result" / body["task_id"] / "ppt" / "manifest.json"
            if ppt_layout == "wrong_manifest":
                manifest = manifest.parent / "unexpected" / "manifest.json"
            image_dir = "unexpected" if ppt_layout == "wrong_image" else "slices"
            image = manifest.parent / image_dir / "ppt-0001-f17-t16s.jpg"
            image.parent.mkdir(parents=True, exist_ok=True)
            if ppt_layout == "symlink_image":
                target = image.with_name("real-ppt-0001-f17-t16s.jpg")
                target.write_bytes(b"jpeg")
                image.symlink_to(target.name)
            elif ppt_layout == "directory_image":
                image.mkdir()
            else:
                image.write_bytes(b"jpeg")
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "task_id": body["task_id"],
                        "operator_task_id": body["operator_task_id"],
                        "status": 60,
                        "path": str(image.parent),
                        "manifest_path": str(manifest),
                        "count": 1,
                        "images": [{"frame_seq": 17, "snap_time": 16, "path": str(image)}],
                        "dynamic_segments": [],
                    }
                ),
                encoding="utf-8",
            )
            if ppt_callback:
                callback_body = {
                    "task_id": body["task_id"],
                    "operator_task_id": body["operator_task_id"],
                    "status": 60,
                    "path": str(manifest.parent),
                    "manifest_path": str(manifest),
                    "count": 1,
                    "reason": "",
                    "dynamic_segments": [],
                }
                import urllib.request

                request = urllib.request.Request(
                    body["result_callback_uri"],
                    data=json.dumps(callback_body).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                urllib.request.urlopen(request, timeout=2).read()
            return 200, {
                "task_id": body["task_id"],
                "operator_task_id": body["operator_task_id"],
                "status": 50,
                "reason": "",
            }
        return 404, {"detail": path}

    return handler


def _run_smoke(
    tmp_path: Path,
    http_url: str,
    ws_url: str,
    manifest: Path,
    *,
    cases: str = "all",
    timeout: str = "2",
    face_endpoints: list[str] | None = None,
    callback_base: str | None = None,
    endpoints_as_file: bool = False,
    endpoint_overrides: dict[str, Any] | None = None,
    extra_arguments: tuple[str, ...] = (),
    environment: dict[str, str] | None = None,
    pass_fds: tuple[int, ...] = (),
) -> subprocess.CompletedProcess[str]:
    endpoints = {
        code: http_url
        for code in (
            "asr_offline",
            "ocr",
            "vbas",
            "facerec",
            "screen_det",
            "ppt_slice",
            "text_analysis",
        )
    }
    endpoints["asr_online"] = ws_url
    if callback_base is None:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("192.0.2.1", 9))
            callback_host = probe.getsockname()[0]
        finally:
            probe.close()
        callback_base = f"http://{callback_host}"
    if face_endpoints is not None:
        endpoints["facerec"] = face_endpoints
    if endpoint_overrides is not None:
        endpoints.update(endpoint_overrides)
    endpoints_argument = json.dumps(endpoints)
    if endpoints_as_file:
        endpoints_path = tmp_path / "endpoints.json"
        endpoints_path.write_text(endpoints_argument, encoding="utf-8")
        endpoints_argument = str(endpoints_path)
    return subprocess.run(
        [
            str(SCRIPTS / "run-operator-smoke"),
            "--release-tag",
            TAG,
            "--git-sha",
            SHA,
            "--reports-root",
            str(tmp_path / "reports"),
            "--fixture-manifest",
            str(manifest),
            "--external-fixture-root",
            str(manifest.parent / "fixtures"),
            "--fixture-target-root",
            str(tmp_path / "staged"),
            "--result-root",
            str(tmp_path / "result"),
            "--callback-advertise-base-url",
            callback_base,
            "--endpoints-json",
            endpoints_argument,
            "--cases",
            cases,
            "--timeout-seconds",
            timeout,
            "--mock",
            *extra_arguments,
        ],
        cwd=PLATFORM_ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
        env=environment,
        pass_fds=pass_fds,
    )


def _ppt_main_args(
    tmp_path: Path,
    http_url: str,
    manifest: Path,
    *,
    repeat: int,
) -> Any:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))
        callback_host = probe.getsockname()[0]
    finally:
        probe.close()
    return SMOKE_MODULE.argparse.Namespace(
        release_tag=TAG,
        git_sha=SHA,
        reports_root=tmp_path / "reports",
        fixture_manifest=manifest,
        external_fixture_root=manifest.parent / "fixtures",
        fixture_target_root=tmp_path / "staged",
        result_root=tmp_path / "result",
        callback_listen_host="0.0.0.0",
        callback_advertise_base_url=f"http://{callback_host}",
        endpoints_json=json.dumps({"ppt_slice": http_url}),
        cases="ppt_slice",
        operator=None,
        instance=None,
        run_id=None,
        repeat=repeat,
        hold_seconds=0.0,
        case_manifest=SMOKE_MODULE.DEFAULT_CASES,
        timeout_seconds=2.0,
        mock=True,
    )


def test_smoke_runner_supports_file_endpoint_and_append_only_instance_run(
    tmp_path: Path,
) -> None:
    manifest = _fixture_manifest(tmp_path)
    call_times: list[float] = []
    base_handler = _smoke_handler(tmp_path)

    def counted_handler(path: str, headers: Any, body: bytes) -> tuple[int, Any]:
        if path == "/ocr/prediction":
            call_times.append(time.monotonic())
        return base_handler(path, headers, body)

    with _WebSocketServer() as ws_url, _Server({}, counted_handler) as http_url:
        completed = _run_smoke(
            tmp_path,
            http_url,
            ws_url,
            manifest,
            endpoints_as_file=True,
            endpoint_overrides={"ocr": {"ocr-gpu0": http_url}},
            extra_arguments=(
                "--operator",
                "ocr",
                "--instance",
                "ocr-gpu0",
                "--run-id",
                "gpu0-ocr",
                "--repeat",
                "2",
                "--hold-seconds",
                "0.08",
            ),
        )
        run_root = (
            _release(tmp_path)
            / "smoke"
            / "instances"
            / "ocr-gpu0"
            / "runs"
            / "gpu0-ocr"
        )
        cases = json.loads((run_root / "cases.json").read_text(encoding="utf-8"))
        reproduction = shlex.split(cases[0]["command"])
        replay = subprocess.run(
            reproduction,
            cwd=PLATFORM_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    assert completed.returncode == 0, completed.stderr
    assert replay.returncode == 0, replay.stderr
    assert len(call_times) > 4
    assert call_times[-1] - call_times[0] >= 0.12
    report = json.loads((run_root / "ocr.json").read_text(encoding="utf-8"))
    assert report["target"] == "ocr-gpu0"
    assert report["summary"]["repeat"] == 2
    assert len(report["summary"]["attempts"]) > 2
    assert cases[0]["target"] == "ocr-gpu0"
    assert reproduction[reproduction.index("--endpoints-json") + 1] == str(
        tmp_path / "endpoints.json"
    )
    assert reproduction[reproduction.index("--run-id") + 1] == "auto"


def test_smoke_runner_emits_bound_activity_events_around_each_real_request(
    tmp_path: Path,
) -> None:
    manifest = _fixture_manifest(tmp_path)
    read_fd, write_fd = os.pipe()
    environment = os.environ.copy()
    environment.update(
        {
            "GPU_EVIDENCE_ACTIVITY_FD": str(write_fd),
            "GPU_EVIDENCE_ACTIVITY_NONCE": "verifier-generated-nonce",
        }
    )
    try:
        with _WebSocketServer() as ws_url, _Server({}, _smoke_handler(tmp_path)) as http_url:
            completed = _run_smoke(
                tmp_path,
                http_url,
                ws_url,
                manifest,
                cases="ocr",
                endpoint_overrides={"ocr": {"ocr-gpu0": http_url}},
                extra_arguments=(
                    "--operator",
                    "ocr",
                    "--instance",
                    "ocr-gpu0",
                    "--run-id",
                    "activity-run",
                    "--repeat",
                    "2",
                ),
                environment=environment,
                pass_fds=(write_fd,),
            )
    finally:
        os.close(write_fd)
    try:
        raw_events = b""
        while chunk := os.read(read_fd, 65536):
            raw_events += chunk
    finally:
        os.close(read_fd)

    assert completed.returncode == 0, completed.stderr
    events = [json.loads(line) for line in raw_events.splitlines()]
    assert [event["event"] for event in events] == ["start", "finish", "start", "finish"]
    for attempt, pair in enumerate((events[:2], events[2:]), start=1):
        assert all(
            event
            == {
                "event": event["event"],
                "nonce": "verifier-generated-nonce",
                "operator_code": "ocr",
                "instance_id": "ocr-gpu0",
                "run_id": "activity-run",
                "attempt": attempt,
                "target_origin": http_url,
            }
            for event in pair
        )


def test_facerec_activity_covers_only_target_recognize_and_pairs_on_failure(
    tmp_path: Path,
) -> None:
    image = tmp_path / "face.png"
    image.write_bytes(b"face-image")
    events: list[str] = []
    observations: list[tuple[str, str, str, bool]] = []
    active = False

    def activity(event: str) -> None:
        nonlocal active
        if event == "start":
            assert not active
            active = True
        else:
            assert event == "finish" and active
            active = False
        events.append(event)

    def handler(request: httpx.Request) -> httpx.Response:
        observations.append((request.method, request.url.host, request.url.path, active))
        if request.method == "POST" and request.url.path == "/persons":
            return httpx.Response(
                200,
                json={
                    "status_code": 200,
                    "message": "created",
                    "data": {"photo_path": ""},
                },
            )
        if request.method == "POST" and request.url.path == "/recognize":
            return httpx.Response(500, json={"detail": "injected recognition failure"})
        if request.method == "DELETE" and request.url.path == "/persons/delete":
            return httpx.Response(
                200,
                json={
                    "status_code": 200,
                    "message": "deleted",
                    "data": {"deleted_count": 1},
                },
            )
        if request.method == "GET" and request.url.path == "/persons":
            return httpx.Response(
                200,
                json={
                    "status_code": 200,
                    "message": "listed",
                    "data": {"persons": []},
                },
            )
        raise AssertionError(f"unexpected FaceRec request: {request.method} {request.url}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(RuntimeError, match="FaceRec recognize HTTP 500"):
            SMOKE_MODULE.smoke_facerec(
                http,
                json.dumps(
                    ["http://create.test", "http://recognize.test", "http://manage.test"]
                ),
                {"facerec_image": image},
                1.0,
                activity=activity,
            )

    assert events == ["start", "finish"]
    assert observations == [
        ("POST", "create.test", "/persons", False),
        ("POST", "recognize.test", "/recognize", True),
        ("DELETE", "manage.test", "/persons/delete", False),
        ("GET", "manage.test", "/persons", False),
    ]
    assert not active


def test_facerec_activity_reports_selected_recognize_instance_origin(
    tmp_path: Path,
) -> None:
    manifest = _fixture_manifest(tmp_path)
    read_fd, write_fd = os.pipe()
    environment = os.environ.copy()
    environment.update(
        {
            "GPU_EVIDENCE_ACTIVITY_FD": str(write_fd),
            "GPU_EVIDENCE_ACTIVITY_NONCE": "verifier-generated-nonce",
        }
    )
    handler = _smoke_handler(tmp_path)
    try:
        with ExitStack() as stack:
            ws_url = stack.enter_context(_WebSocketServer())
            face_urls = [stack.enter_context(_Server({}, handler)) for _ in range(3)]
            completed = _run_smoke(
                tmp_path,
                face_urls[0],
                ws_url,
                manifest,
                cases="facerec",
                endpoint_overrides={
                    "facerec": {
                        f"facerec-gpu{index}": url
                        for index, url in enumerate(face_urls)
                    }
                },
                extra_arguments=(
                    "--operator",
                    "facerec",
                    "--instance",
                    "facerec-gpu1",
                    "--run-id",
                    "facerec-origin-run",
                ),
                environment=environment,
                pass_fds=(write_fd,),
            )
    finally:
        os.close(write_fd)
    try:
        raw_events = b""
        while chunk := os.read(read_fd, 65536):
            raw_events += chunk
    finally:
        os.close(read_fd)

    assert completed.returncode == 0, completed.stderr
    events = [json.loads(line) for line in raw_events.splitlines()]
    assert [event["event"] for event in events] == ["start", "finish"]
    assert {event["target_origin"] for event in events} == {face_urls[1]}


def test_asr_online_activity_starts_with_first_send_after_preparation_and_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio = tmp_path / "online.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\x01\x00" * 1600)

    events: list[str] = []
    observations: dict[str, bool] = {}
    active = False

    def activity(event: str) -> None:
        nonlocal active
        if event == "start":
            assert not active
            active = True
        else:
            assert event == "finish" and active
            active = False
        events.append(event)

    real_wave_open = wave.open

    def observed_wave_open(*args: Any, **kwargs: Any) -> Any:
        observations["wav_read"] = active
        return real_wave_open(*args, **kwargs)

    class Socket:
        async def send(self, _: bytes) -> None:
            observations["first_send"] = active
            raise RuntimeError("injected send failure")

        async def recv(self) -> str:
            raise AssertionError("recv must not run after the injected send failure")

    class Connection:
        async def __aenter__(self) -> Socket:
            observations["connect"] = active
            return Socket()

        async def __aexit__(self, *_: object) -> None:
            observations["close"] = active

    monkeypatch.setattr(SMOKE_MODULE.wave, "open", observed_wave_open)
    monkeypatch.setattr(SMOKE_MODULE.websockets, "connect", lambda *_, **__: Connection())

    with pytest.raises(RuntimeError, match="injected send failure"):
        SMOKE_MODULE.smoke_asr_online(
            object(),
            "ws://asr-online.test",
            {"asr_online_audio": audio},
            1.0,
            activity=activity,
        )

    assert observations == {
        "wav_read": False,
        "connect": False,
        "first_send": True,
        "close": False,
    }
    assert events == ["start", "finish"]
    assert not active


@pytest.mark.parametrize(
    ("arguments", "expected"),
    (
        (("--repeat", "0"), "repeat 必须大于 0"),
        (("--hold-seconds", "-0.1"), "hold-seconds 不能为负数"),
    ),
)
def test_smoke_runner_rejects_invalid_repeat_and_hold_with_specific_reason(
    tmp_path: Path,
    arguments: tuple[str, ...],
    expected: str,
) -> None:
    manifest = _fixture_manifest(tmp_path)
    with _WebSocketServer() as ws_url, _Server({}, _smoke_handler(tmp_path)) as http_url:
        completed = _run_smoke(
            tmp_path,
            http_url,
            ws_url,
            manifest,
            cases="ocr",
            extra_arguments=arguments,
        )

    assert completed.returncode != 0
    assert expected in completed.stderr


def test_instance_smoke_failure_evidence_keeps_the_selected_instance_target(
    tmp_path: Path,
) -> None:
    manifest = _fixture_manifest(tmp_path)
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["fixtures"] = [
        item for item in document["fixtures"] if item["fixture_id"] != "ocr_image"
    ]
    document["missing_fixtures"].append(
        {"fixture_id": "ocr_image", "reason": "fixture unavailable"}
    )
    manifest.write_text(json.dumps(document), encoding="utf-8")

    with _WebSocketServer() as ws_url, _Server({}, _smoke_handler(tmp_path)) as http_url:
        completed = _run_smoke(
            tmp_path,
            http_url,
            ws_url,
            manifest,
            endpoint_overrides={"ocr": {"ocr-gpu0": http_url}},
            extra_arguments=(
                "--operator",
                "ocr",
                "--instance",
                "ocr-gpu0",
                "--run-id",
                "missing-fixture",
            ),
        )

    assert completed.returncode != 0
    run_root = (
        _release(tmp_path)
        / "smoke/instances/ocr-gpu0/runs/missing-fixture"
    )
    evidence = json.loads((run_root / "ocr.json").read_text(encoding="utf-8"))
    cases = json.loads((run_root / "cases.json").read_text(encoding="utf-8"))
    assert evidence["target"] == "ocr-gpu0"
    assert cases[0]["target"] == "ocr-gpu0"


def test_instance_smoke_rejects_instance_id_from_another_operator(
    tmp_path: Path,
) -> None:
    manifest = _fixture_manifest(tmp_path)
    with _WebSocketServer() as ws_url, _Server({}, _smoke_handler(tmp_path)) as http_url:
        completed = _run_smoke(
            tmp_path,
            http_url,
            ws_url,
            manifest,
            endpoint_overrides={"ocr": {"asr-offline-gpu0": http_url}},
            extra_arguments=(
                "--operator",
                "ocr",
                "--instance",
                "asr-offline-gpu0",
                "--run-id",
                "wrong-instance",
            ),
        )

    assert completed.returncode != 0
    assert "实例 ID 与算子不匹配" in completed.stderr


def test_smoke_runner_calls_all_eight_operator_contracts_and_redacts(tmp_path: Path) -> None:
    manifest = _fixture_manifest(tmp_path)
    handler = _smoke_handler(tmp_path)
    with _WebSocketServer() as ws_url, _face_servers(handler) as face_endpoints:
        http_url = face_endpoints[0]
        completed = _run_smoke(
            tmp_path,
            http_url,
            ws_url,
            manifest,
            face_endpoints=face_endpoints,
        )

    assert completed.returncode == 0, completed.stderr
    smoke_dir = _release(tmp_path) / "smoke"
    cases = json.loads((smoke_dir / "cases.json").read_text(encoding="utf-8"))
    assert len(cases) == 8
    assert all(case["status"] == "通过" and case["mock"] for case in cases)
    combined = "".join(path.read_text(encoding="utf-8") for path in smoke_dir.glob("*.json"))
    assert "完整敏感转写" not in combined
    assert "embedding" not in combined
    assert "base64" not in combined.lower()
    assert "harness-person" not in combined
    for case in cases:
        command = case["command"]
        assert "--release-tag" in command and TAG in command
        assert "--git-sha" in command and SHA in command
        assert "--reports-root" in command
        assert "--fixture-manifest" in command
        assert "--external-fixture-root" in command
        assert "--fixture-target-root" in command
        assert "--result-root" in command
        assert "--endpoints-json" in command
        assert "--callback-advertise-base-url" in command


def test_ppt_callback_url_is_not_container_loopback_and_manifest_uses_images(
    tmp_path: Path,
) -> None:
    manifest = _fixture_manifest(tmp_path)
    callback_urls: list[str] = []
    handler = _smoke_handler(tmp_path)

    def inspect(path: str, headers: Any, body: bytes) -> tuple[int, Any]:
        if path == "/LocalVideoPPTSliceTasks/v1.0.0":
            callback = json.loads(body)["result_callback_uri"]
            callback_urls.append(callback)
            assert "127.0.0.1" not in callback and "0.0.0.0" not in callback
        return handler(path, headers, body)

    with _WebSocketServer() as ws_url, _Server({}, inspect) as http_url:
        completed = _run_smoke(tmp_path, http_url, ws_url, manifest, cases="ppt_slice")
    assert completed.returncode == 0, completed.stderr
    assert callback_urls and callback_urls[0].startswith("http://")


def test_ppt_smoke_isolates_each_attempt_and_records_the_task_id(tmp_path: Path) -> None:
    manifest = _fixture_manifest(tmp_path)
    submitted_task_ids: list[str] = []
    handler = _smoke_handler(tmp_path)

    def inspect(path: str, headers: Any, body: bytes) -> tuple[int, Any]:
        if path == "/LocalVideoPPTSliceTasks/v1.0.0":
            submitted_task_ids.append(json.loads(body)["task_id"])
        return handler(path, headers, body)

    with _WebSocketServer() as ws_url, _Server({}, inspect) as http_url:
        completed = _run_smoke(
            tmp_path,
            http_url,
            ws_url,
            manifest,
            cases="ppt_slice",
            extra_arguments=("--repeat", "2"),
        )

    assert completed.returncode == 0, completed.stderr
    assert len(submitted_task_ids) == 2
    assert len(set(submitted_task_ids)) == 2
    assert all(re.fullmatch(r"harness-ppt-[0-9a-f]{32}", value) for value in submitted_task_ids)
    evidence = json.loads((_release(tmp_path) / "smoke/ppt_slice.json").read_text(encoding="utf-8"))
    attempts = evidence["summary"]["attempts"]
    assert [attempt["task_id"] for attempt in attempts] == submitted_task_ids
    assert evidence["summary"]["task_id"] == submitted_task_ids[-1]
    for task_id in submitted_task_ids:
        assert (tmp_path / "result" / task_id / "ppt" / "manifest.json").is_file()


@pytest.mark.parametrize(
    ("ppt_layout", "expected_reason"),
    (
        ("wrong_manifest", "PPT manifest 不在当前 Smoke 任务的精确位置"),
        ("wrong_image", "PPT 切片图片越出当前 Smoke 任务的 slices 目录"),
    ),
)
def test_ppt_smoke_rejects_artifacts_outside_the_current_task_scope(
    tmp_path: Path,
    ppt_layout: str,
    expected_reason: str,
) -> None:
    manifest = _fixture_manifest(tmp_path)
    with _WebSocketServer() as ws_url, _Server(
        {}, _smoke_handler(tmp_path, ppt_layout=ppt_layout)
    ) as http_url:
        completed = _run_smoke(tmp_path, http_url, ws_url, manifest, cases="ppt_slice")

    assert completed.returncode != 0
    assert expected_reason in completed.stderr


@pytest.mark.parametrize("ppt_layout", ("symlink_image", "directory_image"))
def test_ppt_smoke_rejects_symlink_and_non_regular_slice_images(
    tmp_path: Path,
    ppt_layout: str,
) -> None:
    manifest = _fixture_manifest(tmp_path)
    submitted: list[dict[str, Any]] = []
    handler = _smoke_handler(tmp_path, ppt_layout=ppt_layout)

    def inspect(path: str, headers: Any, body: bytes) -> tuple[int, Any]:
        if path == "/LocalVideoPPTSliceTasks/v1.0.0":
            submitted.append(json.loads(body))
        return handler(path, headers, body)

    with _WebSocketServer() as ws_url, _Server(
        {}, inspect
    ) as http_url:
        completed = _run_smoke(tmp_path, http_url, ws_url, manifest, cases="ppt_slice")

    assert completed.returncode != 0
    evidence = json.loads((_release(tmp_path) / "smoke/ppt_slice.json").read_text())
    assert evidence["status"] == "失败"
    _assert_ppt_failure_context(evidence, submitted)


def test_ppt_pre_submit_failure_after_success_does_not_duplicate_previous_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _fixture_manifest(tmp_path)
    submitted: list[dict[str, Any]] = []
    handler = _smoke_handler(tmp_path)
    generated = iter((uuid.UUID(int=1), uuid.UUID(int=2)))

    def fail_before_second_submission() -> uuid.UUID:
        try:
            return next(generated)
        except StopIteration as error:
            raise RuntimeError("injected pre-submit failure") from error

    def inspect(path: str, headers: Any, body: bytes) -> tuple[int, Any]:
        if path == "/LocalVideoPPTSliceTasks/v1.0.0":
            submitted.append(json.loads(body))
        return handler(path, headers, body)

    with _Server({}, inspect) as http_url:
        monkeypatch.setattr(
            SMOKE_MODULE,
            "parse_args",
            lambda: _ppt_main_args(tmp_path, http_url, manifest, repeat=2),
        )
        monkeypatch.setattr(SMOKE_MODULE.uuid, "uuid4", fail_before_second_submission)
        completed = SMOKE_MODULE.main()

    assert completed == 1
    assert len(submitted) == 1
    evidence = json.loads((_release(tmp_path) / "smoke/ppt_slice.json").read_text())
    assert "injected pre-submit failure" in evidence["reason"]
    assert evidence["summary"]["attempt_count"] == 1
    assert len(evidence["summary"]["attempts"]) == 1
    assert evidence["summary"]["attempts"][0]["task_id"] == submitted[0]["task_id"]


def test_ppt_request_build_failure_after_success_is_not_recorded_as_submitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _fixture_manifest(tmp_path)
    submitted: list[dict[str, Any]] = []
    handler = _smoke_handler(tmp_path)
    real_build_request = httpx.Client.build_request
    build_count = 0

    def fail_second_request_build(
        client: httpx.Client,
        method: str,
        url: httpx.URL | str,
        **kwargs: Any,
    ) -> httpx.Request:
        nonlocal build_count
        build_count += 1
        if build_count == 2:
            raise RuntimeError("injected request build failure")
        return real_build_request(client, method, url, **kwargs)

    def inspect(path: str, headers: Any, body: bytes) -> tuple[int, Any]:
        if path == "/LocalVideoPPTSliceTasks/v1.0.0":
            submitted.append(json.loads(body))
        return handler(path, headers, body)

    with _Server({}, inspect) as http_url:
        monkeypatch.setattr(
            SMOKE_MODULE,
            "parse_args",
            lambda: _ppt_main_args(tmp_path, http_url, manifest, repeat=2),
        )
        monkeypatch.setattr(httpx.Client, "build_request", fail_second_request_build)
        completed = SMOKE_MODULE.main()

    assert completed == 1
    assert build_count == 2
    assert len(submitted) == 1
    evidence = json.loads((_release(tmp_path) / "smoke/ppt_slice.json").read_text())
    assert "injected request build failure" in evidence["reason"]
    assert evidence["summary"]["attempt_count"] == 1
    assert len(evidence["summary"]["attempts"]) == 1
    assert evidence["summary"]["attempts"][0]["task_id"] == submitted[0]["task_id"]


def test_ppt_send_failure_after_success_is_recorded_as_submitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _fixture_manifest(tmp_path)
    submitted: list[dict[str, Any]] = []
    sent: list[dict[str, Any]] = []
    handler = _smoke_handler(tmp_path)
    real_send = httpx.Client.send

    def fail_second_send(
        client: httpx.Client,
        request: httpx.Request,
        *args: Any,
        **kwargs: Any,
    ) -> httpx.Response:
        sent.append(json.loads(request.content))
        if len(sent) == 2:
            raise httpx.ConnectError("injected send failure", request=request)
        return real_send(client, request, *args, **kwargs)

    def inspect(path: str, headers: Any, body: bytes) -> tuple[int, Any]:
        if path == "/LocalVideoPPTSliceTasks/v1.0.0":
            submitted.append(json.loads(body))
        return handler(path, headers, body)

    with _Server({}, inspect) as http_url:
        monkeypatch.setattr(
            SMOKE_MODULE,
            "parse_args",
            lambda: _ppt_main_args(tmp_path, http_url, manifest, repeat=2),
        )
        monkeypatch.setattr(httpx.Client, "send", fail_second_send)
        completed = SMOKE_MODULE.main()

    assert completed == 1
    assert len(submitted) == 1
    assert len(sent) == 2
    evidence = json.loads((_release(tmp_path) / "smoke/ppt_slice.json").read_text())
    assert "injected send failure" in evidence["reason"]
    attempts = evidence["summary"]["attempts"]
    assert evidence["summary"]["attempt_count"] == 2
    assert [attempt["task_id"] for attempt in attempts] == [request["task_id"] for request in sent]
    assert len({attempt["task_id"] for attempt in attempts}) == 2
    assert attempts[1]["status"] == "失败"


def test_ppt_success_evidence_write_failure_does_not_create_a_failed_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _fixture_manifest(tmp_path)
    submitted: list[dict[str, Any]] = []
    handler = _smoke_handler(tmp_path)
    real_atomic_json = SMOKE_MODULE.atomic_json

    def fail_pass_evidence(path: Path, payload: Any) -> None:
        if isinstance(payload, dict) and payload.get("status") == "PASS":
            raise RuntimeError("injected PASS evidence failure")
        real_atomic_json(path, payload)

    def inspect(path: str, headers: Any, body: bytes) -> tuple[int, Any]:
        if path == "/LocalVideoPPTSliceTasks/v1.0.0":
            submitted.append(json.loads(body))
        return handler(path, headers, body)

    with _Server({}, inspect) as http_url:
        monkeypatch.setattr(
            SMOKE_MODULE,
            "parse_args",
            lambda: _ppt_main_args(tmp_path, http_url, manifest, repeat=1),
        )
        monkeypatch.setattr(SMOKE_MODULE, "atomic_json", fail_pass_evidence)
        completed = SMOKE_MODULE.main()

    assert completed == 1
    assert len(submitted) == 1
    evidence = json.loads((_release(tmp_path) / "smoke/ppt_slice.json").read_text())
    assert "injected PASS evidence failure" in evidence["reason"]
    assert evidence["summary"]["attempt_count"] == 1
    assert len(evidence["summary"]["attempts"]) == 1
    assert evidence["summary"]["attempts"][0].get("status") != "失败"


def test_ppt_second_submitted_request_failure_keeps_both_actual_attempt_ids(
    tmp_path: Path,
) -> None:
    manifest = _fixture_manifest(tmp_path)
    submitted: list[dict[str, Any]] = []
    handler = _smoke_handler(tmp_path)

    def fail_second_request(path: str, headers: Any, body: bytes) -> tuple[int, Any]:
        if path == "/LocalVideoPPTSliceTasks/v1.0.0":
            submitted.append(json.loads(body))
            if len(submitted) == 2:
                return 503, {"detail": "injected operator failure"}
        return handler(path, headers, body)

    with _WebSocketServer() as ws_url, _Server({}, fail_second_request) as http_url:
        completed = _run_smoke(
            tmp_path,
            http_url,
            ws_url,
            manifest,
            cases="ppt_slice",
            extra_arguments=("--repeat", "2"),
        )

    assert completed.returncode != 0
    assert len(submitted) == 2
    evidence = json.loads((_release(tmp_path) / "smoke/ppt_slice.json").read_text())
    attempts = evidence["summary"]["attempts"]
    assert evidence["summary"]["attempt_count"] == 2
    assert [attempt["task_id"] for attempt in attempts] == [
        request["task_id"] for request in submitted
    ]
    assert len({attempt["task_id"] for attempt in attempts}) == 2
    assert attempts[1]["status"] == "失败"


def test_text_analysis_smoke_uses_the_current_course_overview_contract(
    tmp_path: Path,
) -> None:
    manifest = _fixture_manifest(tmp_path)
    overview_requests: list[dict[str, Any]] = []
    handler = _smoke_handler(tmp_path)

    def inspect(path: str, headers: Any, body: bytes) -> tuple[int, Any]:
        if path == "/v1/course_overviews":
            overview_requests.append(json.loads(body))
        return handler(path, headers, body)

    with _WebSocketServer() as ws_url, _Server({}, inspect) as http_url:
        completed = _run_smoke(
            tmp_path,
            http_url,
            ws_url,
            manifest,
            cases="text_analysis",
        )

    assert completed.returncode == 0, completed.stderr
    assert overview_requests == [
        {"textSegments": [{"text": "函数课程", "bg": 0, "ed": 10}]}
    ]


def test_facerec_requires_three_distinct_instances_and_exact_created_match(
    tmp_path: Path,
) -> None:
    manifest = _fixture_manifest(tmp_path)
    with _WebSocketServer() as ws_url, _Server({}, _smoke_handler(tmp_path)) as http_url:
        duplicate = _run_smoke(
            tmp_path,
            http_url,
            ws_url,
            manifest,
            cases="facerec",
            face_endpoints=[http_url, http_url, http_url],
        )
    assert duplicate.returncode != 0
    assert "三个不同" in duplicate.stderr

    other = tmp_path / "wrong"
    other.mkdir()
    wrong_manifest = _fixture_manifest(other)
    with _WebSocketServer() as ws_url, _face_servers(
        _smoke_handler(other, wrong_face=True)
    ) as face_endpoints:
        http_url = face_endpoints[0]
        wrong = _run_smoke(
            other,
            http_url,
            ws_url,
            wrong_manifest,
            cases="facerec",
            face_endpoints=face_endpoints,
        )
    assert wrong.returncode != 0
    assert "刚创建" in wrong.stderr

    missing = tmp_path / "missing-managed-face"
    missing.mkdir()
    missing_manifest = _fixture_manifest(missing)
    with _WebSocketServer() as ws_url, _face_servers(
        _smoke_handler(missing, missing_managed_face=True)
    ) as face_endpoints:
        http_url = face_endpoints[0]
        absent = _run_smoke(
            missing,
            http_url,
            ws_url,
            missing_manifest,
            cases="facerec",
            face_endpoints=face_endpoints,
        )
    assert absent.returncode != 0
    assert "实例 C" in absent.stderr and "刚创建" in absent.stderr


@pytest.mark.parametrize(
    "endpoints",
    (
        [
            "http://face.example:8003",
            "http://face.example:8003/",
            "http://face.example:8003//",
        ],
        [
            "http://FACE.EXAMPLE",
            "http://face.example:80/",
            "http://Face.Example//",
        ],
    ),
)
def test_facerec_resolve_endpoint_rejects_same_normalized_origin(
    endpoints: list[str],
) -> None:
    configured = {
        f"facerec-gpu{index}": endpoint for index, endpoint in enumerate(endpoints)
    }

    with pytest.raises(ValueError, match="三个不同实例"):
        SMOKE_MODULE.resolve_endpoint(
            {"facerec": configured}, "facerec", "facerec-gpu0"
        )


@pytest.mark.parametrize(
    "endpoints",
    (
        [
            "http://face.example:8003",
            "http://face.example:8003/",
            "http://face.example:8003//",
        ],
        [
            "http://FACE.EXAMPLE",
            "http://face.example:80/",
            "http://Face.Example//",
        ],
    ),
)
def test_facerec_runner_rejects_same_normalized_origin(endpoints: list[str]) -> None:
    with pytest.raises(RuntimeError, match="三个不同实例"):
        SMOKE_MODULE.smoke_facerec(object(), json.dumps(endpoints), {}, 1)


def test_facerec_resolve_endpoint_accepts_three_distinct_ports() -> None:
    configured = {
        f"facerec-gpu{index}": f"http://face.example:{8003 + index}/"
        for index in range(3)
    }

    endpoints, target = SMOKE_MODULE.resolve_endpoint(
        {"facerec": configured}, "facerec", "facerec-gpu1"
    )

    assert target == "facerec-gpu1"
    assert {SMOKE_MODULE.normalized_http_origin(item) for item in endpoints} == {
        ("http", "face.example", 8003),
        ("http", "face.example", 8004),
        ("http", "face.example", 8005),
    }


@pytest.mark.parametrize(
    "callback_base",
    ("https://192.168.29.11", "http://user:password@192.168.29.11"),
)
def test_ppt_callback_advertise_rejects_non_http_or_userinfo(
    tmp_path: Path, callback_base: str
) -> None:
    manifest = _fixture_manifest(tmp_path)
    with _WebSocketServer() as ws_url, _Server({}, _smoke_handler(tmp_path)) as http_url:
        completed = _run_smoke(
            tmp_path,
            http_url,
            ws_url,
            manifest,
            cases="ppt_slice",
            callback_base=callback_base,
        )
    assert completed.returncode != 0
    assert "callback advertise" in completed.stderr
    assert "password" not in completed.stderr


def test_callback_capture_binds_explicit_advertise_port_and_receives_post(
    unused_tcp_port: int,
) -> None:
    payload = {"operator_task_id": "ppt-callback-test", "status": 60}
    advertise_base_url = f"http://192.168.29.11:{unused_tcp_port}"

    with SMOKE_MODULE.CallbackCapture(
        listen_host="127.0.0.1",
        advertise_base_url=advertise_base_url,
    ) as callback:
        assert callback.server.server_address[1] == unused_tcp_port
        assert callback.url == f"{advertise_base_url}/terminal"
        request = urllib.request.Request(
            f"http://127.0.0.1:{unused_tcp_port}/terminal",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            assert response.status == 204
        assert callback.event.wait(1)
        assert callback.payload == payload


@pytest.mark.parametrize(
    ("handler_kwargs", "expected"),
    (
        ({"cleanup_delete_fails": True}, "cleanup HTTP 500"),
        ({"cleanup_leaves_person": True}, "清理后仍存在"),
        (
            {"wrong_face": True, "cleanup_delete_fails": True},
            "未精确匹配",
        ),
    ),
)
def test_facerec_cleanup_failure_is_reported(
    tmp_path: Path, handler_kwargs: dict[str, bool], expected: str
) -> None:
    manifest = _fixture_manifest(tmp_path)
    with _WebSocketServer() as ws_url, _face_servers(
        _smoke_handler(tmp_path, **handler_kwargs)
    ) as face_endpoints:
        http_url = face_endpoints[0]
        completed = _run_smoke(
            tmp_path,
            http_url,
            ws_url,
            manifest,
            cases="facerec",
            face_endpoints=face_endpoints,
        )
    assert completed.returncode != 0
    assert expected in completed.stderr
    if handler_kwargs.get("wrong_face"):
        assert "cleanup HTTP 500" in completed.stderr
    assert "base64" not in completed.stderr.lower()


@pytest.mark.parametrize(
    ("case", "kwargs", "expected"),
    (
        ("vbas", {"vbas_failed": True}, "StatusCode"),
        ("screen_det", {"screen_failed": True}, "failed_modules"),
    ),
)
def test_visual_smoke_rejects_business_level_failure(
    tmp_path: Path, case: str, kwargs: dict[str, bool], expected: str
) -> None:
    manifest = _fixture_manifest(tmp_path)
    with _WebSocketServer() as ws_url, _Server(
        {}, _smoke_handler(tmp_path, **kwargs)
    ) as http_url:
        completed = _run_smoke(tmp_path, http_url, ws_url, manifest, cases=case)
    assert completed.returncode != 0
    assert expected in completed.stderr


def test_smoke_runner_rejects_fixture_hash_mismatch(tmp_path: Path) -> None:
    manifest = _fixture_manifest(tmp_path)
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["fixtures"][0]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(document), encoding="utf-8")
    with _WebSocketServer() as ws_url, _Server({}, _smoke_handler(tmp_path)) as http_url:
        completed = _run_smoke(tmp_path, http_url, ws_url, manifest, cases="asr_offline")
    assert completed.returncode != 0
    assert "SHA-256" in completed.stderr


def test_ocr_only_smoke_does_not_access_or_stage_unselected_ppt_fixture(
    tmp_path: Path,
) -> None:
    manifest = _fixture_manifest(tmp_path)
    document = json.loads(manifest.read_text(encoding="utf-8"))
    ppt = next(item for item in document["fixtures"] if item["fixture_id"] == "ppt_video")
    (manifest.parent / "fixtures" / ppt["source"]).unlink()

    with _WebSocketServer() as ws_url, _Server({}, _smoke_handler(tmp_path)) as http_url:
        completed = _run_smoke(tmp_path, http_url, ws_url, manifest, cases="ocr")

    assert completed.returncode == 0, completed.stderr
    assert {path.name for path in (tmp_path / "staged").iterdir()} == {"ocr_image.jpg"}
    assert not Path(ppt["server_target"]).exists()


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    (
        ("bytes", True, "fixture bytes"),
        ("bytes", -1, "fixture bytes"),
        ("bytes", "10", "fixture bytes"),
        ("sha256", "0" * 63, "fixture sha256"),
        ("sha256", "g" * 64, "fixture sha256"),
    ),
)
def test_fixture_staging_validates_unselected_fixture_metadata(
    tmp_path: Path, field: str, value: object, expected: str
) -> None:
    manifest = _fixture_manifest(tmp_path)
    document = json.loads(manifest.read_text(encoding="utf-8"))
    ppt = next(item for item in document["fixtures"] if item["fixture_id"] == "ppt_video")
    ppt[field] = value
    manifest.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match=expected):
        load_and_stage_fixtures(
            manifest,
            manifest.parent / "fixtures",
            tmp_path / "staged",
            required_fixture_ids={"ocr_image"},
        )


def test_fixture_staging_rejects_existing_destination_symlink_with_same_content(
    tmp_path: Path,
) -> None:
    manifest = _fixture_manifest(tmp_path)
    document = json.loads(manifest.read_text(encoding="utf-8"))
    item = document["fixtures"][0]
    destination = Path(item["server_target"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.symlink_to(manifest.parent / "fixtures" / item["source"])
    with pytest.raises(ValueError, match="目标不是普通文件"):
        load_and_stage_fixtures(
            manifest,
            manifest.parent / "fixtures",
            tmp_path / "staged",
        )
    assert destination.is_symlink()


def test_fixture_staging_uses_open_source_snapshot_when_path_is_replaced(
    tmp_path: Path,
) -> None:
    manifest = _fixture_manifest(tmp_path)
    document = json.loads(manifest.read_text(encoding="utf-8"))
    first = document["fixtures"][0]
    source = manifest.parent / "fixtures" / first["source"]
    original = source.read_bytes()
    replaced = False

    def replace_after_open(opened: Path) -> None:
        nonlocal replaced
        if replaced or opened != source:
            return
        replaced = True
        opened.rename(opened.with_suffix(opened.suffix + ".original"))
        opened.write_bytes(b"malicious replacement")

    staged, _ = load_and_stage_fixtures(
        manifest,
        manifest.parent / "fixtures",
        tmp_path / "staged",
        _after_source_open=replace_after_open,
    )
    assert staged[first["fixture_id"]].read_bytes() == original
    assert source.read_bytes() == b"malicious replacement"


def test_smoke_runner_marks_declared_missing_fixture_unexecuted(tmp_path: Path) -> None:
    manifest = _fixture_manifest(tmp_path)
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["fixtures"] = [
        item for item in document["fixtures"] if item["fixture_id"] != "ppt_video"
    ]
    document["missing_fixtures"] = [
        {
            "fixture_id": "ppt_video",
            "reason": "尚未冻结真实 P 视频 URL、长度和 SHA-256",
        }
    ]
    manifest.write_text(json.dumps(document), encoding="utf-8")
    with _WebSocketServer() as ws_url, _Server({}, _smoke_handler(tmp_path)) as http_url:
        completed = _run_smoke(tmp_path, http_url, ws_url, manifest, cases="ppt_slice")

    assert completed.returncode != 0
    cases = json.loads((_release(tmp_path) / "smoke" / "cases.json").read_text())
    assert cases[0]["status"] == "未执行及原因"
    assert "尚未冻结真实 P 视频" in cases[0]["reason"]


def test_committed_fixture_manifest_truthfully_declares_unavailable_media() -> None:
    path = DEPLOY / "operator-smoke-fixtures.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    available = {item["fixture_id"]: item for item in document["fixtures"]}
    missing = {item["fixture_id"] for item in document["missing_fixtures"]}

    assert set(available) == {"ocr_image", "facerec_image", "screen_det_image"}
    assert {
        "asr_offline_audio",
        "asr_online_audio",
        "vbas_image",
        "ppt_video",
    } <= missing
    for item in available.values():
        assert item["source_kind"] == "repository"
        source = WORKSPACE_ROOT / item["source"]
        assert source.is_file() and not source.is_symlink()
        assert source.stat().st_size == item["bytes"]
        assert __import__("hashlib").sha256(source.read_bytes()).hexdigest() == item["sha256"]


def test_smoke_runner_rejects_symlink_fixture(tmp_path: Path) -> None:
    manifest = _fixture_manifest(tmp_path)
    document = json.loads(manifest.read_text(encoding="utf-8"))
    fixture = document["fixtures"][0]
    source = manifest.parent / "fixtures" / fixture["source"]
    real = source.with_suffix(".real")
    source.rename(real)
    source.symlink_to(real)
    with _WebSocketServer() as ws_url, _Server({}, _smoke_handler(tmp_path)) as http_url:
        completed = _run_smoke(tmp_path, http_url, ws_url, manifest, cases="asr_offline")
    assert completed.returncode != 0
    assert "软链接" in completed.stderr


def test_smoke_runner_marks_operator_error_failed(tmp_path: Path) -> None:
    manifest = _fixture_manifest(tmp_path)
    with (
        _WebSocketServer() as ws_url,
        _Server({}, _smoke_handler(tmp_path, fail_ocr=True)) as http_url,
    ):
        completed = _run_smoke(tmp_path, http_url, ws_url, manifest, cases="ocr")
    assert completed.returncode != 0
    cases = json.loads((_release(tmp_path) / "smoke" / "cases.json").read_text())
    assert cases[0]["status"] == "失败"
    evidence = json.loads((_release(tmp_path) / "smoke" / "ocr.json").read_text())
    assert "summary" not in evidence


def _assert_ppt_failure_context(
    evidence: dict[str, Any],
    submitted: list[dict[str, Any]],
) -> None:
    assert len(submitted) == 1
    summary = evidence["summary"]
    expected_ids = {
        "task_id": submitted[0]["task_id"],
        "operator_task_id": submitted[0]["operator_task_id"],
    }
    assert {
        "task_id": summary["task_id"],
        "operator_task_id": summary["operator_task_id"],
    } == expected_ids
    assert summary["attempt_count"] == 1
    assert len(summary["attempts"]) == 1
    failed_attempt = summary["attempts"][0]
    assert {key: failed_attempt[key] for key in expected_ids} == expected_ids
    assert failed_attempt["status"] == "失败"
    assert failed_attempt["reason"] == evidence["reason"]


def test_smoke_runner_does_not_accept_ppt_status_50_without_terminal_callback(
    tmp_path: Path,
) -> None:
    manifest = _fixture_manifest(tmp_path)
    submitted: list[dict[str, Any]] = []
    handler = _smoke_handler(tmp_path, ppt_callback=False)

    def inspect(path: str, headers: Any, body: bytes) -> tuple[int, Any]:
        if path == "/LocalVideoPPTSliceTasks/v1.0.0":
            submitted.append(json.loads(body))
        return handler(path, headers, body)

    with (
        _WebSocketServer() as ws_url,
        _Server({}, inspect) as http_url,
    ):
        completed = _run_smoke(
            tmp_path, http_url, ws_url, manifest, cases="ppt_slice", timeout="0.2"
        )
    assert completed.returncode != 0
    assert "终态回调" in completed.stderr
    evidence = json.loads((_release(tmp_path) / "smoke/ppt_slice.json").read_text())
    _assert_ppt_failure_context(evidence, submitted)


def test_registration_http_timeout_is_bounded_and_reported(tmp_path: Path) -> None:
    started = time.monotonic()
    with _Server({}, delay_seconds=1) as url:
        completed = _run_registration(tmp_path, url, timeout="0.05")
    assert time.monotonic() - started < 2
    assert completed.returncode != 0
    report = json.loads(
        (_release(tmp_path) / "registration" / "operator-registration.json").read_text()
    )
    assert report["status"] == "失败"
    assert "无法连接" in "".join(report["issues"])


def test_registration_events_share_one_global_deadline(tmp_path: Path) -> None:
    instances = _expected_instances()
    responses = _events(instances)
    with _Server(responses, delay_seconds=0.03) as url:
        started = time.monotonic()
        completed = _run_registration(tmp_path, url, timeout="0.12")
        elapsed = time.monotonic() - started
    assert completed.returncode != 0
    assert elapsed < 0.35
    assert "全局超时" in completed.stderr


def test_smoke_http_timeout_is_bounded_and_reported(tmp_path: Path) -> None:
    manifest = _fixture_manifest(tmp_path)
    started = time.monotonic()
    with (
        _WebSocketServer() as ws_url,
        _Server({}, _smoke_handler(tmp_path), delay_seconds=1) as http_url,
    ):
        completed = _run_smoke(tmp_path, http_url, ws_url, manifest, cases="ocr", timeout="0.05")
    assert time.monotonic() - started < 2
    assert completed.returncode != 0
    cases = json.loads((_release(tmp_path) / "smoke" / "cases.json").read_text())
    assert cases[0]["status"] == "失败"


def test_renderer_is_idempotent_and_refuses_different_existing_output(tmp_path: Path) -> None:
    _publish_renderer_transaction(tmp_path)
    _publish_renderer_transaction(tmp_path)

    output = _release(tmp_path) / "summary" / "report.json"
    output.write_text('{"tampered":true}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="拒绝覆盖"):
        _publish_renderer_transaction(tmp_path)
    assert output.read_text(encoding="utf-8") == '{"tampered":true}\n'


def test_renderer_markdown_conflict_does_not_publish_json(tmp_path: Path) -> None:
    release = _release(tmp_path)
    markdown = release / "summary" / "report.md"
    markdown.write_text("conflict\n", encoding="utf-8")
    markdown.chmod(0o600)
    before = (markdown.read_bytes(), os.lstat(markdown).st_ino)
    with pytest.raises(
        ValueError, match=re.escape("拒绝覆盖报告输出摘要冲突: report.md")
    ) as raised:
        _publish_renderer_transaction(tmp_path)
    assert str(raised.value) == "拒绝覆盖报告输出摘要冲突: report.md"
    assert (markdown.read_bytes(), os.lstat(markdown).st_ino) == before
    assert not (release / "summary" / "report.json").exists()
    assert not (release / "summary/.report-transaction.journal").exists()


def test_renderer_recovers_after_process_crash_after_first_rename(tmp_path: Path) -> None:
    release = _release(tmp_path)
    env = os.environ.copy()
    env["REPORT_TRANSACTION_CRASH_AFTER_FIRST_RENAME"] = "1"
    failed = _run_renderer_transaction_subprocess(release, env)
    assert failed.returncode != 0
    recovered = _run_renderer_transaction_subprocess(release)
    assert recovered.returncode == 3, recovered.stderr
    assert (release / "summary" / "report.json").is_file()
    assert (release / "summary" / "report.md").is_file()
    assert not (release / "summary" / ".report-transaction.journal").exists()
    assert not list((release / "summary").glob(".report.json.*"))
    assert not list((release / "summary").glob(".report.md.*"))


def test_renderer_recovers_after_crash_during_atomic_journal_replace(tmp_path: Path) -> None:
    release = _release(tmp_path)
    env = os.environ.copy()
    env["REPORT_TRANSACTION_CRASH_DURING_JOURNAL_REPLACE"] = "1"
    failed = _run_renderer_transaction_subprocess(release, env)
    assert failed.returncode == 87, failed.stderr
    assert failed.stderr == ""
    journal = release / "summary/.report-transaction.journal"
    assert journal.is_file()
    transaction_id = json.loads(journal.read_text(encoding="utf-8"))["transaction_id"]
    replacement = release / f"summary/.report-transaction.journal.{transaction_id}.tmp"
    assert replacement.is_file()
    recovered = _run_renderer_transaction_subprocess(release)
    assert recovered.returncode == 3, recovered.stderr
    assert (release / "summary" / "report.json").is_file()
    assert (release / "summary" / "report.md").is_file()
    assert not list((release / "summary").glob(".report-transaction.journal.*"))


def test_renderer_truncated_journal_fails_closed_without_overwriting_unknown_output(
    tmp_path: Path,
) -> None:
    release = _release(tmp_path)
    unknown = release / "summary" / "report.json"
    unknown.write_text('{"unknown":true}\n', encoding="utf-8")
    journal = release / "summary/.report-transaction.journal"
    journal.write_text('{"published":', encoding="utf-8")
    journal.chmod(0o600)
    unknown_before = (unknown.read_bytes(), os.lstat(unknown).st_ino)
    journal_before = (journal.read_bytes(), os.lstat(journal).st_ino)
    with pytest.raises(
        ValueError, match=re.escape("报告事务 journal 不合法")
    ) as raised:
        _publish_renderer_transaction(tmp_path)
    assert str(raised.value) == "报告事务 journal 不合法"
    assert (unknown.read_bytes(), os.lstat(unknown).st_ino) == unknown_before
    assert (journal.read_bytes(), os.lstat(journal).st_ino) == journal_before


def test_renderer_concurrent_same_content_is_idempotent(tmp_path: Path) -> None:
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _publish_renderer_transaction(tmp_path), range(2)))
    assert results == [None, None]


def test_renderer_concurrent_different_content_has_one_winner(tmp_path: Path) -> None:
    def attempt(content: bytes) -> bool:
        try:
            _publish_renderer_transaction(tmp_path, json_content=content)
        except ValueError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(attempt, content)
            for content in (TRANSACTION_JSON, b'{"different":true}\n')
        ]
        results = [future.result() for future in futures]
    assert sorted(results) == [False, True]


def test_renderer_valid_shape_journal_cannot_delete_cases_input(tmp_path: Path) -> None:
    release = _release(tmp_path)
    cases = release / "summary/cases.json"
    cases.write_bytes(b'{"schema_version":1}\n')
    cases.chmod(0o600)
    before = (cases.read_bytes(), os.lstat(cases).st_ino)
    payload = _transaction_journal_payload()
    payload["outputs"]["cases.json"] = {
        "sha256": hashlib.sha256(cases.read_bytes()).hexdigest(),
        "published": True,
    }
    journal = _write_transaction_journal(release, payload)

    with pytest.raises(ValueError, match="journal.*不合法"):
        _publish_renderer_transaction(tmp_path)

    assert (cases.read_bytes(), os.lstat(cases).st_ino) == before
    assert journal.exists()


def test_renderer_complete_pair_survives_crash_after_second_rename(
    tmp_path: Path,
) -> None:
    release = _release(tmp_path)
    env = os.environ.copy()
    env["REPORT_TRANSACTION_CRASH_AFTER_SECOND_RENAME"] = "1"

    crashed = _run_renderer_transaction_subprocess(release, env)

    assert crashed.returncode == 88, crashed.stderr
    before = {
        name: (path.read_bytes(), os.lstat(path).st_ino)
        for name in ("report.json", "report.md")
        if (path := release / "summary" / name).is_file()
    }
    assert set(before) == {"report.json", "report.md"}
    recovered = _run_renderer_transaction_subprocess(release)
    assert recovered.returncode == 3, recovered.stderr
    assert {
        name: (path.read_bytes(), os.lstat(path).st_ino)
        for name in ("report.json", "report.md")
        if (path := release / "summary" / name).is_file()
    } == before
    assert not (release / "summary/.report-transaction.journal").exists()


def test_renderer_rolls_forward_single_matching_terminal(tmp_path: Path) -> None:
    release = _release(tmp_path)
    report_json = release / "summary/report.json"
    report_json.write_bytes(TRANSACTION_JSON)
    report_json.chmod(0o600)
    json_inode = os.lstat(report_json).st_ino
    _write_transaction_journal(
        release,
        _transaction_journal_payload(published=(True, False), temporaries=("report.md",)),
    )

    _publish_renderer_transaction(tmp_path)

    assert report_json.read_bytes() == TRANSACTION_JSON
    assert os.lstat(report_json).st_ino == json_inode
    assert (release / "summary/report.md").read_bytes() == TRANSACTION_MARKDOWN
    assert not (release / "summary/.report-transaction.journal").exists()


def test_renderer_complete_matching_pair_recovery_does_not_rewrite(
    tmp_path: Path,
) -> None:
    release = _release(tmp_path)
    before: dict[str, tuple[bytes, int]] = {}
    for name, content in (
        ("report.json", TRANSACTION_JSON),
        ("report.md", TRANSACTION_MARKDOWN),
    ):
        path = release / "summary" / name
        path.write_bytes(content)
        path.chmod(0o600)
        before[name] = (path.read_bytes(), os.lstat(path).st_ino)
    _write_transaction_journal(
        release,
        _transaction_journal_payload(published=(False, True)),
    )

    _publish_renderer_transaction(tmp_path)

    assert {
        name: (path.read_bytes(), os.lstat(path).st_ino)
        for name in before
        if (path := release / "summary" / name).is_file()
    } == before


def test_renderer_exception_after_first_publish_preserves_recoverable_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = _release(tmp_path)
    monkeypatch.setenv("REPORT_TRANSACTION_FAIL_AFTER_FIRST_RENAME", "1")

    with pytest.raises(RuntimeError, match="注入"):
        _publish_renderer_transaction(tmp_path)

    first = release / "summary/report.json"
    before = (first.read_bytes(), os.lstat(first).st_ino)
    assert (release / "summary/.report-transaction.journal").exists()
    monkeypatch.delenv("REPORT_TRANSACTION_FAIL_AFTER_FIRST_RENAME")
    _publish_renderer_transaction(tmp_path)
    assert (first.read_bytes(), os.lstat(first).st_ino) == before
    assert (release / "summary/report.md").read_bytes() == TRANSACTION_MARKDOWN


@pytest.mark.parametrize("journal_kind", ("symlink", "0644", "hardlink", "directory"))
def test_renderer_rejects_unsafe_journal_metadata_without_namespace_mutation(
    tmp_path: Path, journal_kind: str
) -> None:
    release = _release(tmp_path)
    summary = release / "summary"
    journal = summary / ".report-transaction.journal"
    payload = json.dumps(_transaction_journal_payload()).encode()
    if journal_kind == "symlink":
        target = tmp_path / "outside-journal"
        target.write_bytes(payload)
        target.chmod(0o600)
        journal.symlink_to(target)
    elif journal_kind == "0644":
        journal.write_bytes(payload)
        journal.chmod(0o644)
    elif journal_kind == "hardlink":
        journal.write_bytes(payload)
        journal.chmod(0o600)
        os.link(journal, summary / ".journal-second-link")
    else:
        journal.mkdir(mode=0o700)
    before = sorted(path.name for path in summary.iterdir())

    with pytest.raises(ValueError, match="journal"):
        _publish_renderer_transaction(tmp_path)

    assert sorted(path.name for path in summary.iterdir()) == before
    assert not (summary / "report.json").exists()
    assert not (summary / "report.md").exists()


@pytest.mark.parametrize(
    "payload",
    (
        {**_transaction_journal_payload(), "extra": True},
        {key: value for key, value in _transaction_journal_payload().items() if key != "state"},
        {**_transaction_journal_payload(), "schema_version": 2},
        {**_transaction_journal_payload(), "transaction_id": "A" * 32},
        {**_transaction_journal_payload(), "state": "rollback"},
        {
            **_transaction_journal_payload(),
            "outputs": {
                "report.json": {"sha256": "b" * 64, "published": False}
            },
        },
        {
            **_transaction_journal_payload(),
            "outputs": {
                **_transaction_journal_payload()["outputs"],
                "report.md": {"sha256": "B" * 64, "published": False},
            },
        },
        {
            **_transaction_journal_payload(),
            "outputs": {
                **_transaction_journal_payload()["outputs"],
                "report.md": {
                    "sha256": hashlib.sha256(TRANSACTION_MARKDOWN).hexdigest(),
                    "published": 1,
                },
            },
        },
        {
            **_transaction_journal_payload(),
            "temporaries": {"cases.json": ".cases.json." + "a" * 32 + ".tmp"},
        },
        {**_transaction_journal_payload(), "temporaries": {"report.md": ".report.md.wrong.tmp"}},
    ),
)
def test_renderer_rejects_invalid_journal_schema_without_mutation(
    tmp_path: Path, payload: object
) -> None:
    release = _release(tmp_path)
    journal = _write_transaction_journal(release, payload)

    with pytest.raises(ValueError, match="journal.*不合法"):
        _publish_renderer_transaction(tmp_path)

    assert journal.exists()
    assert not (release / "summary/report.json").exists()
    assert not (release / "summary/report.md").exists()


@pytest.mark.parametrize(
    "content",
    (
        b'{"schema_version":1,"schema_version":1}',
        b'{"schema_version":NaN}',
    ),
)
def test_renderer_rejects_non_strict_journal_json(tmp_path: Path, content: bytes) -> None:
    release = _release(tmp_path)
    journal = release / "summary/.report-transaction.journal"
    journal.write_bytes(content)
    journal.chmod(0o600)

    with pytest.raises(ValueError, match="journal.*不合法"):
        _publish_renderer_transaction(tmp_path)

    assert journal.read_bytes() == content


def test_renderer_valid_journal_digest_conflict_fails_closed(tmp_path: Path) -> None:
    release = _release(tmp_path)
    report_json = release / "summary/report.json"
    report_json.write_bytes(b"different\n")
    report_json.chmod(0o600)
    before = (report_json.read_bytes(), os.lstat(report_json).st_ino)
    journal = _write_transaction_journal(
        release, _transaction_journal_payload(published=(True, False))
    )

    with pytest.raises(
        ValueError, match=re.escape("拒绝覆盖报告输出摘要冲突: report.json")
    ) as raised:
        _publish_renderer_transaction(tmp_path)

    assert str(raised.value) == "拒绝覆盖报告输出摘要冲突: report.json"
    assert (report_json.read_bytes(), os.lstat(report_json).st_ino) == before
    assert journal.exists()
    assert not (release / "summary/report.md").exists()


def test_renderer_journal_rejects_terminal_hardlinked_to_unknown_name(
    tmp_path: Path,
) -> None:
    release = _release(tmp_path)
    summary = release / "summary"
    for name, content in (
        ("report.json", TRANSACTION_JSON),
        ("report.md", TRANSACTION_MARKDOWN),
    ):
        path = summary / name
        path.write_bytes(content)
        path.chmod(0o600)
    os.link(summary / "report.json", summary / ".unknown-report-link")
    journal = _write_transaction_journal(
        release, _transaction_journal_payload(published=(True, True))
    )
    before = {
        path.name: (path.read_bytes(), os.lstat(path).st_ino)
        for path in (summary / "report.json", summary / "report.md")
    }

    with pytest.raises(ValueError, match="硬链接|不安全"):
        _publish_renderer_transaction(tmp_path)

    assert {
        path.name: (path.read_bytes(), os.lstat(path).st_ino)
        for path in (summary / "report.json", summary / "report.md")
    } == before
    assert journal.exists()


@pytest.mark.parametrize("replacement_kind", ("regular", "symlink"))
def test_renderer_transaction_rejects_temp_replaced_before_hard_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement_kind: str,
) -> None:
    release = _release(tmp_path)
    summary = release / "summary"
    real_link = os.link
    attacked = False

    def replace_then_link(
        source: str,
        destination: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
        follow_symlinks: bool,
    ) -> None:
        nonlocal attacked
        if destination == "report.json" and not attacked:
            attacked = True
            os.unlink(source, dir_fd=src_dir_fd)
            if replacement_kind == "regular":
                descriptor = os.open(
                    source,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=src_dir_fd,
                )
                try:
                    os.write(descriptor, b"attacker-controlled\n")
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            else:
                os.symlink("attacker-controlled", source, dir_fd=src_dir_fd)
        real_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(RENDERER_MODULE.os, "link", replace_then_link)

    with pytest.raises(ValueError, match="临时|终态|发布"):
        _publish_renderer_transaction(tmp_path)

    assert attacked
    assert (summary / ".report-transaction.journal").is_file()
    assert not (summary / "report.md").exists()
    assert not all(
        (summary / name).is_file() for name in ("report.json", "report.md")
    )


def test_renderer_transaction_revalidates_terminals_after_temp_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = _release(tmp_path)
    summary = release / "summary"
    real_unlink = os.unlink
    attacked = False

    def unlink_then_tamper(name: str, *, dir_fd: int) -> None:
        nonlocal attacked
        real_unlink(name, dir_fd=dir_fd)
        if name.startswith(".report.json.") and not attacked:
            attacked = True
            descriptor = os.open(
                "report.json",
                os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW,
                dir_fd=dir_fd,
            )
            try:
                os.write(descriptor, b"tampered-after-temp-cleanup\n")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    monkeypatch.setattr(RENDERER_MODULE.os, "unlink", unlink_then_tamper)

    with pytest.raises(ValueError, match="摘要冲突"):
        _publish_renderer_transaction(tmp_path)

    assert attacked
    assert (summary / ".report-transaction.journal").is_file()
    assert (summary / "report.json").read_bytes() == b"tampered-after-temp-cleanup\n"
    assert (summary / "report.md").read_bytes() == TRANSACTION_MARKDOWN


def test_renderer_first_link_crash_recovers_hardlinked_temporary(tmp_path: Path) -> None:
    release = _release(tmp_path)
    env = os.environ.copy()
    env["REPORT_TRANSACTION_CRASH_AFTER_FIRST_RENAME"] = "1"

    crashed = _run_renderer_transaction_subprocess(release, env)

    assert crashed.returncode == 86, crashed.stderr
    report_json = release / "summary/report.json"
    assert os.lstat(report_json).st_nlink == 2
    recovered = _run_renderer_transaction_subprocess(release)
    assert recovered.returncode == 3, recovered.stderr
    assert os.lstat(report_json).st_nlink == 1
    assert not list((release / "summary").glob(".report.*.tmp"))


def test_renderer_publisher_rejects_root_rebind_without_publishing_to_either_root(
    tmp_path: Path,
) -> None:
    release = _release(tmp_path)
    root_identity = RENDERER_MODULE.release_root_identity(release)
    outputs = _transaction_outputs(release)
    displaced = release.with_name(f"{release.name}-displaced")
    release.rename(displaced)
    (release / "summary").mkdir(parents=True, mode=0o700)

    with pytest.raises(ValueError, match="root.*锚"):
        publish_report_transaction(
            release,
            outputs,
            expected_root_identity=root_identity,
        )

    for root in (release, displaced):
        assert not (root / "summary/report.json").exists()
        assert not (root / "summary/report.md").exists()
