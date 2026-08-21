#!/usr/bin/env python3

from __future__ import annotations

import argparse
import asyncio
import base64
import errno
import hashlib
import ipaddress
import json
import os
import re
import shlex
import stat
import sys
import tempfile
import threading
import time
import uuid
import wave
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx
import websockets

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))
if TYPE_CHECKING:
    from .operator_topology import CURRENT_TOPOLOGY
else:
    from operator_topology import CURRENT_TOPOLOGY

PLATFORM_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = PLATFORM_ROOT.parent
DEFAULT_CASES = PLATFORM_ROOT / "deploy" / "operator-smoke-cases.json"
TAG_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
SHA_PATTERN = re.compile(r"[0-9a-fA-F]{40}")
SHA256_PATTERN = re.compile(r"[0-9a-fA-F]{64}")
FIXTURE_FIELDS = {"fixture_id", "source_kind", "source", "server_target", "bytes", "sha256"}
CASE_FIELDS = {"case_id", "operator_code", "fixtures", "checks"}
ACTIVITY_FD_ENV = "GPU_EVIDENCE_ACTIVITY_FD"
ACTIVITY_NONCE_ENV = "GPU_EVIDENCE_ACTIVITY_NONCE"
ActivityEmitter = Callable[[str], None]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="直接调用七类算子的 Smoke Harness")
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--reports-root", type=Path, required=True)
    parser.add_argument("--fixture-manifest", type=Path, required=True)
    parser.add_argument("--external-fixture-root", type=Path, required=True)
    parser.add_argument("--fixture-target-root", type=Path, required=True)
    parser.add_argument("--result-root", type=Path, required=True)
    parser.add_argument("--callback-listen-host", default="0.0.0.0")
    parser.add_argument("--callback-advertise-base-url")
    parser.add_argument("--endpoints-json", required=True)
    parser.add_argument("--cases", default="all")
    parser.add_argument("--operator")
    parser.add_argument("--instance")
    parser.add_argument("--run-id")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--hold-seconds", type=float, default=0.0)
    parser.add_argument("--case-manifest", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--timeout-seconds", type=float, default=300)
    parser.add_argument("--mock", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def safe_component(value: str, pattern: re.Pattern[str], name: str) -> str:
    if value in {".", ".."} or pattern.fullmatch(value) is None:
        raise ValueError(f"{name} 不是安全的单路径段")
    return value


def resolve_run_id(value: str | None) -> str | None:
    if value is None:
        return None
    if value == "auto":
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        return f"auto-{timestamp}-{uuid.uuid4().hex}"
    return safe_component(value, TAG_PATTERN, "run ID")


def load_activity_channel() -> tuple[int, str] | None:
    descriptor_value = os.environ.get(ACTIVITY_FD_ENV)
    nonce = os.environ.get(ACTIVITY_NONCE_ENV)
    if descriptor_value is None and nonce is None:
        return None
    if descriptor_value is None or nonce is None or not nonce or "\n" in nonce:
        raise ValueError("GPU activity 通道环境变量不完整")
    try:
        descriptor = int(descriptor_value)
        metadata = os.fstat(descriptor)
    except (OSError, ValueError) as error:
        raise ValueError("GPU activity 通道文件描述符无效") from error
    if descriptor < 0 or not stat.S_ISFIFO(metadata.st_mode):
        raise ValueError("GPU activity 通道必须是 inherited pipe")
    return descriptor, nonce


def emit_activity(
    channel: tuple[int, str] | None,
    *,
    event: str,
    operator_code: str,
    instance_id: str,
    run_id: str,
    attempt: int,
    target_origin: str,
) -> None:
    if channel is None:
        return
    descriptor, nonce = channel
    data = (
        json.dumps(
            {
                "event": event,
                "nonce": nonce,
                "operator_code": operator_code,
                "instance_id": instance_id,
                "run_id": run_id,
                "attempt": attempt,
                "target_origin": target_origin,
            },
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        view = view[written:]


def bind_activity(
    channel: tuple[int, str],
    *,
    operator_code: str,
    instance_id: str,
    run_id: str,
    attempt: int,
    target_origin: str,
) -> ActivityEmitter:
    def bound(event: str) -> None:
        emit_activity(
            channel,
            event=event,
            operator_code=operator_code,
            instance_id=instance_id,
            run_id=run_id,
            attempt=attempt,
            target_origin=target_origin,
        )

    return bound


@contextmanager
def activity_window(activity: ActivityEmitter | None) -> Iterator[None]:
    if activity is None:
        yield
        return
    activity("start")
    try:
        yield
    finally:
        activity("finish")


def reject_symlink_chain(path: Path, name: str) -> None:
    absolute = path.absolute()
    for candidate in (*reversed(absolute.parents), absolute):
        if (
            candidate == Path(candidate.anchor)
            or not candidate.exists()
            and not candidate.is_symlink()
        ):
            continue
        metadata = os.lstat(candidate)
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(f"{name} 不能包含软链接: {candidate}")


def safe_relative(value: str, name: str) -> Path:
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError(f"{name} 必须是安全的仓库相对路径")
    return Path(*relative.parts)


def inside(path: Path, root: Path) -> bool:
    normalized = Path(os.path.abspath(path))
    normalized_root = Path(os.path.abspath(root))
    return normalized == normalized_root or normalized_root in normalized.parents


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    reject_symlink_chain(path.parent, "报告目录")
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        content = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        descriptor = -1
        if path.exists():
            if path.read_bytes() == content:
                temporary.unlink()
                return
            raise ValueError(f"拒绝覆盖不同运行证据: {path}")
        os.link(temporary, path)
        temporary.unlink()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def load_cases(path: Path) -> list[dict[str, Any]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if set(document) != {"schema_version", "cases"} or document["schema_version"] != 1:
        raise ValueError("Smoke case manifest schema 不受支持")
    cases = document["cases"]
    if (
        not isinstance(cases, list)
        or len(cases) != CURRENT_TOPOLOGY.totals["operator_smoke_types"]
    ):
        raise ValueError("Smoke case manifest 必须精确包含七类算子")
    codes: set[str] = set()
    ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != CASE_FIELDS:
            raise ValueError("Smoke case manifest 包含未知或缺失字段")
        if case["case_id"] in ids or case["operator_code"] in codes:
            raise ValueError("Smoke case_id 或 operator_code 重复")
        if not isinstance(case["checks"], list) or not case["checks"]:
            raise ValueError("每个 Smoke case 必须声明 checks")
        ids.add(case["case_id"])
        codes.add(case["operator_code"])
    return cases


def load_endpoints(value: str) -> dict[str, Any]:
    candidate = Path(value)
    if candidate.is_file() or candidate.is_symlink():
        reject_symlink_chain(candidate, "endpoints JSON")
        document = json.loads(candidate.read_text(encoding="utf-8"))
    else:
        document = json.loads(value)
    if not isinstance(document, dict):
        raise ValueError("endpoints-json 必须是对象或包含对象的 JSON 文件")
    return document


def normalized_http_origin(value: Any) -> tuple[str, str, int]:
    if not isinstance(value, str):
        raise ValueError("HTTP endpoint 必须是字符串")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError("HTTP endpoint 端口不合法") from error
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower() if parsed.hostname is not None else None
    if (
        scheme not in {"http", "https"}
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or bool(parsed.query)
        or bool(parsed.fragment)
    ):
        raise ValueError("HTTP endpoint 协议或 origin 不合法")
    return scheme, hostname, port or (443 if scheme == "https" else 80)


def normalized_target_origin(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("target endpoint 必须是字符串")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError("target endpoint 端口不合法") from error
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower() if parsed.hostname is not None else None
    if (
        scheme not in {"http", "https", "ws", "wss"}
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("target endpoint origin 不合法")
    explicit_port = port or (443 if scheme in {"https", "wss"} else 80)
    formatted_host = f"[{hostname}]" if ":" in hostname else hostname
    return f"{scheme}://{formatted_host}:{explicit_port}"


def resolve_endpoint(
    endpoints: dict[str, Any], code: str, instance_id: str | None
) -> tuple[Any, str]:
    configured = endpoints.get(code)
    if instance_id is None:
        if isinstance(configured, dict):
            raise ValueError(f"{code} endpoint 是实例映射，必须同时指定 --instance")
        return configured, code
    if not isinstance(configured, dict) or instance_id not in configured:
        raise ValueError(f"{code} 未配置目标实例 endpoint: {instance_id}")
    if code != "facerec":
        return configured[instance_id], instance_id
    if len(configured) != 3:
        raise ValueError("FaceRec Smoke 必须配置三个不同实例")
    try:
        origins = {normalized_http_origin(value) for value in configured.values()}
    except ValueError as error:
        raise ValueError("FaceRec Smoke 必须配置三个不同实例") from error
    if len(origins) != 3:
        raise ValueError("FaceRec Smoke 必须配置三个不同实例")
    others = [value for key, value in sorted(configured.items()) if key != instance_id]
    if len(others) != 2:
        raise ValueError("FaceRec Smoke 必须配置三个不同实例")
    return [others[0], configured[instance_id], others[1]], instance_id


def hash_stream(stream: Any) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    while chunk := stream.read(1024 * 1024):
        total += len(chunk)
        digest.update(chunk)
    return total, digest.hexdigest()


def open_regular_relative(root: Path, relative: Path, name: str) -> int:
    reject_symlink_chain(root, name)
    directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in relative.parts[:-1]:
            try:
                next_fd = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=directory_fd,
                )
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise ValueError(f"{name} 不能包含软链接或非目录") from exc
                raise
            os.close(directory_fd)
            directory_fd = next_fd
        try:
            descriptor = os.open(
                relative.name,
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise ValueError(f"{name} 不能是软链接") from exc
            raise
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise ValueError(f"{name} 不是普通文件")
        return descriptor
    finally:
        os.close(directory_fd)


def verify_existing_fixture(path: Path, expected: tuple[int, str], fixture_id: str) -> None:
    metadata = os.lstat(path)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"fixture 目标不是普通文件: {fixture_id}")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            actual = hash_stream(stream)
    finally:
        os.close(descriptor)
    if actual != expected:
        raise ValueError(f"fixture 目标已存在不同内容: {fixture_id}")


def copy_fixture_snapshot(
    source_fd: int,
    source: Path,
    destination: Path,
    expected: tuple[int, str],
    fixture_id: str,
    after_source_open: Callable[[Path], None] | None,
) -> None:
    descriptor, name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    temporary = Path(name)
    digest = hashlib.sha256()
    total = 0
    try:
        os.fchmod(descriptor, 0o600)
        if after_source_open is not None:
            after_source_open(source)
        with (
            os.fdopen(os.dup(source_fd), "rb") as source_stream,
            os.fdopen(descriptor, "wb", closefd=True) as target_stream,
        ):
            descriptor = -1
            while chunk := source_stream.read(1024 * 1024):
                target_stream.write(chunk)
                digest.update(chunk)
                total += len(chunk)
            target_stream.flush()
            os.fsync(target_stream.fileno())
        if (total, digest.hexdigest()) != expected:
            raise ValueError(f"fixture 字节数或 SHA-256 不匹配: {fixture_id}")
        try:
            os.link(temporary, destination, follow_symlinks=False)
        except FileExistsError:
            verify_existing_fixture(destination, expected, fixture_id)
        directory_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def load_and_stage_fixtures(
    manifest_path: Path,
    external_root: Path,
    target_root: Path,
    *,
    required_fixture_ids: set[str] | None = None,
    _after_source_open: Callable[[Path], None] | None = None,
) -> tuple[dict[str, Path], dict[str, str]]:
    reject_symlink_chain(manifest_path, "fixture manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        set(manifest) != {"schema_version", "fixtures", "missing_fixtures"}
        or manifest["schema_version"] != 1
    ):
        raise ValueError("fixture manifest schema 不受支持")
    fixtures = manifest["fixtures"]
    if not isinstance(fixtures, list):
        raise ValueError("fixture manifest fixtures 必须是数组")
    staged: dict[str, Path] = {}
    declared: set[str] = set()
    missing: dict[str, str] = {}
    missing_entries = manifest["missing_fixtures"]
    if not isinstance(missing_entries, list):
        raise ValueError("fixture manifest missing_fixtures 必须是数组")
    for item in missing_entries:
        if not isinstance(item, dict) or set(item) != {"fixture_id", "reason"}:
            raise ValueError("missing fixture 包含未知或缺失字段")
        fixture_id = safe_component(str(item["fixture_id"]), TAG_PATTERN, "fixture_id")
        reason = str(item["reason"]).strip()
        if not reason or fixture_id in missing:
            raise ValueError("missing fixture 原因为空或 fixture_id 重复")
        missing[fixture_id] = reason
    target_ready = False
    for item in fixtures:
        if not isinstance(item, dict) or set(item) != FIXTURE_FIELDS:
            raise ValueError("fixture manifest 包含未知或缺失字段")
        fixture_id = safe_component(str(item["fixture_id"]), TAG_PATTERN, "fixture_id")
        if fixture_id in declared:
            raise ValueError(f"fixture_id 重复: {fixture_id}")
        declared.add(fixture_id)
        source_kind = item["source_kind"]
        relative = safe_relative(str(item["source"]), "fixture source")
        if source_kind == "external":
            source_root = external_root
        elif source_kind == "repository":
            source_root = WORKSPACE_ROOT
        else:
            raise ValueError(f"未知 fixture source_kind: {source_kind}")
        source = source_root / relative
        expected_bytes = item["bytes"]
        if (
            not isinstance(expected_bytes, int)
            or isinstance(expected_bytes, bool)
            or expected_bytes < 0
        ):
            raise ValueError(f"fixture bytes 必须是非负整数: {fixture_id}")
        expected_sha256 = item["sha256"]
        if (
            not isinstance(expected_sha256, str)
            or SHA256_PATTERN.fullmatch(expected_sha256) is None
        ):
            raise ValueError(f"fixture sha256 必须是 64 位十六进制字符串: {fixture_id}")
        expected = (expected_bytes, expected_sha256.lower())
        destination = Path(str(item["server_target"]))
        if not destination.is_absolute() or not inside(destination, target_root):
            raise ValueError(f"fixture server_target 越出目标根: {fixture_id}")
        if required_fixture_ids is not None and fixture_id not in required_fixture_ids:
            continue
        if not target_ready:
            target_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            reject_symlink_chain(target_root, "fixture 目标根")
            target_ready = True
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        reject_symlink_chain(destination.parent, "fixture 目标目录")
        source_fd = open_regular_relative(source_root, relative, "fixture source")
        try:
            copy_fixture_snapshot(
                source_fd,
                source,
                destination,
                expected,
                fixture_id,
                _after_source_open,
            )
        finally:
            os.close(source_fd)
        staged[fixture_id] = destination
    if declared & set(missing):
        raise ValueError("fixture 不能同时声明为可用和缺失")
    return staged, missing


def data_url(path: Path) -> str:
    suffix = path.suffix.lower()
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(
        suffix, "application/octet-stream"
    )
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


class CallbackCapture:
    def __init__(self, *, listen_host: str, advertise_base_url: str) -> None:
        self.payload: dict[str, Any] | None = None
        self.event = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                try:
                    value = json.loads(self.rfile.read(length))
                    if not isinstance(value, dict):
                        raise ValueError
                    owner.payload = value
                    owner.event.set()
                    self.send_response(204)
                    self.end_headers()
                except (ValueError, json.JSONDecodeError):
                    self.send_response(400)
                    self.end_headers()

            def log_message(self, *_: object) -> None:
                return

        parsed = validate_callback_advertise_base_url(advertise_base_url)
        self.advertise_scheme = parsed.scheme
        self.advertise_host = parsed.hostname
        self.advertise_port = parsed.port
        listen_port = self.advertise_port if self.advertise_port is not None else 0
        self.server = ThreadingHTTPServer((listen_host, listen_port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> CallbackCapture:
        self.thread.start()
        return self

    @property
    def url(self) -> str:
        address = self.server.server_address
        port = self.advertise_port or int(address[1])
        return f"{self.advertise_scheme}://{self.advertise_host}:{port}/terminal"

    def __exit__(self, *_: object) -> None:
        self.server.shutdown()
        self.thread.join(timeout=3)
        self.server.server_close()


def validate_callback_advertise_base_url(value: str) -> Any:
    parsed = urlsplit(value)
    if parsed.scheme != "http" or not parsed.hostname:
        raise ValueError("callback advertise base URL 必须是 HTTP URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("callback advertise base URL 不能包含用户凭据")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("callback advertise base URL 不能包含路径、查询或片段")
    if parsed.hostname.lower() == "localhost":
        raise ValueError("callback advertise base URL 必须可由算子容器访问")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address is not None and (address.is_loopback or address.is_unspecified):
        raise ValueError("callback advertise base URL 必须可由算子容器访问")
    return parsed


def require_http(response: httpx.Response, name: str) -> dict[str, Any]:
    if response.status_code < 200 or response.status_code >= 300:
        raise RuntimeError(f"{name} HTTP {response.status_code}")
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError(f"{name} 响应不是 JSON 对象")
    return value


def smoke_asr_offline(
    http: httpx.Client,
    endpoint: str,
    fixtures: dict[str, Path],
    _: float,
    *,
    activity: ActivityEmitter | None = None,
) -> dict[str, Any]:
    path = fixtures["asr_offline_audio"]
    with path.open("rb") as stream:
        with activity_window(activity):
            response = http.post(
                endpoint.rstrip("/") + "/v1.1.8/seacraft_asr",
                files={"audioFile": (path.name, stream, "audio/wav")},
                data={
                    "language": "auto",
                    "showSpk": "true",
                    "showEmotion": "true",
                    "showRoleIdentify": "false",
                    "wordTimestamps": "false",
                },
            )
    body = require_http(response, "ASR Offline")
    if body.get("code") not in {None, 0} or not body.get("text") or not body.get("segments"):
        raise RuntimeError("ASR Offline 未返回非空 text/segments")
    return {"segment_count": len(body["segments"]), "text_non_empty": True}


async def _online(
    endpoint: str,
    audio: Path,
    timeout: float,
    *,
    activity: ActivityEmitter | None = None,
) -> dict[str, Any]:
    with wave.open(str(audio), "rb") as stream:
        if (stream.getframerate(), stream.getnchannels(), stream.getsampwidth()) != (16000, 1, 2):
            raise RuntimeError("ASR Online fixture 必须是 16kHz 单声道 PCM16 WAV")
        pcm = stream.readframes(stream.getnframes())
    url = endpoint.rstrip("/") + "/v1.0.1/seacraft_asr_online"
    texts: list[str] = []
    async with websockets.connect(url, open_timeout=timeout, close_timeout=timeout) as socket:
        started = False
        try:
            for offset in range(0, len(pcm), 7680 * 2):
                if not started and activity is not None:
                    activity("start")
                    started = True
                await socket.send(pcm[offset : offset + 7680 * 2])
                response = json.loads(await asyncio.wait_for(socket.recv(), timeout))
                if response.get("text"):
                    texts.append(str(response["text"]))
                if texts:
                    break
        finally:
            if started and activity is not None:
                activity("finish")
    if not texts:
        raise RuntimeError("ASR Online 未返回增量文本")
    return {"incremental_messages": len(texts), "text_non_empty": True}


def smoke_asr_online(
    _: httpx.Client,
    endpoint: str,
    fixtures: dict[str, Path],
    timeout: float,
    *,
    activity: ActivityEmitter | None = None,
) -> dict[str, Any]:
    return asyncio.run(
        _online(endpoint, fixtures["asr_online_audio"], timeout, activity=activity)
    )


def smoke_ocr(
    http: httpx.Client,
    endpoint: str,
    fixtures: dict[str, Path],
    _: float,
    *,
    activity: ActivityEmitter | None = None,
) -> dict[str, Any]:
    image = base64.b64encode(fixtures["ocr_image"].read_bytes()).decode()
    with activity_window(activity):
        response = http.post(
            endpoint.rstrip("/") + "/ocr/prediction",
            json={
                "key": ["smoke-image"],
                "value": [image],
                "enable_formula": False,
            },
        )
    body = require_http(
        response,
        "OCR",
    )
    if body.get("err_no") != 0 or len(body.get("value") or []) != 1:
        raise RuntimeError("OCR 响应合同失败")
    parsed = json.loads(body["value"][0])
    if not parsed or not any(item.get("text") for item in parsed):
        raise RuntimeError("OCR 未返回文本")
    return {"image_count": 1, "text_items": len(parsed)}


def smoke_vbas(
    http: httpx.Client,
    endpoint: str,
    fixtures: dict[str, Path],
    _: float,
    *,
    activity: ActivityEmitter | None = None,
) -> dict[str, Any]:
    payload = {
        "ImageList": [{"StoragePath": data_url(fixtures["vbas_image"]), "ImageId": "smoke-frame"}],
        "task_id": "smoke-vbas",
        "batch_id": "smoke-batch",
    }
    checks = {}
    with activity_window(activity):
        for role in ("student", "teacher"):
            body = require_http(
                http.post(
                    endpoint.rstrip("/") + f"/ImageDetect/{role}/v1.0.0",
                    json=payload,
                ),
                f"VBas {role}",
            )
            status = body.get("StatusObject")
            data = body.get("DataList")
            if not isinstance(status, dict) or status.get("StatusCode") != 0:
                raise RuntimeError(f"VBas {role} 顶层 StatusCode 不是 0")
            if not isinstance(data, list) or len(data) != 1:
                raise RuntimeError(f"VBas {role} DataList 未映射输入图片")
            image_status = data[0].get("StatusObject") if isinstance(data[0], dict) else None
            if (
                not isinstance(image_status, dict)
                or image_status.get("StatusCode") != 0
                or image_status.get("ImageId") != "smoke-frame"
            ):
                raise RuntimeError(f"VBas {role} 图片 StatusCode 或 ImageId 不匹配")
            checks[role] = len(data)
    return checks


def smoke_facerec(
    http: httpx.Client,
    endpoint: str,
    fixtures: dict[str, Path],
    _: float,
    *,
    activity: ActivityEmitter | None = None,
) -> dict[str, Any]:
    endpoints = json.loads(endpoint)
    if not isinstance(endpoints, list) or len(endpoints) != 3:
        raise RuntimeError("FaceRec Smoke 必须配置三个不同实例")
    try:
        origins = {normalized_http_origin(item) for item in endpoints}
    except ValueError as error:
        raise RuntimeError("FaceRec Smoke 必须配置三个不同实例") from error
    if len(origins) != 3:
        raise RuntimeError("FaceRec Smoke 必须配置三个不同实例")
    create_endpoint, recognize_endpoint, manage_endpoint = [
        str(item).rstrip("/") for item in endpoints
    ]
    image = data_url(fixtures["facerec_image"])
    number = "harness-" + uuid.uuid4().hex
    created = False
    primary_error: BaseException | None = None
    result: dict[str, Any] | None = None
    try:
        created_body = require_http(
            http.post(
                create_endpoint + "/persons",
                json={"photo": image, "name": "Harness", "number": number},
            ),
            "FaceRec persons",
        )
        if created_body.get("status_code") != 200:
            raise RuntimeError("FaceRec 人物创建失败")
        data = created_body.get("data") or {}
        if data.get("photo_path") not in {None, ""}:
            raise RuntimeError("FaceRec save_person_photo=false 未生效")
        created = True
        with activity_window(activity):
            recognize_response = http.post(
                recognize_endpoint + "/recognize", json={"photo": image, "targets": [number]}
            )
        recognized = require_http(recognize_response, "FaceRec recognize")
        matches = (recognized.get("data") or {}).get("match")
        if (
            recognized.get("status_code") != 200
            or not isinstance(matches, list)
            or number not in {
                item.get("number") for item in matches if isinstance(item, dict)
            }
        ):
            raise RuntimeError("FaceRec 实例 B 未精确匹配实例 A 刚创建的人物")
        listed = require_http(
            http.get(manage_endpoint + "/persons", params={"skip": 0, "limit": 100}),
            "FaceRec shared Mongo query",
        )
        listed_data = listed.get("data")
        listed_people = listed_data.get("persons") if isinstance(listed_data, dict) else None
        if (
            listed.get("status_code") != 200
            or not isinstance(listed_people, list)
            or number
            not in {
                item.get("number") for item in listed_people if isinstance(item, dict)
            }
        ):
            raise RuntimeError("FaceRec 实例 C 未查到实例 A 刚创建的人物")
        result = {"created": True, "recognized": True, "photo_saved": False, "cleanup": True}
    except BaseException as exc:
        primary_error = exc
    cleanup_error: BaseException | None = None
    if created:
        try:
            cleanup = require_http(
                http.request(
                    "DELETE", manage_endpoint + "/persons/delete", json={"number": number}
                ),
                "FaceRec cleanup",
            )
            if cleanup.get("status_code") != 200:
                raise RuntimeError("FaceRec 测试人物清理失败")
            after_cleanup = require_http(
                http.get(manage_endpoint + "/persons", params={"skip": 0, "limit": 100}),
                "FaceRec cleanup verify",
            )
            people_data = after_cleanup.get("data")
            people = people_data.get("persons") if isinstance(people_data, dict) else []
            if isinstance(people, list) and number in {
                item.get("number") for item in people if isinstance(item, dict)
            }:
                raise RuntimeError("FaceRec 测试人物清理后仍存在")
        except BaseException as exc:
            cleanup_error = exc
    if primary_error is not None and cleanup_error is not None:
        raise RuntimeError(f"{primary_error}；清理失败: {cleanup_error}")
    if primary_error is not None:
        raise primary_error
    if cleanup_error is not None:
        raise cleanup_error
    if result is None:
        raise RuntimeError("FaceRec Smoke 未产生结果")
    return result


def smoke_screen_det(
    http: httpx.Client,
    endpoint: str,
    fixtures: dict[str, Path],
    _: float,
    *,
    activity: ActivityEmitter | None = None,
) -> dict[str, Any]:
    image = base64.b64encode(fixtures["screen_det_image"].read_bytes()).decode()
    with activity_window(activity):
        response = http.post(
            endpoint.rstrip("/") + "/detect_all",
            json={"image": image},
        )
    body = require_http(
        response,
        "ScreenDet",
    )
    required = {"tilt", "screen", "quality_abnormal", "occlusion"}
    executed = body.get("executed_modules")
    if body.get("code") != 200 or not isinstance(executed, list):
        raise RuntimeError("ScreenDet detect_all 响应合同失败")
    if body.get("failed_modules") != []:
        raise RuntimeError("ScreenDet failed_modules 必须为空")
    if set(executed) != required:
        raise RuntimeError("ScreenDet 未执行全部要求模块")
    for module in sorted(required):
        part = body.get(module)
        if not isinstance(part, dict) or part.get("code") != 200:
            raise RuntimeError(f"ScreenDet 模块 {module} 未成功")
    return {
        "executed_modules": body["executed_modules"],
        "failed_module_count": len(body.get("failed_modules") or []),
    }


def smoke_ppt(
    http: httpx.Client,
    endpoint: str,
    fixtures: dict[str, Path],
    timeout: float,
    *,
    callback_listen_host: str,
    callback_advertise_base_url: str,
    result_root: Path,
    task_id: str,
    operator_task_id: str,
    mark_submitted: Callable[[str, str], None],
    activity: ActivityEmitter | None = None,
) -> dict[str, Any]:
    with CallbackCapture(
        listen_host=callback_listen_host,
        advertise_base_url=callback_advertise_base_url,
    ) as callback:
        with activity_window(activity):
            request = http.build_request(
                "POST",
                endpoint.rstrip("/") + "/LocalVideoPPTSliceTasks/v1.0.0",
                json={
                    "video_path": str(fixtures["ppt_video"]),
                    "task_id": task_id,
                    "operator_task_id": operator_task_id,
                    "result_callback_uri": callback.url,
                    "threshold": 0.98,
                },
            )
            mark_submitted(task_id, operator_task_id)
            accepted_response = http.send(request)
        accepted = require_http(
            accepted_response,
            "PPT Slice",
        )
        if (
            accepted.get("status") != 50
            or accepted.get("task_id") != task_id
            or accepted.get("operator_task_id") != operator_task_id
        ):
            raise RuntimeError("PPT Slice 未受理任务")
        if not callback.event.wait(timeout):
            raise RuntimeError("PPT Slice status 50 后未收到终态回调")
        terminal = callback.payload or {}
        if (
            terminal.get("status") != 60
            or terminal.get("task_id") != task_id
            or terminal.get("operator_task_id") != operator_task_id
        ):
            raise RuntimeError("PPT Slice 终态回调不是成功终态")
        manifest = Path(str(terminal.get("manifest_path", "")))
        expected_manifest = result_root / task_id / "ppt" / "manifest.json"
        if Path(os.path.abspath(manifest)) != Path(os.path.abspath(expected_manifest)):
            raise RuntimeError("PPT manifest 不在当前 Smoke 任务的精确位置")
        reject_symlink_chain(manifest, "PPT manifest")
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        count = terminal.get("count")
        images = payload.get("images") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or payload.get("task_id") != task_id
            or payload.get("operator_task_id") != operator_task_id
            or not isinstance(count, int)
            or count < 0
            or not isinstance(images, list)
            or len(images) != count
        ):
            raise RuntimeError("PPT manifest images 与终态 count 不一致")
        slices_root = expected_manifest.parent / "slices"
        for item in images:
            image_path = Path(str(item.get("path", ""))) if isinstance(item, dict) else Path()
            if (
                not isinstance(item, dict)
                or set(item) != {"frame_seq", "snap_time", "path"}
                or not isinstance(item["frame_seq"], int)
                or not isinstance(item["snap_time"], int)
                or not inside(image_path, slices_root)
                or not image_path.is_file()
            ):
                raise RuntimeError("PPT 切片图片越出当前 Smoke 任务的 slices 目录")
            reject_symlink_chain(image_path, "PPT 切片图片")
        return {
            "task_id": task_id,
            "operator_task_id": operator_task_id,
            "terminal_status": 60,
            "slide_count": count,
            "manifest_verified": True,
        }


RUNNERS: dict[str, Callable[..., dict[str, Any]]] = {
    "asr_offline": smoke_asr_offline,
    "asr_online": smoke_asr_online,
    "ocr": smoke_ocr,
    "vbas": smoke_vbas,
    "facerec": smoke_facerec,
    "screen_det": smoke_screen_det,
    "ppt_slice": smoke_ppt,
}
INSTANCE_PREFIXES = {
    entry.operator_code: f"{entry.service_prefix}-"
    for entry in CURRENT_TOPOLOGY.operators
}


def make_case(
    case: dict[str, Any],
    *,
    status: str,
    started: str,
    finished: str,
    reason: str,
    evidence: list[str],
    mock: bool,
    tag: str,
    sha: str,
    command: str,
) -> dict[str, Any]:
    code = case["operator_code"]
    return {
        "case_id": case["case_id"],
        "status": status,
        "started_at": started,
        "finished_at": finished,
        "target": code,
        "command": command,
        "evidence": evidence,
        "reason": reason,
        "mock": mock,
        "release_tag": tag,
        "git_sha": sha,
    }


def reproduction_command(
    args: argparse.Namespace,
    *,
    code: str,
    tag: str,
    sha: str,
) -> str:
    command: list[str] = [
        "deploy/scripts/run-operator-smoke",
        "--release-tag",
        tag,
        "--git-sha",
        sha,
        "--reports-root",
        str(args.reports_root),
        "--fixture-manifest",
        str(args.fixture_manifest),
        "--external-fixture-root",
        str(args.external_fixture_root),
        "--fixture-target-root",
        str(args.fixture_target_root),
        "--result-root",
        str(args.result_root),
        "--endpoints-json",
        str(args.endpoints_json),
        "--case-manifest",
        str(args.case_manifest),
        "--timeout-seconds",
        str(args.timeout_seconds),
    ]
    if args.operator is not None:
        command.extend(("--operator", code, "--instance", str(args.instance)))
    else:
        command.extend(("--cases", code))
    command.extend(("--run-id", "auto"))
    if args.repeat != 1:
        command.extend(("--repeat", str(args.repeat)))
    if args.hold_seconds:
        command.extend(("--hold-seconds", str(args.hold_seconds)))
    if args.callback_advertise_base_url is not None:
        command.extend(
            (
                "--callback-listen-host",
                str(args.callback_listen_host),
                "--callback-advertise-base-url",
                str(args.callback_advertise_base_url),
            )
        )
    if args.mock:
        command.append("--mock")
    return shlex.join(command)


def main() -> int:
    args = parse_args()
    try:
        tag = safe_component(args.release_tag, TAG_PATTERN, "release tag")
        sha = safe_component(args.git_sha.lower(), SHA_PATTERN, "Git SHA")
        if args.timeout_seconds <= 0:
            raise ValueError("命令超时必须大于 0")
        if args.repeat <= 0:
            raise ValueError("repeat 必须大于 0")
        if args.hold_seconds < 0:
            raise ValueError("hold-seconds 不能为负数")
        if (args.operator is None) != (args.instance is None):
            raise ValueError("--operator 与 --instance 必须同时指定")
        instance_id = (
            safe_component(args.instance, TAG_PATTERN, "instance ID")
            if args.instance is not None
            else None
        )
        run_id = resolve_run_id(args.run_id)
        if instance_id is not None and run_id is None:
            raise ValueError("逐实例 Smoke 必须指定 --run-id 以隔离追加证据")
        endpoints = load_endpoints(args.endpoints_json)
        all_cases = load_cases(args.case_manifest)
        if args.operator is not None:
            if args.cases not in {"all", args.operator}:
                raise ValueError("--operator 与 --cases 选择冲突")
            selected_codes = {args.operator}
        else:
            selected_codes = set(RUNNERS) if args.cases == "all" else set(args.cases.split(","))
        unknown = selected_codes - set(RUNNERS)
        if unknown:
            raise ValueError(f"未知 Smoke case: {sorted(unknown)}")
        if instance_id is not None:
            selected_code = next(iter(selected_codes))
            if not instance_id.startswith(INSTANCE_PREFIXES[selected_code]):
                raise ValueError("实例 ID 与算子不匹配")
        selected = [case for case in all_cases if case["operator_code"] in selected_codes]
        activity_channel = load_activity_channel()
        if activity_channel is not None and (
            args.operator is None or instance_id is None or run_id is None or len(selected) != 1
        ):
            raise ValueError("GPU activity 通道只支持单个逐实例 Smoke")
        resolved_endpoints: dict[str, Any] = {}
        targets: dict[str, str] = {}
        activity_origins: dict[str, str] = {}
        for case in selected:
            code = case["operator_code"]
            endpoint, target = resolve_endpoint(endpoints, code, instance_id)
            resolved_endpoints[code] = endpoint
            targets[code] = target
            if case["operator_code"] == "facerec":
                if not isinstance(endpoint, list) or len(endpoint) != 3:
                    raise ValueError("FaceRec Smoke 必须配置三个不同实例")
                try:
                    origins = {normalized_http_origin(item) for item in endpoint}
                except ValueError as error:
                    raise ValueError(
                        "FaceRec Smoke 必须配置三个不同实例"
                    ) from error
                if len(origins) != 3:
                    raise ValueError("FaceRec Smoke 必须配置三个不同实例")
                activity_origins[code] = normalized_target_origin(endpoint[1])
                continue
            parsed = urlsplit(str(endpoint))
            allowed = {"ws", "wss"} if case["operator_code"] == "asr_online" else {"http", "https"}
            if (
                parsed.scheme not in allowed
                or not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
                or bool(parsed.query)
                or bool(parsed.fragment)
            ):
                raise ValueError(f"{case['operator_code']} endpoint 协议不合法")
            activity_origins[code] = normalized_target_origin(endpoint)
        if "ppt_slice" in selected_codes:
            if args.callback_advertise_base_url is None:
                raise ValueError("PPT Slice Smoke 必须指定 --callback-advertise-base-url")
            validate_callback_advertise_base_url(args.callback_advertise_base_url)
        needed = {fixture for case in selected for fixture in case["fixtures"]}
        fixtures, missing_fixtures = load_and_stage_fixtures(
            args.fixture_manifest,
            args.external_fixture_root,
            args.fixture_target_root,
            required_fixture_ids=needed,
        )
        undeclared = needed - set(fixtures) - set(missing_fixtures)
        if undeclared:
            raise ValueError(f"fixture manifest 未声明: {sorted(undeclared)}")
        release_root = args.reports_root / "milestone-2b" / "releases" / tag / sha
        smoke_root = release_root / "smoke"
        if instance_id is not None:
            smoke_root = smoke_root / "instances" / instance_id / "runs" / str(run_id)
        elif run_id is not None:
            smoke_root = smoke_root / "runs" / run_id
        results: list[dict[str, Any]] = []
        failed = False
        with httpx.Client(timeout=args.timeout_seconds, follow_redirects=False) as http:
            for case in selected:
                started = utc_now()
                code = case["operator_code"]
                report_case = {**case, "operator_code": targets[code]}
                command = reproduction_command(
                    args,
                    code=code,
                    tag=tag,
                    sha=sha,
                )
                evidence_path = smoke_root / f"{code}.json"
                relative = evidence_path.relative_to(release_root).as_posix()
                unavailable = [
                    missing_fixtures[fixture]
                    for fixture in case["fixtures"]
                    if fixture in missing_fixtures
                ]
                if unavailable:
                    failed = True
                    reason = "；".join(unavailable)
                    atomic_json(
                        evidence_path,
                        {
                            "schema_version": 1,
                            "evidence_type": "operator_smoke",
                            "operator_code": code,
                            "target": targets[code],
                            "checks": case["checks"],
                            "status": "未执行及原因",
                            "reason": reason,
                            "mock": args.mock,
                            "release_tag": tag,
                            "git_sha": sha,
                        },
                    )
                    results.append(
                        make_case(
                            report_case,
                            status="未执行及原因",
                            started=started,
                            finished=utc_now(),
                            reason=reason,
                            evidence=[],
                            mock=args.mock,
                            tag=tag,
                            sha=sha,
                            command=command,
                        )
                    )
                    print(f"{code} Smoke 未执行: {reason}", file=sys.stderr)
                    continue
                attempts: list[dict[str, Any]] = []
                ppt_attempt_context: dict[str, str] | None = None
                try:
                    endpoint_value = (
                        json.dumps(resolved_endpoints[code])
                        if code == "facerec"
                        else str(resolved_endpoints[code])
                    )
                    hold_deadline = time.monotonic() + args.hold_seconds
                    while len(attempts) < args.repeat or time.monotonic() < hold_deadline:
                        ppt_attempt_context = None
                        attempt_number = len(attempts) + 1
                        attempt_activity = (
                            bind_activity(
                                activity_channel,
                                operator_code=code,
                                instance_id=str(instance_id),
                                run_id=str(run_id),
                                attempt=attempt_number,
                                target_origin=activity_origins[code],
                            )
                            if activity_channel is not None
                            else None
                        )
                        if code == "ppt_slice":
                            allocated_context = {
                                "task_id": "harness-ppt-" + uuid.uuid4().hex,
                                "operator_task_id": (
                                    "harness-ppt-operator-" + uuid.uuid4().hex
                                ),
                            }

                            def mark_ppt_submitted(
                                task_id: str, operator_task_id: str
                            ) -> None:
                                nonlocal ppt_attempt_context
                                ppt_attempt_context = {
                                    "task_id": task_id,
                                    "operator_task_id": operator_task_id,
                                }

                            attempt = smoke_ppt(
                                http,
                                endpoint_value,
                                fixtures,
                                args.timeout_seconds,
                                callback_listen_host=args.callback_listen_host,
                                callback_advertise_base_url=str(
                                    args.callback_advertise_base_url
                                ),
                                result_root=args.result_root,
                                task_id=allocated_context["task_id"],
                                operator_task_id=allocated_context["operator_task_id"],
                                mark_submitted=mark_ppt_submitted,
                                activity=attempt_activity,
                            )
                            ppt_attempt_context = None
                        else:
                            attempt = RUNNERS[code](
                                http,
                                endpoint_value,
                                fixtures,
                                args.timeout_seconds,
                                activity=attempt_activity,
                            )
                        attempts.append(attempt)
                    summary = {
                        **attempts[-1],
                        "repeat": args.repeat,
                        "attempt_count": len(attempts),
                        "hold_seconds": args.hold_seconds,
                        "attempts": attempts,
                    }
                    evidence_payload = {
                        "schema_version": 1,
                        "evidence_type": "operator_smoke",
                        "operator_code": code,
                        "status": "PASS",
                        "target": targets[code],
                        "checks": case["checks"],
                        "summary": summary,
                        "mock": args.mock,
                        "release_tag": tag,
                        "git_sha": sha,
                    }
                    atomic_json(evidence_path, evidence_payload)
                    results.append(
                        make_case(
                            report_case,
                            status="通过",
                            started=started,
                            finished=utc_now(),
                            reason="直接调用响应符合算子合同",
                            evidence=[relative],
                            mock=args.mock,
                            tag=tag,
                            sha=sha,
                            command=command,
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - one failed case must not hide other evidence
                    failed = True
                    reason = str(exc)
                    failure_evidence: dict[str, Any] = {
                        "schema_version": 1,
                        "evidence_type": "operator_smoke",
                        "operator_code": code,
                        "target": targets[code],
                        "checks": case["checks"],
                        "status": "失败",
                        "reason": reason,
                        "mock": args.mock,
                        "release_tag": tag,
                        "git_sha": sha,
                    }
                    if code == "ppt_slice":
                        failure_attempts = [*attempts]
                        if ppt_attempt_context is not None:
                            failure_attempts.append(
                                {
                                    **ppt_attempt_context,
                                    "status": "失败",
                                    "reason": reason,
                                }
                            )
                        if failure_attempts:
                            failure_evidence["summary"] = {
                                **failure_attempts[-1],
                                "repeat": args.repeat,
                                "attempt_count": len(failure_attempts),
                                "hold_seconds": args.hold_seconds,
                                "attempts": failure_attempts,
                            }
                    atomic_json(
                        evidence_path,
                        failure_evidence,
                    )
                    results.append(
                        make_case(
                            report_case,
                            status="失败",
                            started=started,
                            finished=utc_now(),
                            reason=reason,
                            evidence=[relative],
                            mock=args.mock,
                            tag=tag,
                            sha=sha,
                            command=command,
                        )
                    )
                    print(f"{code} Smoke 失败: {reason}", file=sys.stderr)
        atomic_json(smoke_root / "cases.json", results)
        return 1 if failed else 0
    except (OSError, ValueError, json.JSONDecodeError, httpx.HTTPError) as exc:
        print(f"Smoke Harness 失败: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
