from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import threading
import time
import uuid
import wave
from base64 import b64decode
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PLATFORM_ROOT.parent
DEPLOY = PLATFORM_ROOT / "deploy"
SCRIPTS = DEPLOY / "scripts"
PYTHON = PLATFORM_ROOT / ".venv" / "bin" / "python"
SHA = "a" * 40
TAG = "v1.0_260812"


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


def _expected_instances() -> list[dict[str, Any]]:
    capabilities = {
        "asr-offline": ("asr_offline", ["asr_offline"]),
        "asr-online": ("asr_online", ["asr_online"]),
        "ocr": ("ocr", ["ocr"]),
        "vbas": ("vbas", ["student_behavior", "teacher_behavior"]),
        "facerec": ("facerec", ["recognize"]),
        "screen-det": ("screen_det", ["detect_all"]),
        "ppt-slice": ("ppt_slice", ["ppt_slice"]),
        "text-analysis": ("text_analysis", ["course_overviews", "extract_keywords"]),
    }
    result = []
    for prefix, (code, caps) in capabilities.items():
        suffix = "gpu" if prefix not in {"ppt-slice", "text-analysis"} else "cpu"
        for index in range(3):
            labels = {"gpu": str(index)} if suffix == "gpu" else {}
            result.append(
                {
                    "instance_id": f"{prefix}-{suffix}{index}",
                    "operator_code": code,
                    "capabilities": caps,
                    "service_url": f"http://{prefix}-{suffix}{index}:9999",
                    "declared_capacity": 2 if prefix == "ppt-slice" else 1,
                    "labels": labels,
                    "lifecycle": "ONLINE",
                    "inflight": 0,
                    "model_ready": True,
                    "last_heartbeat_at": "2026-08-12T00:00:01Z",
                }
            )
    return result


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
    tmp_path: Path, url: str, *, timeout: str = "1"
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
        ],
        cwd=PLATFORM_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


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


@pytest.mark.parametrize(
    ("mutate", "reason"),
    (
        (lambda rows: rows.pop(), "缺失"),
        (lambda rows: rows.append(dict(rows[0])), "重复"),
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
            evidence.write_text("{}\n", encoding="utf-8")
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
        _case("INF-MOCK", mock=True),
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
            return 200, {
                "status_code": 200,
                "message": "listed",
                "data": {"persons": [{"number": state["number"]}]},
            }
        if path == "/persons/delete":
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
            json.dumps(endpoints),
            "--cases",
            cases,
            "--timeout-seconds",
            timeout,
            "--mock",
        ],
        cwd=PLATFORM_ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )


def test_smoke_runner_calls_all_eight_operator_contracts_and_redacts(tmp_path: Path) -> None:
    manifest = _fixture_manifest(tmp_path)
    with _WebSocketServer() as ws_url, _Server({}, _smoke_handler(tmp_path)) as http_url:
        completed = _run_smoke(
            tmp_path,
            http_url,
            ws_url,
            manifest,
            face_endpoints=[http_url, http_url + "/b", http_url + "/c"],
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
    with _WebSocketServer() as ws_url, _Server(
        {}, _smoke_handler(other, wrong_face=True)
    ) as http_url:
        wrong = _run_smoke(
            other,
            http_url,
            ws_url,
            wrong_manifest,
            cases="facerec",
            face_endpoints=[http_url, http_url + "/b", http_url + "/c"],
        )
    assert wrong.returncode != 0
    assert "刚创建" in wrong.stderr


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
    evidence.write_text("{}\n", encoding="utf-8")
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
