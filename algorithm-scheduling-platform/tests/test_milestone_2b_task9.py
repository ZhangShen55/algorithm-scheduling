from __future__ import annotations

import importlib.util
import json
import os
import shlex
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


def _case(case_id: str, *, status: str = "通过", mock: bool = False) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "status": status,
        "started_at": "2026-08-12T10:00:00+08:00",
        "finished_at": "2026-08-12T10:00:01+08:00",
        "target": "ocr-gpu0",
        "command": "deploy/scripts/run-operator-smoke --case ocr",
        "evidence": ["smoke/ocr.json"],
        "reason": "响应合同正确",
        "mock": mock,
        "release_tag": TAG,
        "git_sha": SHA,
    }


def _run_renderer(tmp_path: Path, cases: list[dict[str, Any]]) -> subprocess.CompletedProcess[str]:
    release = _release(tmp_path)
    for item in cases:
        for relative in item.get("evidence", []):
            evidence = release / relative
            evidence.parent.mkdir(parents=True, exist_ok=True)
            evidence.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "evidence_type": "operator_smoke",
                        "status": "PASS",
                        "mock": item["mock"],
                        "release_tag": item["release_tag"],
                        "git_sha": item["git_sha"],
                        "target": item["target"],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
    source = release / "summary" / f"cases-{uuid.uuid4().hex}.json"
    source.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    return subprocess.run(
        [
            str(PYTHON),
            str(PLATFORM_ROOT / "scripts" / "render_milestone_2b_report.py"),
            "--input",
            str(source),
            "--release-root",
            str(release),
            "--output-json",
            str(release / "summary" / "report.json"),
            "--output-markdown",
            str(release / "summary" / "report.md"),
        ],
        cwd=PLATFORM_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_renderer_summarizes_real_mock_and_statuses(tmp_path: Path) -> None:
    cases = [
        _case("INF-001"),
        {**_case("INF-MOCK", mock=True), "evidence": ["smoke/ocr-mock.json"]},
        {**_case("INF-002", status="失败"), "evidence": [], "reason": "HTTP 500"},
        {
            **_case("INF-003", status="未执行及原因"),
            "command": "",
            "evidence": [],
            "reason": "缺少外部 fixture",
        },
    ]
    completed = _run_renderer(tmp_path, cases)

    assert completed.returncode == 0, completed.stderr
    result = json.loads(
        (_release(tmp_path) / "summary" / "report.json").read_text(encoding="utf-8")
    )
    assert result["counts"] == {"通过": 1, "失败": 1, "未执行及原因": 1}
    assert result["mock_counts"] == {"通过": 1, "失败": 0, "未执行及原因": 0}
    markdown = (_release(tmp_path) / "summary" / "report.md").read_text(encoding="utf-8")
    assert "真实验证" in markdown and "Mock 合同验证" in markdown


@pytest.mark.parametrize(
    "mutate",
    (
        lambda cases: cases.append(dict(cases[0])),
        lambda cases: cases[0].update(status="成功"),
        lambda cases: cases[0].update(evidence=[]),
        lambda cases: cases[0].update(reason="", status="未执行及原因", evidence=[]),
        lambda cases: cases[0].update(unknown=True),
        lambda cases: cases[0].update(evidence=["../escape.json"]),
        lambda cases: cases[0].update(command="python -c 'repository.complete_node()'"),
        lambda cases: cases[0].update(command="curl -H 'Authorization: Bearer secret'"),
        lambda cases: cases[0].update(git_sha="b" * 40),
    ),
)
def test_renderer_rejects_unsafe_or_invalid_cases(tmp_path: Path, mutate: Any) -> None:
    cases = [_case("INF-001")]
    mutate(cases)
    completed = _run_renderer(tmp_path, cases)

    assert completed.returncode != 0


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("status", "FAIL"),
        ("mock", True),
        ("git_sha", "b" * 40),
        ("target", "other-target"),
        ("evidence_type", "unknown"),
    ),
)
def test_renderer_rejects_evidence_semantic_mismatch(
    tmp_path: Path, field: str, value: Any
) -> None:
    cases = [_case("INF-001")]
    release = _release(tmp_path)
    evidence = release / "smoke" / "ocr.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "evidence_type": "operator_smoke",
                "status": "PASS",
                "mock": False,
                "release_tag": TAG,
                "git_sha": SHA,
                "target": "ocr-gpu0",
                field: value,
            }
        ),
        encoding="utf-8",
    )
    source = release / "summary" / "cases.json"
    source.write_text(json.dumps(cases), encoding="utf-8")
    completed = subprocess.run(
        [
            str(PYTHON),
            str(PLATFORM_ROOT / "scripts" / "render_milestone_2b_report.py"),
            "--input",
            str(source),
            "--release-root",
            str(release),
            "--output-json",
            str(release / "summary" / "report.json"),
            "--output-markdown",
            str(release / "summary" / "report.md"),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode != 0


def test_renderer_escapes_all_markdown_cells_and_rejects_invalid_identifiers(
    tmp_path: Path,
) -> None:
    escaped = [
        {
            **_case("INF-001"),
            "status": "失败",
            "evidence": [],
            "target": "ocr|<script>alert(1)</script>",
            "reason": "bad|<img src=x>\nnext",
        }
    ]
    completed = _run_renderer(tmp_path, escaped)
    assert completed.returncode == 0, completed.stderr
    markdown = (_release(tmp_path) / "summary" / "report.md").read_text(encoding="utf-8")
    assert "<script>" not in markdown and "<img" not in markdown
    assert "ocr\\|&lt;script&gt;" in markdown

    invalid = [{**_case("bad|id"), "status": "失败", "evidence": []}]
    rejected = _run_renderer(tmp_path / "invalid", invalid)
    assert rejected.returncode != 0


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


def test_gpu_acceptance_docs_contain_complete_executable_commands() -> None:
    readme = (DEPLOY / "README.md").read_text(encoding="utf-8")
    scenario = (
        PLATFORM_ROOT / "harness/scenarios/milestone-2b-deploy.md"
    ).read_text(encoding="utf-8")
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
    ):
        assert required in readme
    assert "run-operator-smoke is delivered by the later" not in readme
    assert "http://192.168.29.11:19090" in scenario
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
    assert "--instance-id asr-offline-gpu0" in scenario


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
            image = manifest.parent / "slices" / "ppt-0001-f17-t16s.jpg"
            image.parent.mkdir(parents=True, exist_ok=True)
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


def test_smoke_runner_does_not_accept_ppt_status_50_without_terminal_callback(
    tmp_path: Path,
) -> None:
    manifest = _fixture_manifest(tmp_path)
    with (
        _WebSocketServer() as ws_url,
        _Server({}, _smoke_handler(tmp_path, ppt_callback=False)) as http_url,
    ):
        completed = _run_smoke(
            tmp_path, http_url, ws_url, manifest, cases="ppt_slice", timeout="0.2"
        )
    assert completed.returncode != 0
    assert "终态回调" in completed.stderr


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
    cases = [_case("INF-001")]
    first = _run_renderer(tmp_path, cases)
    second = _run_renderer(tmp_path, cases)
    assert first.returncode == second.returncode == 0

    output = _release(tmp_path) / "summary" / "report.json"
    output.write_text('{"tampered":true}\n', encoding="utf-8")
    third = _run_renderer(tmp_path, cases)
    assert third.returncode != 0
    assert output.read_text(encoding="utf-8") == '{"tampered":true}\n'


def test_renderer_markdown_conflict_does_not_publish_json(tmp_path: Path) -> None:
    cases = [_case("INF-001")]
    release = _release(tmp_path)
    markdown = release / "summary" / "report.md"
    markdown.write_text("conflict\n", encoding="utf-8")
    completed = _run_renderer(tmp_path, cases)
    assert completed.returncode != 0
    assert not (release / "summary" / "report.json").exists()


def test_renderer_recovers_after_process_crash_after_first_rename(tmp_path: Path) -> None:
    cases = [_case("INF-001")]
    release = _release(tmp_path)
    source = release / "summary" / "cases.json"
    evidence = release / "smoke" / "ocr.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "evidence_type": "operator_smoke",
                "status": "PASS",
                "mock": False,
                "release_tag": TAG,
                "git_sha": SHA,
                "target": "ocr-gpu0",
            }
        ),
        encoding="utf-8",
    )
    source.write_text(json.dumps(cases), encoding="utf-8")
    command = [
        str(PYTHON),
        str(PLATFORM_ROOT / "scripts" / "render_milestone_2b_report.py"),
        "--input",
        str(source),
        "--release-root",
        str(release),
        "--output-json",
        str(release / "summary" / "report.json"),
        "--output-markdown",
        str(release / "summary" / "report.md"),
    ]
    env = os.environ.copy()
    env["REPORT_TRANSACTION_CRASH_AFTER_FIRST_RENAME"] = "1"
    failed = subprocess.run(command, env=env, text=True, capture_output=True, check=False)
    assert failed.returncode != 0
    recovered = subprocess.run(command, text=True, capture_output=True, check=False)
    assert recovered.returncode == 0, recovered.stderr
    assert (release / "summary" / "report.json").is_file()
    assert (release / "summary" / "report.md").is_file()
    assert not (release / "summary" / ".report-transaction.journal").exists()
    assert not list((release / "summary").glob(".report.json.*"))
    assert not list((release / "summary").glob(".report.md.*"))


def test_renderer_recovers_after_crash_during_atomic_journal_replace(tmp_path: Path) -> None:
    cases = [_case("INF-001")]
    release = _release(tmp_path)
    source = release / "summary" / "cases.json"
    evidence = release / "smoke" / "ocr.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "evidence_type": "operator_smoke",
                "status": "PASS",
                "mock": False,
                "release_tag": TAG,
                "git_sha": SHA,
                "target": "ocr-gpu0",
            }
        ),
        encoding="utf-8",
    )
    source.write_text(json.dumps(cases), encoding="utf-8")
    command = [
        str(PYTHON),
        str(PLATFORM_ROOT / "scripts" / "render_milestone_2b_report.py"),
        "--input",
        str(source),
        "--release-root",
        str(release),
        "--output-json",
        str(release / "summary" / "report.json"),
        "--output-markdown",
        str(release / "summary" / "report.md"),
    ]
    env = os.environ.copy()
    env["REPORT_TRANSACTION_CRASH_DURING_JOURNAL_REPLACE"] = "1"
    failed = subprocess.run(command, env=env, text=True, capture_output=True, check=False)
    assert failed.returncode != 0
    recovered = subprocess.run(command, text=True, capture_output=True, check=False)
    assert recovered.returncode == 0, recovered.stderr
    assert (release / "summary" / "report.json").is_file()
    assert (release / "summary" / "report.md").is_file()
    assert not list((release / "summary").glob(".report-transaction.journal.*"))


def test_renderer_truncated_journal_fails_closed_without_overwriting_unknown_output(
    tmp_path: Path,
) -> None:
    cases = [_case("INF-001")]
    release = _release(tmp_path)
    unknown = release / "summary" / "report.json"
    unknown.write_text('{"unknown":true}\n', encoding="utf-8")
    (release / "summary" / ".report-transaction.journal").write_text(
        '{"published":', encoding="utf-8"
    )
    completed = _run_renderer(tmp_path, cases)
    assert completed.returncode != 0
    assert "journal" in completed.stderr and "不合法" in completed.stderr
    assert unknown.read_text(encoding="utf-8") == '{"unknown":true}\n'


def test_renderer_concurrent_same_content_is_idempotent(tmp_path: Path) -> None:
    cases = [_case("INF-001")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _run_renderer(tmp_path, cases), range(2)))
    assert [item.returncode for item in results] == [0, 0]


def test_renderer_concurrent_different_content_has_one_winner(tmp_path: Path) -> None:
    first = [_case("INF-001")]
    second = [{**_case("INF-002"), "reason": "另一份报告"}]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(_run_renderer, tmp_path, cases)
            for cases in (first, second)
        ]
        results = [future.result() for future in futures]
    assert sorted(item.returncode for item in results) == [0, 1]
