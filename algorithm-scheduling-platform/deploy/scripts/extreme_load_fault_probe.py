#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import secrets
import shlex
import stat
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zlib
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from threading import Barrier, BrokenBarrierError
from typing import Protocol, cast
from urllib.parse import quote, urlsplit

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect as websocket_connect

CONTROL_READINESS_URL = "http://127.0.0.1:18100/ops/readiness"
OPERATOR_INSTANCES_URL = "http://127.0.0.1:18100/ops/operator-instances"
OPERATOR_CAPACITY_URL = "http://127.0.0.1:18100/ops/operator-instances/snapshot"
QUEUES_URL = "http://127.0.0.1:18100/ops/queues"
ORCHESTRATOR_READINESS_URL = "http://127.0.0.1:18101/ops/readiness"
VISION_READINESS_URL = "http://127.0.0.1:18102/ready"
GATEWAY_READINESS_URL = "http://127.0.0.1:18103/ready"
GATEWAY_HEALTH_URL = "http://127.0.0.1:18103/health"
GATEWAY_METRICS_URL = "http://127.0.0.1:18103/metrics"
CONTROL_JOBS_URL = "http://127.0.0.1:18100/api/course-jobs"
GATEWAY_ASR_URL = "ws://127.0.0.1:18103/api/online/asr/stream"

READ_ONLY_ENDPOINT_ALLOWLIST = frozenset(
    {
        CONTROL_READINESS_URL,
        OPERATOR_INSTANCES_URL,
        OPERATOR_CAPACITY_URL,
        QUEUES_URL,
        ORCHESTRATOR_READINESS_URL,
        VISION_READINESS_URL,
        GATEWAY_READINESS_URL,
        GATEWAY_HEALTH_URL,
        GATEWAY_METRICS_URL,
    }
)

GPU_OPERATOR_PREFIXES: dict[str, str] = {
    "asr_offline": "asr-offline",
    "asr_online": "asr-online",
    "ocr": "ocr",
    "vbas": "vbas",
    "facerec": "facerec",
    "screen_det": "screen-det",
}
ALL_OPERATOR_PREFIXES = {**GPU_OPERATOR_PREFIXES, "ppt_slice": "ppt-slice"}

_OPERATOR_PROJECT = "algorithm-operators"
_PLATFORM_PROJECT = "algorithm-scheduling-platform"
_FULL_CONTAINER_ID = re.compile(r"[0-9a-f]{64}")
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_CHALLENGE = re.compile(r"[0-9a-f]{32}")
_GIT_SHA = re.compile(r"[0-9a-f]{40}")
_SERVICE = re.compile(r"[a-z0-9][a-z0-9-]{0,127}")
_MAX_HTTP_BYTES = 4 * 1024 * 1024
_MAX_WITNESS_CONCURRENCY = 1000
_EVIDENCE_REFERENCE = re.compile(
    r"release:(?P<path>[A-Za-z0-9_.\-/]{1,512})#sha256:(?P<sha>[0-9a-f]{64})"
)


class ProbeValidationError(ValueError):
    pass


def _parse_utc_timestamp(value: object, field: str) -> datetime:
    if type(value) is not str:
        raise ProbeValidationError(f"{field} must be an ISO UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ProbeValidationError(f"{field} must be an ISO UTC timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ProbeValidationError(f"{field} must be an aware UTC timestamp")
    return parsed


class ReadOnlyHttpClient(Protocol):
    def get_json(self, url: str) -> object: ...

    def get_text(self, url: str) -> str: ...

    def post_json(
        self,
        url: str,
        payload: Mapping[str, object],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> HttpObservation: ...

    def probe_asr_lease(self, trace_id: str) -> Mapping[str, object]: ...

    def probe_asr_leases(
        self,
        trace_ids: Sequence[str],
    ) -> tuple[Mapping[str, object], ...]: ...

    def prepare_persistent_asr(
        self,
        request: ProbeRequest,
        trace_id: str,
    ) -> Mapping[str, object]: ...

    def persistent_asr_state(
        self,
        request: ProbeRequest,
        trace_id: str,
    ) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class HttpObservation:
    status_code: int
    body: object


@dataclass(frozen=True, slots=True)
class TargetIdentity:
    compose_project: str
    compose_service: str
    container_id: str

    @classmethod
    def parse(cls, value: str) -> TargetIdentity:
        parts = value.split(":")
        if len(parts) != 3:
            raise ProbeValidationError("target must contain exact project:service:container_id")
        return cls(parts[0], parts[1], parts[2])

    def validate(self) -> None:
        if self.compose_project not in {_OPERATOR_PROJECT, _PLATFORM_PROJECT}:
            raise ProbeValidationError("target Compose project is not authoritative")
        if _SERVICE.fullmatch(self.compose_service) is None:
            raise ProbeValidationError("target Compose service is not exact")
        if _FULL_CONTAINER_ID.fullmatch(self.container_id) is None:
            raise ProbeValidationError("target container identity must be a full ID")

    def to_dict(self) -> dict[str, str]:
        return {
            "container_id": self.container_id,
            "compose_project": self.compose_project,
            "compose_service": self.compose_service,
        }


@dataclass(frozen=True, slots=True)
class ScenarioContract:
    case_id: str
    scenario_id: str
    services: frozenset[str]
    operator_code: str | None = None
    gpu_index: int | None = None

    @property
    def external_check_index(self) -> int:
        return 2


def _scenario_contracts() -> dict[str, ScenarioContract]:
    contracts: dict[str, ScenarioContract] = {}
    operator_codes = (
        "asr_offline",
        "asr_online",
        "ocr",
        "vbas",
        "facerec",
        "screen_det",
        "ppt_slice",
    )
    for index, code in enumerate(operator_codes, start=1):
        scenario_id = f"fault-operator-{index:02d}-{code.replace('_', '-')}"
        prefix = ALL_OPERATOR_PREFIXES[code]
        device = "cpu" if code == "ppt_slice" else "gpu"
        contracts[scenario_id] = ScenarioContract(
            case_id=f"RECOVERY-OPERATOR-{code.replace('_', '-').upper()}",
            scenario_id=scenario_id,
            services=frozenset(f"{prefix}-{device}{item}" for item in range(3)),
            operator_code=code,
        )
    for gpu_index in range(3):
        scenario_id = f"fault-gpu-{gpu_index}"
        contracts[scenario_id] = ScenarioContract(
            case_id=f"RECOVERY-GPU-{gpu_index}",
            scenario_id=scenario_id,
            services=frozenset(
                f"{prefix}-gpu{gpu_index}" for prefix in GPU_OPERATOR_PREFIXES.values()
            ),
            gpu_index=gpu_index,
        )
    platform = (
        ("CONTROL", "control-service"),
        ("ORCHESTRATOR", "orchestrator-service"),
        ("VISION", "vision-orchestrator-service"),
        ("ONLINE-GATEWAY", "online-gateway-service"),
    )
    for index, (case_suffix, service) in enumerate(platform, start=1):
        scenario_id = f"fault-platform-{index:02d}"
        contracts[scenario_id] = ScenarioContract(
            case_id=f"RECOVERY-PLATFORM-{case_suffix}",
            scenario_id=scenario_id,
            services=frozenset({service}),
        )
    contracts["fault-kafka"] = ScenarioContract(
        "RECOVERY-KAFKA", "fault-kafka", frozenset({"kafka"})
    )
    contracts["fault-redis"] = ScenarioContract(
        "RECOVERY-REDIS", "fault-redis", frozenset({"redis"})
    )
    return contracts


_SCENARIOS = _scenario_contracts()


def _reject_symlink_chain(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            raise ProbeValidationError(f"path contains symlink: {current}")


def _validate_roots(release_root: Path, evidence_root: Path) -> tuple[Path, Path]:
    release = release_root.absolute()
    evidence = evidence_root.absolute()
    if not release_root.is_absolute() or not evidence_root.is_absolute():
        raise ProbeValidationError("release root and evidence root must be absolute")
    if (
        release.parent.parent.name != "releases"
        or _SAFE_IDENTIFIER.fullmatch(release.parent.name) is None
        or _GIT_SHA.fullmatch(release.name) is None
    ):
        raise ProbeValidationError("release root must end with releases/<tag>/<git-sha>")
    try:
        relative = evidence.relative_to(release)
    except ValueError as error:
        raise ProbeValidationError("evidence root must stay inside the release root") from error
    if not relative.parts:
        raise ProbeValidationError("evidence root must be a strict release descendant")
    _reject_symlink_chain(release)
    _reject_symlink_chain(evidence)
    if not release.is_dir():
        raise ProbeValidationError("release root must already exist as a directory")
    return release, evidence


def _holder_command_tokens(holder_pid: int) -> tuple[str, ...]:
    try:
        payload = Path(f"/proc/{holder_pid}/cmdline").read_bytes()
    except OSError:
        try:
            completed = subprocess.run(
                ("ps", "-p", str(holder_pid), "-o", "command="),
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ProbeValidationError("maintenance lock holder command is unavailable") from error
        return tuple(shlex.split(completed.stdout.strip()))
    try:
        return tuple(
            item.decode("utf-8", errors="strict")
            for item in payload.split(b"\0")
            if item
        )
    except UnicodeError as error:
        raise ProbeValidationError("maintenance lock holder command is not UTF-8") from error


def _holder_working_directory(holder_pid: int) -> Path:
    try:
        return Path(os.readlink(f"/proc/{holder_pid}/cwd")).resolve(strict=True)
    except OSError:
        try:
            completed = subprocess.run(
                ("lsof", "-a", "-p", str(holder_pid), "-d", "cwd", "-Fn"),
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ProbeValidationError("maintenance lock holder cwd is unavailable") from error
        paths = [Path(line[1:]) for line in completed.stdout.splitlines() if line.startswith("n")]
        if len(paths) != 1:
            raise ProbeValidationError("maintenance lock holder cwd is ambiguous") from None
        return paths[0].resolve(strict=True)


def _holder_has_open_inode(holder_pid: int, lock_metadata: os.stat_result) -> bool:
    descriptor_root = Path(f"/proc/{holder_pid}/fd")
    try:
        descriptors = tuple(descriptor_root.iterdir())
    except OSError:
        try:
            completed = subprocess.run(
                ("lsof", "-a", "-p", str(holder_pid), "-Fn"),
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ProbeValidationError(
                "maintenance lock holder descriptors are unavailable"
            ) from error
        for line in completed.stdout.splitlines():
            if not line.startswith("n"):
                continue
            try:
                metadata = os.stat(line[1:])
            except OSError:
                continue
            if (metadata.st_dev, metadata.st_ino) == (
                lock_metadata.st_dev,
                lock_metadata.st_ino,
            ):
                return True
        return False
    for descriptor in descriptors:
        try:
            metadata = descriptor.stat()
        except OSError:
            continue
        if (metadata.st_dev, metadata.st_ino) == (
            lock_metadata.st_dev,
            lock_metadata.st_ino,
        ):
            return True
    return False


def _validate_lock_binding(
    release_root: Path,
    holder_pid: int,
    lock_path: Path,
    *,
    check_held: bool,
) -> None:
    if type(holder_pid) is not int or holder_pid <= 0:
        raise ProbeValidationError("maintenance lock holder PID is invalid")
    if (
        not lock_path.is_absolute()
        or lock_path.name != ".operator-lifecycle.lock"
        or lock_path.parent != release_root.parent
    ):
        raise ProbeValidationError("maintenance lock path is not bound to the release tag")
    _reject_symlink_chain(lock_path)
    try:
        root_metadata = os.lstat(lock_path.parent)
        lock_metadata = os.lstat(lock_path)
    except OSError as error:
        raise ProbeValidationError("maintenance lock holder or path is unavailable") from error
    if (
        not stat.S_ISDIR(root_metadata.st_mode)
        or root_metadata.st_uid != os.getuid()
        or stat.S_IMODE(root_metadata.st_mode) & 0o022
        or stat.S_ISLNK(lock_metadata.st_mode)
        or not stat.S_ISREG(lock_metadata.st_mode)
        or lock_metadata.st_uid != os.getuid()
        or stat.S_IMODE(lock_metadata.st_mode) != 0o600
        or lock_metadata.st_nlink != 1
    ):
        raise ProbeValidationError("maintenance lock ownership or mode is invalid")
    if not check_held:
        return
    try:
        os.kill(holder_pid, 0)
    except OSError as error:
        raise ProbeValidationError("maintenance lock holder is unavailable") from error
    tokens = _holder_command_tokens(holder_pid)
    try:
        script_index = next(
            index
            for index, token in enumerate(tokens)
            if Path(token).name == "operator_lifecycle.py"
        )
    except StopIteration as error:
        raise ProbeValidationError(
            "maintenance lock holder command is not authoritative"
        ) from error
    arguments = tokens[script_index:]
    if len(arguments) != 6 or arguments[1] != "hold-lock":
        raise ProbeValidationError("maintenance lock holder arguments are not authoritative")
    option_values = dict(zip(arguments[2::2], arguments[3::2], strict=True))
    if option_values != {
        "--release-tag-root": str(lock_path.parent),
        "--lock-path": str(lock_path),
    }:
        raise ProbeValidationError("maintenance lock holder is bound to another release")
    raw_script = Path(arguments[0])
    holder_script = (
        raw_script
        if raw_script.is_absolute()
        else _holder_working_directory(holder_pid) / raw_script
    ).resolve(strict=True)
    expected_script = Path(__file__).resolve().with_name("operator_lifecycle.py")
    if holder_script != expected_script:
        raise ProbeValidationError("maintenance lock holder script path is not authoritative")
    if not _holder_has_open_inode(holder_pid, lock_metadata):
        raise ProbeValidationError("maintenance lock holder has not opened the canonical inode")
    descriptor = os.open(lock_path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (
            lock_metadata.st_dev,
            lock_metadata.st_ino,
        ):
            raise ProbeValidationError("maintenance lock inode changed while opening")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        raise ProbeValidationError("maintenance lock is not currently held")
    finally:
        os.close(descriptor)


@dataclass(frozen=True, slots=True)
class ProbeRequest:
    campaign_id: str
    case_id: str
    scenario_id: str
    phase: str
    check_index: int
    challenge: str
    targets: tuple[TargetIdentity, ...]
    release_root: Path
    evidence_root: Path
    lock_holder_pid: int
    lock_path: Path
    short_teacher_video_url: str
    long_teacher_video_url: str
    long_slides_video_url: str
    fault_window_token: str | None
    fault_window_opened_at: str | None
    baseline_ref: str | None
    action_ref: str | None

    @classmethod
    def build(
        cls,
        *,
        campaign_id: str,
        case_id: str,
        scenario_id: str,
        phase: str,
        check_index: int,
        challenge: str,
        targets: Sequence[TargetIdentity],
        release_root: Path,
        evidence_root: Path,
        lock_holder_pid: int,
        lock_path: Path,
        short_teacher_video_url: str,
        long_teacher_video_url: str,
        long_slides_video_url: str,
        fault_window_token: str | None = None,
        fault_window_opened_at: str | None = None,
        baseline_ref: str | None = None,
        action_ref: str | None = None,
    ) -> ProbeRequest:
        if _SAFE_IDENTIFIER.fullmatch(campaign_id) is None:
            raise ProbeValidationError("campaign identity is invalid")
        contract = _SCENARIOS.get(scenario_id)
        if contract is None or contract.case_id != case_id:
            raise ProbeValidationError("case and scenario identity do not match")
        if phase not in {"baseline", "action", "disruption", "recovery"}:
            raise ProbeValidationError("phase must be baseline, action, disruption or recovery")
        expected_index = 0 if phase in {"baseline", "action"} else contract.external_check_index
        if type(check_index) is not int or check_index != expected_index:
            raise ProbeValidationError("check index is not the semantic check")
        if _CHALLENGE.fullmatch(challenge) is None:
            raise ProbeValidationError("challenge must be a fresh 128-bit lowercase hex value")
        exact_targets = tuple(targets)
        if not exact_targets:
            raise ProbeValidationError("target set cannot be empty")
        for target in exact_targets:
            target.validate()
        services = {target.compose_service for target in exact_targets}
        expected_count = 6 if contract.gpu_index is not None else 1
        if len(exact_targets) != expected_count or len(services) != expected_count:
            raise ProbeValidationError("target set cardinality is not authoritative")
        if contract.gpu_index is not None:
            if services != contract.services:
                raise ProbeValidationError("target GPU service set is not authoritative")
        elif not services <= contract.services:
            raise ProbeValidationError("target service does not match the scenario")
        expected_project = (
            _OPERATOR_PROJECT
            if contract.operator_code is not None or contract.gpu_index is not None
            else _PLATFORM_PROJECT
        )
        if any(target.compose_project != expected_project for target in exact_targets):
            raise ProbeValidationError("target project does not match the scenario")
        container_ids = [target.container_id for target in exact_targets]
        if len(container_ids) != len(set(container_ids)):
            raise ProbeValidationError("target container IDs must be unique")
        release, evidence = _validate_roots(release_root, evidence_root)
        _validate_lock_binding(release, lock_holder_pid, lock_path, check_held=False)
        media_urls = (
            short_teacher_video_url,
            long_teacher_video_url,
            long_slides_video_url,
        )
        for value in media_urls:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise ProbeValidationError(
                    "fault witness media must be an HTTP/HTTPS URL without credentials"
                )
        if phase == "baseline":
            if fault_window_token is not None or fault_window_opened_at is not None:
                raise ProbeValidationError("baseline probe cannot bind to a fault window")
        else:
            if (
                fault_window_token is None
                or _CHALLENGE.fullmatch(fault_window_token) is None
            ):
                raise ProbeValidationError(
                    "fault window token must be a fresh 128-bit lowercase hex value"
                )
            _parse_utc_timestamp(fault_window_opened_at, "fault_window_opened_at")
        if phase == "baseline" and (baseline_ref is not None or action_ref is not None):
            raise ProbeValidationError("baseline probe cannot consume an earlier baseline")
        if baseline_ref is not None and _EVIDENCE_REFERENCE.fullmatch(baseline_ref) is None:
            raise ProbeValidationError("baseline evidence reference is invalid")
        if action_ref is not None and _EVIDENCE_REFERENCE.fullmatch(action_ref) is None:
            raise ProbeValidationError("action evidence reference is invalid")
        return cls(
            campaign_id,
            case_id,
            scenario_id,
            phase,
            check_index,
            challenge,
            exact_targets,
            release,
            evidence,
            lock_holder_pid,
            lock_path,
            short_teacher_video_url,
            long_teacher_video_url,
            long_slides_video_url,
            fault_window_token,
            fault_window_opened_at,
            baseline_ref,
            action_ref,
        )


@dataclass(frozen=True, slots=True)
class ProbeDecision:
    status: str
    reasons: tuple[str, ...]
    observations: Mapping[str, object]


class UrllibReadOnlyClient:
    @staticmethod
    def _allowed_read_url(url: str) -> bool:
        if url in READ_ONLY_ENDPOINT_ALLOWLIST:
            return True
        parsed = urlsplit(url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.port != 18100
            or parsed.query
            or parsed.fragment
        ):
            return False
        if re.fullmatch(r"/api/course-jobs/[A-Za-z0-9_.-]{1,200}", parsed.path):
            return True
        return (
            re.fullmatch(
                r"/ops/operator-instances/[a-z0-9][a-z0-9-]{0,127}/active-leases",
                parsed.path,
            )
            is not None
        )

    def _read(self, url: str) -> bytes:
        if not self._allowed_read_url(url):
            raise ProbeValidationError("HTTP endpoint is outside the read-only allowlist")
        request = urllib.request.Request(
            url,
            method="GET",
            headers={"Accept": "application/json, text/plain"},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
                content = response.read(_MAX_HTTP_BYTES + 1)
        except (OSError, urllib.error.HTTPError) as error:
            raise RuntimeError("read-only operational endpoint is unavailable") from error
        if len(content) > _MAX_HTTP_BYTES:
            raise RuntimeError("read-only operational response exceeds the size limit")
        return cast(bytes, content)

    def get_json(self, url: str) -> object:
        try:
            return json.loads(self._read(url))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise RuntimeError("read-only operational response is not JSON") from error

    def get_text(self, url: str) -> str:
        try:
            return self._read(url).decode("utf-8")
        except UnicodeError as error:
            raise RuntimeError("read-only operational response is not UTF-8") from error

    def post_json(
        self,
        url: str,
        payload: Mapping[str, object],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> HttpObservation:
        parsed = urlsplit(url)
        allowed = (
            url == CONTROL_JOBS_URL
            or (
                parsed.scheme == "http"
                and parsed.hostname == "127.0.0.1"
                and parsed.port == 18103
                and parsed.path
                in {
                    "/api/online/vbas/analyze",
                    "/api/online/face/recognize",
                    "/api/online/image-quality/detect",
                    "/api/online/ocr/recognize",
                }
                and not parsed.query
                and not parsed.fragment
            )
        )
        if not allowed:
            raise ProbeValidationError("business POST is outside northbound ports 18100/18103")
        content = canonical_json_bytes(payload)
        request_headers = {"Accept": "application/json", "Content-Type": "application/json"}
        request_headers.update(dict(headers or {}))
        request = urllib.request.Request(
            url,
            data=content,
            method="POST",
            headers=request_headers,
        )
        status_code = 0
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                status_code = response.status
                raw = response.read(_MAX_HTTP_BYTES + 1)
        except urllib.error.HTTPError as error:
            status_code = error.code
            raw = error.read(_MAX_HTTP_BYTES + 1)
        except OSError as error:
            raise RuntimeError("northbound business endpoint is unavailable") from error
        if len(raw) > _MAX_HTTP_BYTES:
            raise RuntimeError("northbound business response exceeds the size limit")
        try:
            body: object = json.loads(raw)
        except (UnicodeError, json.JSONDecodeError) as error:
            raise RuntimeError("northbound business response is not JSON") from error
        return HttpObservation(status_code, body)

    def probe_asr_lease(self, trace_id: str) -> Mapping[str, object]:
        return _probe_asr_lease(self, trace_id)

    def probe_asr_leases(
        self,
        trace_ids: Sequence[str],
    ) -> tuple[Mapping[str, object], ...]:
        return _probe_asr_leases(self, trace_ids)

    def prepare_persistent_asr(
        self,
        request: ProbeRequest,
        trace_id: str,
    ) -> Mapping[str, object]:
        return _prepare_persistent_asr(request, trace_id)

    def persistent_asr_state(
        self,
        request: ProbeRequest,
        trace_id: str,
    ) -> Mapping[str, object]:
        return _read_gateway_worker_state(_gateway_worker_state_path(request), request, trace_id)


def _mapping(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        return None
    return cast(Mapping[str, object], value)


def _ready(value: object) -> bool:
    document = _mapping(value)
    return document is not None and document.get("status") == "ready"


def _valid_queues(value: object) -> tuple[bool, int]:
    document = _mapping(value)
    if document is None:
        return False, -1
    pending = document.get("outbox_pending")
    queues = document.get("queues")
    if type(pending) is not int or pending < 0 or not isinstance(queues, list):
        return False, -1
    for item in queues:
        record = _mapping(item)
        count = record.get("count") if record is not None else None
        if type(count) is not int or count < 0:
            return False, -1
    return True, pending


def _records(value: object) -> dict[str, Mapping[str, object]] | None:
    if not isinstance(value, list):
        return None
    result: dict[str, Mapping[str, object]] = {}
    for raw in value:
        item = _mapping(raw)
        if item is None or type(item.get("instance_id")) is not str:
            return None
        instance_id = cast(str, item["instance_id"])
        if instance_id in result:
            return None
        result[instance_id] = item
    return result


def _healthy_instance(item: Mapping[str, object], code: str, gpu: int | None) -> bool:
    declared = item.get("declared_capacity")
    if (
        item.get("operator_code") != code
        or item.get("lifecycle") != "ONLINE"
        or item.get("model_ready") is not True
        or type(declared) is not int
        or declared <= 0
    ):
        return False
    labels = _mapping(item.get("labels"))
    return gpu is None or labels is not None and labels.get("gpu") == str(gpu)


def _healthy_capacity(item: Mapping[str, object], code: str) -> bool:
    declared = item.get("declared_capacity")
    active = item.get("active_lease_count")
    return (
        item.get("operator_code") == code
        and item.get("lifecycle") == "ONLINE"
        and item.get("model_ready") is True
        and type(declared) is int
        and declared > 0
        and type(active) is int
        and 0 <= active <= declared
        and item.get("capacity_mismatch") is False
    )


def _service_code(service: str) -> str | None:
    for code, prefix in ALL_OPERATOR_PREFIXES.items():
        if service.startswith(f"{prefix}-"):
            return code
    return None


def _lease_counters(metrics: str) -> dict[str, float]:
    counters: dict[str, float] = {}
    for line in metrics.splitlines():
        if not line.startswith("algorithm_capacity_lease_events_total{"):
            continue
        try:
            labels_text, raw_value = line.split("}", 1)
            labels = dict(re.findall(r'(\w+)="([^"]*)"', labels_text))
            capability = labels["capability"]
            outcome = labels["outcome"]
            instance_id = labels.get("instance_id", "none")
            value = float(raw_value.strip().split()[0])
        except (KeyError, ValueError):
            continue
        key = f"{capability}|{instance_id}|{outcome}"
        counters[key] = counters.get(key, 0.0) + value
    return counters


def _active_leases_for_instance(
    client: ReadOnlyHttpClient,
    instance_id: str,
) -> tuple[Mapping[str, object], ...]:
    url = (
        "http://127.0.0.1:18100/ops/operator-instances/"
        f"{quote(instance_id, safe='')}/active-leases"
    )
    document = _mapping(client.get_json(url))
    leases = document.get("leases") if document is not None else None
    if (
        document is None
        or document.get("instance_id") != instance_id
        or type(document.get("active_lease_count")) is not int
        or not isinstance(leases, list)
        or document["active_lease_count"] != len(leases)
    ):
        raise RuntimeError("active lease response is malformed")
    parsed: list[Mapping[str, object]] = []
    lease_ids: set[str] = set()
    for raw in leases:
        lease = _mapping(raw)
        lease_id = lease.get("lease_id") if lease is not None else None
        context = _mapping(lease.get("work_context")) if lease is not None else None
        if (
            lease is None
            or type(lease_id) is not str
            or not lease_id
            or lease_id in lease_ids
            or context is None
        ):
            raise RuntimeError("active lease fact is malformed or ambiguous")
        lease_ids.add(lease_id)
        parsed.append(lease)
    return tuple(parsed)


def _all_active_leases(
    client: ReadOnlyHttpClient,
    *,
    operator_code: str | None = None,
) -> tuple[Mapping[str, object], ...]:
    records = _records(client.get_json(OPERATOR_INSTANCES_URL))
    if records is None:
        raise RuntimeError("operator instance inventory is malformed")
    leases: list[Mapping[str, object]] = []
    for instance_id, instance in records.items():
        if operator_code is not None and instance.get("operator_code") != operator_code:
            continue
        leases.extend(_active_leases_for_instance(client, instance_id))
    ids = [lease.get("lease_id") for lease in leases]
    if any(type(item) is not str for item in ids) or len(ids) != len(set(ids)):
        raise RuntimeError("active lease inventory contains duplicate identities")
    return tuple(leases)


def _lease_for_trace(
    client: ReadOnlyHttpClient,
    trace_id: str,
    *,
    operator_code: str = "asr_online",
) -> Mapping[str, object] | None:
    matches = []
    for lease in _all_active_leases(client, operator_code=operator_code):
        context = _mapping(lease.get("work_context"))
        if context is not None and context.get("trace_id") == trace_id:
            matches.append(lease)
    if len(matches) > 1:
        raise RuntimeError("trace ID is bound to multiple active leases")
    return matches[0] if matches else None


def _lease_for_task(
    client: ReadOnlyHttpClient,
    task_id: str,
    operator_code: str,
) -> Mapping[str, object] | None:
    matches = []
    for lease in _all_active_leases(client, operator_code=operator_code):
        context = _mapping(lease.get("work_context"))
        if context is not None and context.get("task_id") == task_id:
            matches.append(lease)
    if len(matches) > 1:
        raise RuntimeError("task ID is bound to multiple active leases")
    return matches[0] if matches else None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _fault_window(request: ProbeRequest) -> dict[str, str] | None:
    if request.fault_window_token is None or request.fault_window_opened_at is None:
        return None
    return {
        "token": request.fault_window_token,
        "opened_at": request.fault_window_opened_at,
    }


def _is_in_fault_window(request: ProbeRequest, observed_at: object) -> bool:
    if request.fault_window_opened_at is None:
        return False
    try:
        observed = _parse_utc_timestamp(observed_at, "observed_at")
        opened = _parse_utc_timestamp(
            request.fault_window_opened_at,
            "fault_window_opened_at",
        )
    except ProbeValidationError:
        return False
    return observed >= opened


def _probe_asr_lease(
    client: ReadOnlyHttpClient,
    trace_id: str,
) -> Mapping[str, object]:
    if _SAFE_IDENTIFIER.fullmatch(trace_id) is None:
        raise ProbeValidationError("ASR witness trace ID is invalid")
    connected_at = _utc_now()
    try:
        with websocket_connect(
            GATEWAY_ASR_URL,
            additional_headers={"X-Trace-ID": trace_id},
            open_timeout=5,
            close_timeout=2,
        ) as socket:
            deadline = time.monotonic() + 5.0
            lease: Mapping[str, object] | None = None
            while lease is None and time.monotonic() < deadline:
                lease = _lease_for_trace(client, trace_id)
                if lease is None:
                    time.sleep(0.05)
            if lease is None:
                try:
                    message = socket.recv(timeout=0.2)
                except (TimeoutError, ConnectionClosed):
                    message = None
                raise RuntimeError(
                    "ASR northbound session did not acquire a trace-bound lease"
                    + ("" if message is None else ": capacity response observed")
                )
            return {
                "trace_id": trace_id,
                "lease_id": lease["lease_id"],
                "instance_id": lease["instance_id"],
                "connected_at": connected_at,
                "observed_at": _utc_now(),
            }
    except (OSError, ConnectionClosed) as error:
        raise RuntimeError("ASR northbound WebSocket is unavailable") from error


def _probe_asr_leases(
    client: ReadOnlyHttpClient,
    trace_ids: Sequence[str],
) -> tuple[Mapping[str, object], ...]:
    exact_trace_ids = tuple(trace_ids)
    if not exact_trace_ids or len(exact_trace_ids) > _MAX_WITNESS_CONCURRENCY:
        raise ProbeValidationError("ASR witness concurrency exceeds the approved bound")
    if len(set(exact_trace_ids)) != len(exact_trace_ids):
        raise ProbeValidationError("ASR witness trace IDs must be unique")
    if len(exact_trace_ids) == 1:
        return (_probe_asr_lease(client, exact_trace_ids[0]),)
    acquired = Barrier(len(exact_trace_ids))

    def hold_until_all_acquired(trace_id: str) -> Mapping[str, object]:
        if _SAFE_IDENTIFIER.fullmatch(trace_id) is None:
            raise ProbeValidationError("ASR witness trace ID is invalid")
        connected_at = _utc_now()
        try:
            with websocket_connect(
                GATEWAY_ASR_URL,
                additional_headers={"X-Trace-ID": trace_id},
                open_timeout=5,
                close_timeout=2,
            ):
                deadline = time.monotonic() + 5.0
                lease = None
                while lease is None and time.monotonic() < deadline:
                    lease = _lease_for_trace(client, trace_id)
                    if lease is None:
                        time.sleep(0.05)
                if lease is None:
                    acquired.abort()
                    raise RuntimeError("concurrent ASR session has no trace-bound lease")
                try:
                    acquired.wait(timeout=2.0)
                except BrokenBarrierError as error:
                    raise RuntimeError("concurrent ASR lease occupancy did not converge") from error
                return {
                    "trace_id": trace_id,
                    "lease_id": lease["lease_id"],
                    "instance_id": lease["instance_id"],
                    "connected_at": connected_at,
                    "observed_at": _utc_now(),
                }
        except (OSError, ConnectionClosed) as error:
            acquired.abort()
            raise RuntimeError("concurrent ASR WebSocket is unavailable") from error

    with ThreadPoolExecutor(
        max_workers=len(exact_trace_ids),
        thread_name_prefix="fault-asr-occupancy",
    ) as executor:
        futures = [
            executor.submit(hold_until_all_acquired, trace_id)
            for trace_id in exact_trace_ids
        ]
        return tuple(future.result() for future in futures)


def _gateway_worker_root(request: ProbeRequest) -> Path:
    release_binding = hashlib.sha256(str(request.release_root).encode()).hexdigest()[:24]
    return (
        Path(tempfile.gettempdir()).resolve()
        / f"algorithm-extreme-load-fault-{os.getuid()}"
        / release_binding
    )


def _gateway_worker_state_path(request: ProbeRequest) -> Path:
    return (
        _gateway_worker_root(request)
        / request.case_id.lower()
        / f"{request.scenario_id}-{request.challenge}.json"
    )


def _prepare_private_directory(path: Path) -> None:
    private_root = (
        Path(tempfile.gettempdir()).resolve()
        / f"algorithm-extreme-load-fault-{os.getuid()}"
    )
    try:
        relative = path.relative_to(private_root)
    except ValueError as error:
        raise ProbeValidationError(
            "gateway witness runtime path escaped its private root"
        ) from error
    private_root.mkdir(mode=0o700, exist_ok=True)
    current = private_root
    for part in relative.parts:
        current /= part
        if not current.exists():
            current.mkdir(mode=0o700)
        metadata = os.lstat(current)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
        ):
            raise ProbeValidationError("gateway witness runtime directory is not private")
        if stat.S_IMODE(metadata.st_mode) != 0o700:
            raise ProbeValidationError("gateway witness runtime directory mode is not 0700")


def _write_gateway_worker_state(path: Path, payload: Mapping[str, object]) -> None:
    _prepare_private_directory(path.parent)
    content = canonical_json_bytes(payload)
    temporary = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise OSError("gateway witness state write made no progress")
            offset += written
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _read_gateway_worker_state(
    path: Path,
    request: ProbeRequest,
    trace_id: str,
) -> Mapping[str, object]:
    try:
        metadata = os.lstat(path)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or metadata.st_size > 64 * 1024
        ):
            raise ProbeValidationError("gateway witness state file is not private")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            content = os.read(descriptor, 64 * 1024 + 1)
        finally:
            os.close(descriptor)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise ProbeValidationError("gateway witness state changed while opening")
        raw: object = json.loads(content)
    except FileNotFoundError:
        return {"status": "missing"}
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProbeValidationError("gateway witness state is unreadable") from error
    document = _mapping(raw)
    expected = {
        "campaign_id": request.campaign_id,
        "case_id": request.case_id,
        "scenario_id": request.scenario_id,
        "challenge": request.challenge,
        "trace_id": trace_id,
    }
    if document is None or any(document.get(key) != value for key, value in expected.items()):
        raise ProbeValidationError("gateway witness state identity is ambiguous")
    return document


def _prepare_persistent_asr(
    request: ProbeRequest,
    trace_id: str,
) -> Mapping[str, object]:
    state_path = _gateway_worker_state_path(request)
    state = _read_gateway_worker_state(state_path, request, trace_id)
    if state.get("status") == "connected":
        return state
    if state.get("status") not in {"missing", "starting"}:
        raise RuntimeError("persistent ASR witness terminated before the fault window")
    marker = state_path.with_suffix(".launch")
    _prepare_private_directory(marker.parent)
    try:
        descriptor = os.open(
            marker,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except FileExistsError:
        descriptor = -1
    if descriptor >= 0:
        os.close(descriptor)
        subprocess.Popen(  # noqa: S603
            (
                sys.executable,
                str(Path(__file__).resolve()),
                "--gateway-worker",
                "--worker-state-path",
                str(state_path),
                "--campaign-id",
                request.campaign_id,
                "--case-id",
                request.case_id,
                "--scenario-id",
                request.scenario_id,
                "--challenge",
                request.challenge,
                "--worker-trace-id",
                trace_id,
            ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
            cwd="/",
        )
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline:
        state = _read_gateway_worker_state(state_path, request, trace_id)
        if state.get("status") == "connected":
            return state
        if state.get("status") in {"failed", "disconnected", "expired"}:
            raise RuntimeError("persistent ASR witness could not stay connected")
        time.sleep(0.05)
    return {"status": "starting"}


def _gateway_worker_main(arguments: argparse.Namespace) -> int:
    state_path = Path(arguments.worker_state_path)
    worker_root = (
        Path(tempfile.gettempdir()).resolve()
        / f"algorithm-extreme-load-fault-{os.getuid()}"
    )
    try:
        state_path.relative_to(worker_root)
    except ValueError:
        return 2
    identity = {
        "campaign_id": arguments.campaign_id,
        "case_id": arguments.case_id,
        "scenario_id": arguments.scenario_id,
        "challenge": arguments.challenge,
        "trace_id": arguments.worker_trace_id,
    }
    if (
        not state_path.is_absolute()
        or state_path.suffix != ".json"
        or _SAFE_IDENTIFIER.fullmatch(arguments.campaign_id) is None
        or _SAFE_IDENTIFIER.fullmatch(arguments.case_id) is None
        or _SAFE_IDENTIFIER.fullmatch(arguments.scenario_id) is None
        or _CHALLENGE.fullmatch(arguments.challenge) is None
        or _SAFE_IDENTIFIER.fullmatch(arguments.worker_trace_id) is None
    ):
        return 2
    client = UrllibReadOnlyClient()
    try:
        with websocket_connect(
            GATEWAY_ASR_URL,
            additional_headers={"X-Trace-ID": arguments.worker_trace_id},
            open_timeout=5,
            close_timeout=2,
        ) as socket:
            deadline = time.monotonic() + 5.0
            lease = None
            while lease is None and time.monotonic() < deadline:
                lease = _lease_for_trace(client, arguments.worker_trace_id)
                if lease is None:
                    time.sleep(0.05)
            if lease is None:
                raise RuntimeError("persistent ASR session has no trace-bound lease")
            connected = {
                **identity,
                "status": "connected",
                "worker_pid": os.getpid(),
                "lease_id": lease["lease_id"],
                "instance_id": lease["instance_id"],
                "connected_at": _utc_now(),
            }
            _write_gateway_worker_state(state_path, connected)
            expires = time.monotonic() + 900.0
            while time.monotonic() < expires:
                try:
                    socket.recv(timeout=1.0)
                except TimeoutError:
                    continue
                except ConnectionClosed as error:
                    _write_gateway_worker_state(
                        state_path,
                        {
                            **connected,
                            "status": "disconnected",
                            "disconnected_at": _utc_now(),
                            "close_code": error.rcvd.code if error.rcvd is not None else None,
                        },
                    )
                    return 0
            _write_gateway_worker_state(
                state_path,
                {**connected, "status": "expired", "expired_at": _utc_now()},
            )
            return 1
    except Exception as error:
        _write_gateway_worker_state(
            state_path,
            {
                **identity,
                "status": "failed",
                "failed_at": _utc_now(),
                "error_type": type(error).__name__,
            },
        )
        return 1


_EXPECTED_NODES: dict[str, tuple[str, ...]] = {
    "ASR": ("ASR_TRANSCRIPTION",),
    "PPT": ("PPT_SLICE", "PPT_OCR"),
    "TEACHER_BEHAVIOR": ("TEACHER_BEHAVIOR_ANALYSIS",),
    "STUDENT_BEHAVIOR": ("STUDENT_BEHAVIOR_ANALYSIS",),
}
_TERMINAL_STATUSES = frozenset({60, 70, 80})


def _witness_id(request: ProbeRequest, purpose: str, attempt: int = 0) -> str:
    normalized = re.sub(r"[^a-z0-9-]+", "-", purpose.lower()).strip("-")
    value = (
        f"fault-{request.campaign_id}-{request.scenario_id}-"
        f"{request.challenge[:12]}-{normalized}-{attempt}"
    )
    if len(value) > 200 or _SAFE_IDENTIFIER.fullmatch(value) is None:
        value = f"fault-{hashlib.sha256(value.encode()).hexdigest()}"
    return value


def _business_data(observation: HttpObservation, *, expected_code: int) -> Mapping[str, object]:
    body = _mapping(observation.body)
    data = _mapping(body.get("data")) if body is not None else None
    if (
        observation.status_code != 200
        or body is None
        or body.get("code") != expected_code
        or (expected_code == 0 and data is None)
    ):
        raise RuntimeError("northbound business response did not match the expected code")
    return {} if data is None else data


def _task_fact(document: object, task_id: str, task_type: str) -> Mapping[str, object]:
    body = _mapping(document)
    data = _mapping(body.get("data")) if body is not None else None
    tasks = data.get("tasks") if data is not None else None
    if (
        body is None
        or body.get("code") != 0
        or data is None
        or data.get("task_id") != task_id
        or not isinstance(tasks, list)
    ):
        raise RuntimeError("course task query fact is malformed")
    requested: Mapping[str, object] | None = None
    seen_types: set[str] = set()
    for raw in tasks:
        task = _mapping(raw)
        raw_type = task.get("task_type") if task is not None else None
        if task is None or type(raw_type) is not str or raw_type in seen_types:
            raise RuntimeError("course task types are duplicated or malformed")
        seen_types.add(raw_type)
        if raw_type == task_type:
            requested = task
    if requested is None:
        raise RuntimeError("requested course task type is missing")
    status_value = requested.get("status")
    nodes = requested.get("nodes")
    if type(status_value) is not int or not isinstance(nodes, list):
        raise RuntimeError("requested course task status or nodes are malformed")
    node_codes: list[str] = []
    node_statuses: list[int] = []
    node_updated_at: list[str] = []
    result_digests: list[str] = []
    for raw in nodes:
        node = _mapping(raw)
        code = node.get("node_code") if node is not None else None
        node_status = node.get("status") if node is not None else None
        updated_at = node.get("updated_at") if node is not None else None
        if (
            node is None
            or type(code) is not str
            or type(node_status) is not int
            or type(updated_at) is not str
        ):
            raise RuntimeError("course task node fact is malformed")
        _parse_utc_timestamp(updated_at, "node.updated_at")
        node_codes.append(code)
        node_statuses.append(node_status)
        node_updated_at.append(updated_at)
        result = node.get("result")
        if result not in (None, "", [], {}):
            result_digests.append(
                hashlib.sha256(canonical_json_bytes({"result": result})).hexdigest()
            )
    if len(node_codes) != len(set(node_codes)):
        raise RuntimeError("course task contains duplicate DAG nodes")
    expected_nodes = _EXPECTED_NODES[task_type]
    if node_codes and tuple(node_codes) != expected_nodes:
        raise RuntimeError("course task DAG does not match the authoritative node set")
    return {
        "task_id": task_id,
        "task_type": task_type,
        "status": status_value,
        "terminal": status_value in _TERMINAL_STATUSES,
        "completed": status_value == 60,
        "node_codes": node_codes,
        "node_count": len(node_codes),
        "node_statuses": node_statuses,
        "node_updated_at": node_updated_at,
        "result_digests": result_digests,
    }


def _query_task_fact(
    client: ReadOnlyHttpClient,
    task_id: str,
    task_type: str,
) -> Mapping[str, object]:
    return _task_fact(
        client.get_json(f"http://127.0.0.1:18100/api/course-jobs/{quote(task_id, safe='')}"),
        task_id,
        task_type,
    )


def _submit_task(
    request: ProbeRequest,
    client: ReadOnlyHttpClient,
    *,
    purpose: str,
    task_type: str,
    media_url: str,
    attempt: int = 0,
) -> Mapping[str, object]:
    task_id = _witness_id(request, purpose, attempt)
    payload: dict[str, object] = {
        "task_id": task_id,
        "task_types": [task_type],
        "priority": "URGENT",
    }
    if task_type in {"ASR", "TEACHER_BEHAVIOR"}:
        payload["teacher_video_path"] = media_url
    elif task_type == "PPT":
        payload["slides_video_path"] = media_url
    else:
        payload.update({"student_video_path": media_url, "student_count": 1})
    trace_id = _witness_id(request, f"trace-{purpose}", attempt)
    accepted_at = _utc_now()
    observation = client.post_json(
        CONTROL_JOBS_URL,
        payload,
        headers={"X-Trace-ID": trace_id},
    )
    data = _business_data(observation, expected_code=0)
    if data.get("task_id") != task_id:
        raise RuntimeError("accepted task identity does not match the witness")
    fact = _query_task_fact(client, task_id, task_type)
    return {
        "task_id": task_id,
        "accepted_at": accepted_at,
        "http_status": observation.status_code,
        "business_code": 0,
        "trace_id": trace_id,
        "fact": fact,
    }


def _probe_png_base64() -> str:
    width = 256
    height = 256
    rows = b"".join(b"\x00" + b"\xff\xff\xff" * width for _ in range(height))

    def chunk(kind: bytes, content: bytes) -> bytes:
        return (
            struct.pack(">I", len(content))
            + kind
            + content
            + struct.pack(">I", zlib.crc32(kind + content) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, level=9))
        + chunk(b"IEND", b"")
    )
    return base64.b64encode(png).decode("ascii")


_IMAGE_BASE64 = _probe_png_base64()
_ONLINE_WORKLOADS: dict[str, tuple[str, str, str]] = {
    "ocr": ("ocr", "/api/online/ocr/recognize", "ocr"),
    "vbas": ("student_behavior", "/api/online/vbas/analyze", "vbas"),
    "facerec": ("recognize", "/api/online/face/recognize", "face"),
    "screen_det": ("detect_all", "/api/online/image-quality/detect", "screen"),
}


def _image_payload(kind: str, request_id: str) -> Mapping[str, object]:
    if kind == "ocr":
        return {"image_id": request_id, "image": _IMAGE_BASE64, "enable_formula": False}
    if kind == "vbas":
        return {
            "stream_type": "student",
            "ImageList": [{"ImageId": request_id, "StoragePath": _IMAGE_BASE64}],
        }
    if kind == "face":
        return {"photo": _IMAGE_BASE64}
    return {"image": _IMAGE_BASE64}


def _positive_counter_deltas(
    before: Mapping[str, float],
    after: Mapping[str, float],
    *,
    capability: str,
    outcome: str,
) -> dict[str, float]:
    suffix = f"|{outcome}"
    prefix = f"{capability}|"
    deltas: dict[str, float] = {}
    for key in set(before) | set(after):
        if not key.startswith(prefix) or not key.endswith(suffix):
            continue
        delta = after.get(key, 0.0) - before.get(key, 0.0)
        if delta > 0:
            instance_id = key[len(prefix) : -len(suffix)]
            deltas[instance_id] = delta
    return deltas


def _run_online_image_workload(
    request: ProbeRequest,
    client: ReadOnlyHttpClient,
    operator_code: str,
    *,
    attempt: int,
    expected_business_code: int = 0,
) -> Mapping[str, object]:
    capability, path, kind = _ONLINE_WORKLOADS[operator_code]
    request_id = _witness_id(request, f"{request.phase}-{operator_code}-image", attempt)
    trace_id = _witness_id(request, f"{request.phase}-{operator_code}-trace", attempt)
    before = _lease_counters(client.get_text(GATEWAY_METRICS_URL))
    started_at = _utc_now()
    observation = client.post_json(
        f"http://127.0.0.1:18103{path}",
        _image_payload(kind, request_id),
        headers={"X-Trace-ID": trace_id},
    )
    _business_data(observation, expected_code=expected_business_code)
    after = _lease_counters(client.get_text(GATEWAY_METRICS_URL))
    acquired = _positive_counter_deltas(
        before,
        after,
        capability=capability,
        outcome="acquired",
    )
    rejected = _positive_counter_deltas(
        before,
        after,
        capability=capability,
        outcome="rejected",
    )
    rejection_binding: Mapping[str, object] | None = None
    if expected_business_code == 0:
        if len(acquired) != 1 or next(iter(acquired.values())) != 1.0:
            raise RuntimeError("online workload lease routing is missing or ambiguous")
    else:
        rejection_binding = {
            "request_id": request_id,
            "trace_id": trace_id,
            "business_code": expected_business_code,
        }
    return {
        "request_id": request_id,
        "trace_id": trace_id,
        "started_at": started_at,
        "completed_at": _utc_now(),
        "http_status": observation.status_code,
        "business_code": expected_business_code,
        "rejection_binding": rejection_binding,
        "acquired_deltas": acquired,
        "rejected_deltas": rejected,
        "routed_instance": next(iter(acquired), None),
    }


def _run_online_image_batch(
    request: ProbeRequest,
    client: ReadOnlyHttpClient,
    operator_code: str,
    *,
    count: int,
    eligible_instances: set[str],
) -> Mapping[str, object]:
    if not 1 <= count <= _MAX_WITNESS_CONCURRENCY:
        raise ProbeValidationError("online witness concurrency exceeds the approved bound")
    expected_instances = _operator_services(operator_code)
    if not eligible_instances or not eligible_instances <= expected_instances:
        raise ProbeValidationError("online witness eligible instance set is invalid")
    _capability, path, kind = _ONLINE_WORKLOADS[operator_code]
    start_barrier = Barrier(count)
    request_specs = tuple(
        (
            _witness_id(
                request,
                f"{request.phase}-{operator_code}-batch-image",
                attempt,
            ),
            _witness_id(
                request,
                f"{request.phase}-{operator_code}-batch-trace",
                attempt,
            ),
        )
        for attempt in range(count)
    )
    trace_ids = {trace_id for _request_id, trace_id in request_specs}

    def send(request_id: str, trace_id: str) -> Mapping[str, object]:
        try:
            start_barrier.wait(timeout=2.0)
        except BrokenBarrierError as error:
            raise RuntimeError("online workload concurrency did not start together") from error
        started_at = _utc_now()
        observation = client.post_json(
            f"http://127.0.0.1:18103{path}",
            _image_payload(kind, request_id),
            headers={"X-Trace-ID": trace_id},
        )
        _business_data(observation, expected_code=0)
        return {
            "request_id": request_id,
            "trace_id": trace_id,
            "started_at": started_at,
            "completed_at": _utc_now(),
            "http_status": observation.status_code,
            "business_code": 0,
        }

    with ThreadPoolExecutor(
        max_workers=count,
        thread_name_prefix=f"fault-{operator_code}-occupancy",
    ) as executor:
        futures = [
            executor.submit(send, request_id, trace_id)
            for request_id, trace_id in request_specs
        ]
        active_witness: Mapping[str, object] | None = None
        deadline = time.monotonic() + 7.0
        while active_witness is None and time.monotonic() < deadline:
            matches: list[Mapping[str, object]] = []
            for instance_id in sorted(eligible_instances):
                for lease in _active_leases_for_instance(client, instance_id):
                    context = _mapping(lease.get("work_context"))
                    trace_id = context.get("trace_id") if context is not None else None
                    if type(trace_id) is str and trace_id in trace_ids:
                        matches.append(lease)
            if matches:
                bound_traces = [
                    cast(Mapping[str, object], lease["work_context"])["trace_id"]
                    for lease in matches
                ]
                if len(bound_traces) != len(set(bound_traces)):
                    raise RuntimeError("probe trace is bound to multiple active leases")
                selected = matches[0]
                active_witness = {
                    "lease_id": selected["lease_id"],
                    "instance_id": selected["instance_id"],
                    "trace_id": cast(Mapping[str, object], selected["work_context"])[
                        "trace_id"
                    ],
                    "observed_at": _utc_now(),
                }
                break
            if all(future.done() for future in futures):
                break
            time.sleep(0.005)
        requests = [future.result() for future in futures]
    if active_witness is None:
        raise RuntimeError("online workloads lack trace-bound active lease evidence")
    return {
        "request_count": count,
        "requests": requests,
        "active_lease_witness": active_witness,
    }


def _active_lease_count_snapshot(
    client: ReadOnlyHttpClient,
    operator_code: str,
) -> dict[str, int]:
    return {
        instance_id: len(_active_leases_for_instance(client, instance_id))
        for instance_id in sorted(_operator_services(operator_code))
    }


def _run_released_online_image_workload(
    request: ProbeRequest,
    client: ReadOnlyHttpClient,
    operator_code: str,
    *,
    attempt: int,
) -> Mapping[str, object]:
    capability, path, kind = _ONLINE_WORKLOADS[operator_code]
    lease_counts_before = _active_lease_count_snapshot(client, operator_code)
    counters_before = _lease_counters(client.get_text(GATEWAY_METRICS_URL))
    request_id = _witness_id(request, f"{request.phase}-{operator_code}-released", attempt)
    trace_id = _witness_id(request, f"{request.phase}-{operator_code}-released-trace", attempt)
    started_at = _utc_now()
    observation = client.post_json(
        f"http://127.0.0.1:18103{path}",
        _image_payload(kind, request_id),
        headers={"X-Trace-ID": trace_id},
    )
    _business_data(observation, expected_code=0)
    counters_after = _lease_counters(client.get_text(GATEWAY_METRICS_URL))
    lease_counts_after = _active_lease_count_snapshot(client, operator_code)
    acquired = _positive_counter_deltas(
        counters_before,
        counters_after,
        capability=capability,
        outcome="acquired",
    )
    released = _positive_counter_deltas(
        counters_before,
        counters_after,
        capability=capability,
        outcome="released",
    )
    if not acquired or not released:
        raise RuntimeError("recovery request lacks acquired and released lease deltas")
    if lease_counts_after != lease_counts_before:
        raise RuntimeError("recovery request left active leases above the pre-request baseline")
    return {
        "request_id": request_id,
        "trace_id": trace_id,
        "started_at": started_at,
        "completed_at": _utc_now(),
        "http_status": observation.status_code,
        "business_code": 0,
        "acquired_deltas": acquired,
        "released_deltas": released,
        "active_lease_counts_before": lease_counts_before,
        "active_lease_counts_after": lease_counts_after,
    }


def _operator_services(operator_code: str) -> set[str]:
    prefix = ALL_OPERATOR_PREFIXES[operator_code]
    device = "cpu" if operator_code == "ppt_slice" else "gpu"
    return {f"{prefix}-{device}{index}" for index in range(3)}


def _recovery_route_width(
    operator_code: str,
    target_services: set[str],
    capacities: Mapping[str, Mapping[str, object]],
) -> int:
    ordered = sorted(_operator_services(operator_code))
    matching_targets = [service for service in ordered if service in target_services]
    if len(matching_targets) != 1:
        raise RuntimeError("recovery workload must bind one exact target per operator")
    target = matching_targets[0]
    width = 1
    for service in ordered:
        item = capacities.get(service)
        declared = item.get("declared_capacity") if item is not None else None
        active = item.get("active_lease_count") if item is not None else None
        if (
            item is None
            or item.get("operator_code") != operator_code
            or type(declared) is not int
            or type(active) is not int
            or declared <= 0
            or not 0 <= active <= declared
        ):
            raise RuntimeError("operator capacity cannot bound recovery routing")
        if service == target:
            if active >= declared:
                raise RuntimeError("restored target has no free lease capacity")
            break
        width += declared - active
    if not 1 <= width <= _MAX_WITNESS_CONCURRENCY:
        raise RuntimeError("recovery routing requires unsafe witness concurrency")
    return width


def _active_operator_workload(
    request: ProbeRequest,
    client: ReadOnlyHttpClient,
    operator_code: str,
    *,
    target_services: set[str],
    capacities: Mapping[str, Mapping[str, object]],
) -> tuple[Mapping[str, object] | None, str | None]:
    expected_services = _operator_services(operator_code)
    attempts = (
        1
        if request.phase == "disruption"
        else _recovery_route_width(operator_code, target_services, capacities)
    )
    if operator_code == "asr_online":
        trace_ids = tuple(
            _witness_id(
                request,
                f"{request.phase}-asr-online-session",
                attempt,
            )
            for attempt in range(attempts)
        )
        sessions = (
            (client.probe_asr_lease(trace_ids[0]),)
            if attempts == 1
            else client.probe_asr_leases(trace_ids)
        )
        observations: list[Mapping[str, object]] = []
        for session in sessions:
            instance_id = session.get("instance_id")
            if type(instance_id) is not str or instance_id not in expected_services:
                return None, "ASR session lease instance is outside the authoritative set"
            observations.append(session)
            if request.phase == "disruption" and instance_id in target_services:
                return None, "ASR session was routed to a stopped target"
            if request.phase == "recovery" and instance_id in target_services:
                return {
                    "operator_code": operator_code,
                    "sessions": observations,
                    "participating_instance": instance_id,
                }, None
        if request.phase == "disruption":
            return {
                "operator_code": operator_code,
                "sessions": observations,
                "participating_instance": observations[0]["instance_id"],
            }, None
        return None, "restored ASR target has not received an independent lease"
    if operator_code in _ONLINE_WORKLOADS:
        eligible_instances = (
            expected_services & target_services
            if request.phase == "recovery"
            else expected_services - target_services
        )
        try:
            batch = _run_online_image_batch(
                request,
                client,
                operator_code,
                count=attempts,
                eligible_instances=eligible_instances,
            )
        except RuntimeError:
            return None, "online workload has no request-bound active lease on an eligible instance"
        active_lease = _mapping(batch.get("active_lease_witness"))
        instance_id = active_lease.get("instance_id") if active_lease is not None else None
        if type(instance_id) is not str or instance_id not in eligible_instances:
            return None, "online workload active lease is outside the eligible instance set"
        return {
            "operator_code": operator_code,
            "batch": batch,
            "participating_instance": instance_id,
        }, None

    task_type = "ASR" if operator_code == "asr_offline" else "PPT"
    media_url = (
        request.long_teacher_video_url
        if operator_code == "asr_offline"
        else request.long_slides_video_url
    )
    submissions: list[Mapping[str, object]] = []
    for attempt in range(attempts):
        submission = _submit_task(
            request,
            client,
            purpose=f"{request.phase}-{operator_code}-routing",
            task_type=task_type,
            media_url=media_url,
            attempt=attempt,
        )
        submissions.append(submission)
        fact = _mapping(submission.get("fact"))
        task_id = fact.get("task_id") if fact is not None else None
        if fact is None or type(task_id) is not str:
            return None, "offline workload task identity is malformed"
        lease = _lease_for_task(client, task_id, operator_code)
        if lease is None:
            continue
        instance_id = lease.get("instance_id")
        if type(instance_id) is not str or instance_id not in expected_services:
            return None, "offline workload lease is outside the authoritative instance set"
        if request.phase == "disruption" and instance_id in target_services:
            return None, "offline workload was leased to a stopped target"
        if request.phase == "recovery" and instance_id not in target_services:
            continue
        return {
            "operator_code": operator_code,
            "submissions": submissions,
            "lease": dict(lease),
            "participating_instance": instance_id,
        }, None
    return None, (
        "remaining offline instances have not actively leased the disruption workload"
        if request.phase == "disruption"
        else "restored offline target has not actively leased a recovery workload"
    )


def _read_phase_observations(
    request: ProbeRequest,
    *,
    phase: str,
    reference: str | None,
) -> Mapping[str, object] | None:
    if reference is None:
        return None
    match = _EVIDENCE_REFERENCE.fullmatch(reference)
    if match is None:
        return None
    relative = PurePosixPath(match.group("path"))
    if relative.is_absolute() or ".." in relative.parts:
        return None
    path = request.release_root.joinpath(*relative.parts)
    try:
        if sha256_file(path) != match.group("sha"):
            return None
        raw: object = json.loads(path.read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError, ProbeValidationError):
        return None
    document = _mapping(raw)
    observations = _mapping(document.get("observations")) if document is not None else None
    expected_window = None if phase == "baseline" else _fault_window(request)
    if (
        document is None
        or document.get("campaign_id") != request.campaign_id
        or document.get("case_id") != request.case_id
        or document.get("scenario_id") != request.scenario_id
        or document.get("phase") != phase
        or document.get("check_index") != 0
        or document.get("challenge") != request.challenge
        or document.get("status") != "passed"
        or document.get("targets") != [target.to_dict() for target in request.targets]
        or document.get("fault_window") != expected_window
        or observations is None
    ):
        return None
    return observations


def _read_baseline_observations(request: ProbeRequest) -> Mapping[str, object] | None:
    return _read_phase_observations(
        request,
        phase="baseline",
        reference=request.baseline_ref,
    )


def _read_action_observations(request: ProbeRequest) -> Mapping[str, object] | None:
    return _read_phase_observations(
        request,
        phase="action",
        reference=request.action_ref,
    )


def _baseline_decision(request: ProbeRequest, client: ReadOnlyHttpClient) -> ProbeDecision:
    readiness = {
        "control": client.get_json(CONTROL_READINESS_URL),
        "orchestrator": client.get_json(ORCHESTRATOR_READINESS_URL),
        "vision": client.get_json(VISION_READINESS_URL),
        "gateway": client.get_json(GATEWAY_READINESS_URL),
    }
    health = client.get_json(GATEWAY_HEALTH_URL)
    queues = client.get_json(QUEUES_URL)
    instances = _records(client.get_json(OPERATOR_INSTANCES_URL))
    capacities = _records(client.get_json(OPERATOR_CAPACITY_URL))
    counters = _lease_counters(client.get_text(GATEWAY_METRICS_URL))
    queues_valid, outbox_pending = _valid_queues(queues)
    reasons: list[str] = []
    if not all(_ready(value) for value in readiness.values()):
        reasons.append("all four platform services must be ready before fault injection")
    health_document = _mapping(health)
    if health_document is None or health_document.get("status") != "ok":
        reasons.append("Online Gateway health baseline is unavailable")
    if not queues_valid:
        reasons.append("task queue and Outbox baseline is unavailable")
    expected: set[str] = set()
    for code, prefix in ALL_OPERATOR_PREFIXES.items():
        device = "cpu" if code == "ppt_slice" else "gpu"
        expected.update(f"{prefix}-{device}{index}" for index in range(3))
    if (
        instances is None
        or capacities is None
        or set(instances) != expected
        or set(capacities) != expected
    ):
        reasons.append("operator registry baseline is not authoritative 21/21")
    service = request.targets[0].compose_service
    active_witness: Mapping[str, object] = {"kind": "readiness_only"}
    if not reasons and service == "control-service":
        active_witness = {
            "kind": "control_task_fact",
            **_submit_task(
                request,
                client,
                purpose="control-restart",
                task_type="ASR",
                media_url=request.short_teacher_video_url,
            ),
        }
        fact = _mapping(active_witness.get("fact"))
        if fact is None or fact.get("node_count") != 1:
            reasons.append("Control pre-restart task DAG fact has not been created")
    elif not reasons and service == "vision-orchestrator-service":
        active_witness = {
            "kind": "vision_task_fact",
            **_submit_task(
                request,
                client,
                purpose="vision-restart",
                task_type="TEACHER_BEHAVIOR",
                media_url=request.short_teacher_video_url,
            ),
        }
        fact = _mapping(active_witness.get("fact"))
        if (
            fact is None
            or fact.get("node_count") != 1
            or fact.get("terminal") is not False
            or fact.get("node_statuses") != [50]
            or fact.get("result_digests") != []
        ):
            reasons.append("visual pre-restart task lacks one running unfinished node")
    elif not reasons and service in {"online-gateway-service", "redis"}:
        trace_id = _witness_id(request, f"{service}-old-asr")
        session = client.prepare_persistent_asr(request, trace_id)
        if session.get("status") != "connected":
            reasons.append("persistent northbound ASR session is not connected")
        active_witness = {
            "kind": "persistent_asr_session",
            "session": dict(session),
        }
    heartbeat_facts = {
        instance_id: item.get("last_heartbeat_at")
        for instance_id, item in (instances or {}).items()
    }
    return ProbeDecision(
        "passed" if not reasons else "pending",
        tuple(reasons),
        {
            "platform_readiness": readiness,
            "outbox_pending": outbox_pending,
            "registry_count": 0 if instances is None else len(instances),
            "capacity_count": 0 if capacities is None else len(capacities),
            "gateway_lease_counters": counters,
            "operator_heartbeats": heartbeat_facts,
            "active_witness": active_witness,
        },
    )


def _action_decision(request: ProbeRequest, client: ReadOnlyHttpClient) -> ProbeDecision:
    baseline = _read_baseline_observations(request)
    if baseline is None:
        return ProbeDecision(
            "pending",
            ("phase-bound baseline evidence is unavailable",),
            {"action_state": "baseline_missing"},
        )
    service = request.targets[0].compose_service
    target_url = {
        "control-service": CONTROL_READINESS_URL,
        "orchestrator-service": ORCHESTRATOR_READINESS_URL,
        "vision-orchestrator-service": VISION_READINESS_URL,
        "online-gateway-service": GATEWAY_READINESS_URL,
        "kafka": ORCHESTRATOR_READINESS_URL,
        "redis": CONTROL_READINESS_URL,
    }[service]
    try:
        readiness = client.get_json(target_url)
        unavailable = not _ready(readiness)
    except RuntimeError:
        readiness = {"status": "unavailable"}
        unavailable = True
    if service == "kafka" and not unavailable:
        document = _mapping(readiness)
        checks = _mapping(document.get("checks")) if document is not None else None
        kafka = _mapping(checks.get("kafka")) if checks is not None else None
        unavailable = kafka is None or kafka.get("ready") is not True
    if service == "redis" and not unavailable:
        try:
            client.get_json(OPERATOR_CAPACITY_URL)
        except RuntimeError:
            unavailable = True
    if not unavailable:
        return ProbeDecision(
            "pending",
            ("target disruption is not yet observable",),
            {"target_service": service, "action_readiness": readiness},
        )
    reasons: list[str] = []
    active_witness: Mapping[str, object] = {"kind": "target_unavailable"}
    baseline_active = _mapping(baseline.get("active_witness"))
    if service in {"orchestrator-service", "kafka"}:
        submission = _submit_task(
            request,
            client,
            purpose=f"{service}-restart-window",
            task_type="ASR",
            media_url=request.short_teacher_video_url,
        )
        if not _is_in_fault_window(request, submission.get("accepted_at")):
            reasons.append("restart-window task was accepted before the bound fault window")
        queues = client.get_json(QUEUES_URL)
        valid, pending = _valid_queues(queues)
        baseline_pending = baseline.get("outbox_pending")
        if (
            not valid
            or type(baseline_pending) is not int
            or pending < baseline_pending + 1
        ):
            reasons.append("accepted restart-window task is not retained in Outbox")
        active_witness = {
            "kind": "retained_offline_task",
            **submission,
            "outbox_event": {
                "aggregate_id": f"{submission['task_id']}:ASR",
                "event_type": "COURSE_TASK_REQUESTED",
            },
            "outbox_pending": pending,
            "baseline_outbox_pending": baseline_pending,
        }
    elif service == "vision-orchestrator-service":
        fact = _mapping(baseline_active.get("fact")) if baseline_active is not None else None
        task_id = fact.get("task_id") if fact is not None else None
        if fact is None or type(task_id) is not str:
            reasons.append("pre-restart visual task witness is unavailable")
        else:
            current = _query_task_fact(client, task_id, "TEACHER_BEHAVIOR")
            if current.get("node_codes") != fact.get("node_codes"):
                reasons.append("visual task DAG changed during restart window")
            active_witness = {
                "kind": "visual_task_retained",
                "fact": current,
            }
    elif service == "online-gateway-service":
        session = _mapping(baseline_active.get("session")) if baseline_active is not None else None
        trace_id = session.get("trace_id") if session is not None else None
        state = (
            client.persistent_asr_state(request, trace_id)
            if type(trace_id) is str
            else {"status": "missing"}
        )
        if (
            session is None
            or type(session.get("lease_id")) is not str
            or state.get("status") != "disconnected"
            or state.get("lease_id") != session.get("lease_id")
            or not _is_in_fault_window(request, state.get("disconnected_at"))
        ):
            reasons.append("the pre-restart ASR session has not recorded an actual disconnect")
        active_witness = {
            "kind": "asr_disconnect",
            "old_session": dict(state),
            "seamless_migration_claimed": False,
        }
    elif service == "redis":
        redis_request = _run_online_image_workload(
            request,
            client,
            "ocr",
            attempt=0,
            expected_business_code=50301,
        )
        if not _is_in_fault_window(request, redis_request.get("started_at")):
            reasons.append("Redis rejection request started before the bound fault window")
        active_witness = {
            "kind": "redis_capacity_rejection",
            "request": redis_request,
        }
    elif service == "control-service":
        if baseline_active is None or baseline_active.get("kind") != "control_task_fact":
            reasons.append("pre-restart Control task witness is unavailable")
        else:
            control_fact = _mapping(baseline_active.get("fact"))
            active_witness = {
                "kind": "control_unavailable_with_preexisting_task",
                "task_id": control_fact.get("task_id") if control_fact is not None else None,
            }
    return ProbeDecision(
        "passed" if not reasons else "pending",
        tuple(reasons),
        {
            "target_service": service,
            "action_readiness": readiness,
            "window_observed_at": _utc_now(),
            "active_witness": active_witness,
        },
    )


def _operator_decision(request: ProbeRequest, client: ReadOnlyHttpClient) -> ProbeDecision:
    readiness = client.get_json(CONTROL_READINESS_URL)
    raw_instances = client.get_json(OPERATOR_INSTANCES_URL)
    raw_capacity = client.get_json(OPERATOR_CAPACITY_URL)
    instances = _records(raw_instances)
    capacities = _records(raw_capacity)
    reasons: list[str] = []
    if _read_baseline_observations(request) is None:
        reasons.append("phase-bound operator baseline is unavailable")
    if not _ready(readiness):
        reasons.append("Control readiness has not recovered")
    if instances is None or capacities is None:
        reasons.append("operator registry or capacity facts are malformed")
        return ProbeDecision(
            "pending",
            tuple(reasons),
            {"control_readiness": readiness, "registry_count": 0, "capacity_count": 0},
        )
    target_services = {target.compose_service for target in request.targets}
    expected_codes = {_service_code(service) for service in target_services}
    if None in expected_codes:
        reasons.append("target operator code cannot be derived")
    if request.phase == "disruption":
        present = target_services & set(instances)
        if present:
            reasons.append("target instances have not disappeared after TTL")
        for raw_code in expected_codes:
            if raw_code is None:
                continue
            prefix = ALL_OPERATOR_PREFIXES[raw_code]
            device = "cpu" if raw_code == "ppt_slice" else "gpu"
            siblings = {f"{prefix}-{device}{index}" for index in range(3)} - target_services
            if not siblings <= set(instances) or not siblings <= set(capacities):
                reasons.append(f"remaining {raw_code} instances are incomplete")
                continue
            for sibling in siblings:
                gpu = None if device == "cpu" else int(sibling.rsplit("gpu", 1)[1])
                if not _healthy_instance(
                    instances[sibling], raw_code, gpu
                ) or not _healthy_capacity(capacities[sibling], raw_code):
                    reasons.append(f"remaining {raw_code} capacity is not schedulable")
                    break
    else:
        for service in target_services:
            code = _service_code(service)
            gpu = None if "-cpu" in service else int(service.rsplit("gpu", 1)[1])
            if (
                code is None
                or service not in instances
                or service not in capacities
                or not _healthy_instance(instances[service], code, gpu)
                or not _healthy_capacity(capacities[service], code)
            ):
                reasons.append(f"restored operator is not healthy and schedulable: {service}")
    workload_evidence: list[Mapping[str, object]] = []
    if not reasons:
        for raw_code in sorted(code for code in expected_codes if code is not None):
            evidence, error = _active_operator_workload(
                request,
                client,
                raw_code,
                target_services=target_services,
                capacities=capacities,
            )
            if error is not None:
                reasons.append(error)
            elif evidence is not None:
                workload_evidence.append(evidence)
    return ProbeDecision(
        "passed" if not reasons else "pending",
        tuple(reasons),
        {
            "control_readiness": readiness,
            "registry_count": len(instances),
            "capacity_count": len(capacities),
            "target_instances_present": sorted(target_services & set(instances)),
            "active_workloads": workload_evidence,
        },
    )


def _platform_decision(request: ProbeRequest, client: ReadOnlyHttpClient) -> ProbeDecision:
    service = request.targets[0].compose_service
    readiness_urls = {
        "control-service": CONTROL_READINESS_URL,
        "orchestrator-service": ORCHESTRATOR_READINESS_URL,
        "vision-orchestrator-service": VISION_READINESS_URL,
        "online-gateway-service": GATEWAY_READINESS_URL,
    }
    readiness = client.get_json(readiness_urls[service])
    queues = client.get_json(QUEUES_URL)
    queues_valid, outbox_pending = _valid_queues(queues)
    observations: dict[str, object] = {
        "service_readiness": readiness,
        "queues_valid": queues_valid,
        "outbox_pending": outbox_pending,
    }
    reasons: list[str] = []
    baseline = _read_baseline_observations(request)
    action = _read_action_observations(request)
    if baseline is None:
        reasons.append("phase-bound platform baseline is unavailable")
    if not _ready(readiness):
        reasons.append(f"{service} readiness has not recovered")
    if not queues_valid:
        reasons.append("Control task and queue facts are unavailable")
    if request.phase in {"disruption", "recovery"} and action is None:
        reasons.append("restart-window action witness is unavailable")
    active_recovery: Mapping[str, object] = {"kind": "not_applicable"}
    if request.phase == "recovery" and baseline is not None:
        baseline_active = _mapping(baseline.get("active_witness"))
        action_active = _mapping(action.get("active_witness")) if action is not None else None
        if service == "control-service":
            baseline_fact = (
                _mapping(baseline_active.get("fact")) if baseline_active is not None else None
            )
            task_id = baseline_fact.get("task_id") if baseline_fact is not None else None
            if baseline_fact is None or type(task_id) is not str:
                reasons.append("Control pre-restart task fact is unavailable")
            else:
                current = _query_task_fact(client, task_id, "ASR")
                if (
                    current.get("task_id") != baseline_fact.get("task_id")
                    or current.get("node_codes") != baseline_fact.get("node_codes")
                    or current.get("node_count") != baseline_fact.get("node_count")
                ):
                    reasons.append("Control task or node identity changed across restart")
                instances = _records(client.get_json(OPERATOR_INSTANCES_URL))
                baseline_heartbeats = _mapping(baseline.get("operator_heartbeats"))
                advanced = 0
                if (
                    instances is None
                    or len(instances) != 21
                    or baseline_heartbeats is None
                    or set(instances) != set(baseline_heartbeats)
                ):
                    reasons.append("Control registration heartbeat inventory is not 21/21")
                else:
                    fault_opened_at = _parse_utc_timestamp(
                        request.fault_window_opened_at,
                        "fault_window_opened_at",
                    )
                    for instance_id, instance in instances.items():
                        before_value = baseline_heartbeats.get(instance_id)
                        after_value = instance.get("last_heartbeat_at")
                        try:
                            before_time = _parse_utc_timestamp(
                                before_value,
                                "baseline heartbeat",
                            )
                            after_time = _parse_utc_timestamp(
                                after_value,
                                "recovery heartbeat",
                            )
                        except ProbeValidationError:
                            reasons.append("operator heartbeat timestamp is malformed")
                            break
                        if after_time > before_time and after_time >= fault_opened_at:
                            advanced += 1
                    if advanced != 21:
                        reasons.append("all 21 operator heartbeats have not advanced after restart")
                active_recovery = {
                    "kind": "control_task_and_heartbeats",
                    "fact": current,
                    "advanced_heartbeat_count": advanced,
                }
        elif service == "orchestrator-service":
            action_fact = (
                _mapping(action_active.get("fact")) if action_active is not None else None
            )
            task_id = action_fact.get("task_id") if action_fact is not None else None
            if type(task_id) is not str:
                reasons.append("orchestrator restart-window task fact is unavailable")
            else:
                current = _query_task_fact(client, task_id, "ASR")
                if current.get("completed") is not True:
                    reasons.append("orchestrator restart-window task is not completed")
                if current.get("node_codes") != ["ASR_TRANSCRIPTION"]:
                    reasons.append("orchestrator task DAG is missing or duplicated")
                active_recovery = {
                    "kind": "orchestrator_task_completed",
                    "fact": current,
                }
        elif service == "vision-orchestrator-service":
            baseline_fact = (
                _mapping(baseline_active.get("fact")) if baseline_active is not None else None
            )
            task_id = baseline_fact.get("task_id") if baseline_fact is not None else None
            if type(task_id) is not str:
                reasons.append("visual pre-restart task fact is unavailable")
            else:
                current = _query_task_fact(client, task_id, "TEACHER_BEHAVIOR")
                result_digests = current.get("result_digests")
                node_updated_at = current.get("node_updated_at")
                if current.get("completed") is not True:
                    reasons.append("visual task did not complete successfully after recovery")
                if current.get("node_codes") != ["TEACHER_BEHAVIOR_ANALYSIS"]:
                    reasons.append("visual task node is missing or duplicated")
                if (
                    not isinstance(result_digests, list)
                    or len(result_digests) != 1
                    or len(set(result_digests)) != 1
                ):
                    reasons.append("visual task requires one nonempty unique result digest")
                if (
                    not isinstance(node_updated_at, list)
                    or len(node_updated_at) != 1
                    or not _is_in_fault_window(request, node_updated_at[0])
                ):
                    reasons.append("visual result node was not updated in the bound fault window")
                active_recovery = {
                    "kind": "visual_task_terminal",
                    "fact": current,
                }
        elif service == "online-gateway-service":
            session = (
                _mapping(baseline_active.get("session"))
                if baseline_active is not None
                else None
            )
            old_lease_id = session.get("lease_id") if session is not None else None
            active_lease_ids = {
                lease.get("lease_id")
                for lease in _all_active_leases(client, operator_code="asr_online")
            }
            if type(old_lease_id) is not str or old_lease_id in active_lease_ids:
                reasons.append("old Gateway ASR lease has not been reclaimed")
            else:
                new_trace_id = _witness_id(request, "gateway-reconnected-asr")
                new_session = client.probe_asr_lease(new_trace_id)
                if (
                    new_session.get("trace_id") != new_trace_id
                    or type(new_session.get("lease_id")) is not str
                    or new_session.get("lease_id") == old_lease_id
                ):
                    reasons.append("reconnected ASR session has no independent lease")
                online_request = _run_online_image_batch(
                    request,
                    client,
                    "ocr",
                    count=1,
                    eligible_instances=_operator_services("ocr"),
                )
                active_recovery = {
                    "kind": "gateway_disconnect_reconnect",
                    "old_lease_id": old_lease_id,
                    "old_lease_reclaimed": old_lease_id not in active_lease_ids,
                    "new_session": new_session,
                    "online_http_request": online_request,
                    "seamless_migration_claimed": False,
                }
    if service == "online-gateway-service":
        health = client.get_json(GATEWAY_HEALTH_URL)
        metrics = client.get_text(GATEWAY_METRICS_URL)
        observations["gateway_health"] = health
        observations["gateway_metrics_sha256"] = hashlib.sha256(metrics.encode()).hexdigest()
    observations["active_recovery"] = active_recovery
    return ProbeDecision(
        "passed" if not reasons else "pending",
        tuple(reasons),
        observations,
    )


def _kafka_decision(request: ProbeRequest, client: ReadOnlyHttpClient) -> ProbeDecision:
    queues = client.get_json(QUEUES_URL)
    readiness = client.get_json(ORCHESTRATOR_READINESS_URL)
    valid, pending = _valid_queues(queues)
    reasons: list[str] = []
    if _read_baseline_observations(request) is None:
        reasons.append("phase-bound Kafka baseline is unavailable")
    if not valid:
        reasons.append("Outbox and queue facts are unavailable")
    action = _read_action_observations(request)
    active_recovery: Mapping[str, object] = {"kind": "not_applicable"}
    if request.phase == "disruption":
        if action is None:
            reasons.append("Kafka restart-window action witness is unavailable")
    else:
        if action is None:
            reasons.append("Kafka restart-window action witness is unavailable")
        if not _ready(readiness):
            reasons.append("orchestrator Kafka consumer has not recovered")
        document = _mapping(readiness)
        checks = _mapping(document.get("checks")) if document is not None else None
        kafka = _mapping(checks.get("kafka")) if checks is not None else None
        if kafka is None or kafka.get("ready") is not True:
            reasons.append("Kafka consumer lag fact is not ready")
        action_active = _mapping(action.get("active_witness")) if action is not None else None
        action_fact = _mapping(action_active.get("fact")) if action_active is not None else None
        task_id = action_fact.get("task_id") if action_fact is not None else None
        outbox_event = (
            _mapping(action_active.get("outbox_event"))
            if action_active is not None
            else None
        )
        if type(task_id) is not str:
            reasons.append("Kafka restart-window task identity is unavailable")
        else:
            if (
                outbox_event is None
                or outbox_event.get("aggregate_id") != f"{task_id}:ASR"
                or outbox_event.get("event_type") != "COURSE_TASK_REQUESTED"
            ):
                reasons.append("Kafka task-bound Outbox event identity is unavailable")
            current = _query_task_fact(client, task_id, "ASR")
            if current.get("completed") is not True:
                reasons.append("Kafka restart-window task has not completed after recovery")
            if current.get("node_codes") != ["ASR_TRANSCRIPTION"]:
                reasons.append("Kafka recovery created a missing or duplicate DAG")
            active_recovery = {
                "kind": "kafka_outbox_consumed_once",
                "fact": current,
            }
    return ProbeDecision(
        "passed" if not reasons else "pending",
        tuple(reasons),
        {
            "orchestrator_readiness": readiness,
            "outbox_pending": pending,
            "active_recovery": active_recovery,
        },
    )


def _redis_decision(request: ProbeRequest, client: ReadOnlyHttpClient) -> ProbeDecision:
    readiness = client.get_json(CONTROL_READINESS_URL)
    instances = _records(client.get_json(OPERATOR_INSTANCES_URL))
    capacities = _records(client.get_json(OPERATOR_CAPACITY_URL))
    reasons: list[str] = []
    observations: dict[str, object] = {"control_readiness": readiness}
    baseline = _read_baseline_observations(request)
    action = _read_action_observations(request)
    if baseline is None:
        reasons.append("phase-bound Redis baseline is unavailable")
    if instances is None or capacities is None:
        reasons.append("Redis registry and lease facts are unavailable")
        return ProbeDecision("pending", tuple(reasons), observations)
    observations["registry_count"] = len(instances)
    observations["capacity_count"] = len(capacities)
    if request.phase == "disruption":
        if action is None:
            reasons.append("Redis restart-window action witness is unavailable")
    else:
        if action is None:
            reasons.append("Redis restart-window action witness is unavailable")
        expected: set[str] = set()
        for code, prefix in ALL_OPERATOR_PREFIXES.items():
            device = "cpu" if code == "ppt_slice" else "gpu"
            expected.update(f"{prefix}-{device}{index}" for index in range(3))
        if set(instances) != expected or set(capacities) != expected:
            reasons.append("operator registration has not recovered to authoritative 21/21")
        for service in expected & set(instances) & set(capacities):
            code = cast(str, _service_code(service))
            gpu = None if "-cpu" in service else int(service.rsplit("gpu", 1)[1])
            if not _healthy_instance(instances[service], code, gpu) or not _healthy_capacity(
                capacities[service], code
            ):
                reasons.append(f"Redis capacity fact is unhealthy or oversold: {service}")
                break
        if not _ready(readiness):
            reasons.append("Control Redis readiness has not recovered")
        baseline_active = _mapping(baseline.get("active_witness")) if baseline is not None else None
        session = (
            _mapping(baseline_active.get("session")) if baseline_active is not None else None
        )
        old_lease_id = session.get("lease_id") if session is not None else None
        active_lease_ids = {
            lease.get("lease_id")
            for lease in _all_active_leases(client, operator_code="asr_online")
        }
        if type(old_lease_id) is not str or old_lease_id in active_lease_ids:
            reasons.append("pre-restart Redis lease has not been reclaimed")
        action_active = _mapping(action.get("active_witness")) if action is not None else None
        action_request = (
            _mapping(action_active.get("request")) if action_active is not None else None
        )
        rejection_binding = (
            _mapping(action_request.get("rejection_binding"))
            if action_request is not None
            else None
        )
        request_id = action_request.get("request_id") if action_request is not None else None
        trace_id = action_request.get("trace_id") if action_request is not None else None
        if (
            action_active is None
            or action_active.get("kind") != "redis_capacity_rejection"
            or action_request is None
            or action_request.get("business_code") != 50301
            or type(request_id) is not str
            or type(trace_id) is not str
            or rejection_binding is None
            or rejection_binding.get("request_id") != request_id
            or rejection_binding.get("trace_id") != trace_id
            or rejection_binding.get("business_code") != 50301
        ):
            reasons.append("Redis stop-window capacity rejection is unavailable or ambiguous")
        if not reasons:
            recovered_request = _run_released_online_image_workload(
                request,
                client,
                "ocr",
                attempt=1,
            )
            observations["active_recovery"] = {
                "kind": "redis_registration_and_lease_recovered",
                "old_lease_id": old_lease_id,
                "old_lease_reclaimed": True,
                "new_request": recovered_request,
            }
    return ProbeDecision(
        "passed" if not reasons else "pending",
        tuple(reasons),
        observations,
    )


def evaluate_request(request: ProbeRequest, client: ReadOnlyHttpClient) -> ProbeDecision:
    if request.phase == "baseline":
        return _baseline_decision(request, client)
    if request.phase == "action":
        return _action_decision(request, client)
    contract = _SCENARIOS[request.scenario_id]
    if contract.operator_code is not None or contract.gpu_index is not None:
        return _operator_decision(request, client)
    service = request.targets[0].compose_service
    if service in {
        "control-service",
        "orchestrator-service",
        "vision-orchestrator-service",
        "online-gateway-service",
    }:
        return _platform_decision(request, client)
    if service == "kafka":
        return _kafka_decision(request, client)
    if service == "redis":
        return _redis_decision(request, client)
    raise ProbeValidationError("scenario has no semantic evaluator")


def canonical_json_bytes(payload: Mapping[str, object]) -> bytes:
    return (
        json.dumps(
            dict(payload),
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _prepare_publication_directory(root: Path, relative_parent: Path) -> Path:
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    _reject_symlink_chain(root)
    current = root
    for part in relative_parent.parts:
        if part in {"", ".", ".."}:
            raise ProbeValidationError("evidence path contains an unsafe component")
        current /= part
        current.mkdir(mode=0o700, exist_ok=True)
        metadata = os.lstat(current)
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise ProbeValidationError("evidence parent is not a real directory")
        if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o022:
            raise PermissionError(
                "evidence parent must be owned by the probe UID and not writable by others"
            )
    return current


def publish_json_once(
    request: ProbeRequest,
    relative_path: Path,
    payload: Mapping[str, object],
) -> tuple[Path, str]:
    if relative_path.is_absolute() or ".." in relative_path.parts or not relative_path.name:
        raise ProbeValidationError("evidence path must be release-relative and cannot escape")
    parent = _prepare_publication_directory(request.evidence_root, relative_path.parent)
    final_path = parent / relative_path.name
    if final_path.exists() or final_path.is_symlink():
        raise FileExistsError(f"evidence already exists: {final_path}")
    content = canonical_json_bytes(payload)
    directory_fd = os.open(
        parent,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    temporary_name = f".{relative_path.name}.{secrets.token_hex(16)}.tmp"
    descriptor = -1
    linked = False
    try:
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise OSError("evidence write made no progress")
            offset += written
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        opened = os.fstat(descriptor)
        os.link(
            temporary_name,
            relative_path.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
            follow_symlinks=False,
        )
        linked = True
        os.unlink(temporary_name, dir_fd=directory_fd)
        linked = False
        os.fsync(directory_fd)
        named = os.stat(relative_path.name, dir_fd=directory_fd, follow_symlinks=False)
        if (
            (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
            or named.st_uid != os.getuid()
            or named.st_nlink != 1
            or stat.S_IMODE(named.st_mode) != 0o600
        ):
            raise RuntimeError("published evidence identity or mode changed")
    except FileExistsError:
        if linked:
            os.unlink(relative_path.name, dir_fd=directory_fd)
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)
    return final_path, hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    metadata = os.lstat(path)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise ProbeValidationError("evidence file is not a mode-restricted regular file")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    digest = hashlib.sha256()
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise ProbeValidationError("evidence file changed while opening")
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (
            (after.st_dev, after.st_ino) != (metadata.st_dev, metadata.st_ino)
            or after.st_nlink != 1
            or stat.S_IMODE(after.st_mode) != 0o600
        ):
            raise ProbeValidationError("evidence file changed while hashing")
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def execute_probe(
    request: ProbeRequest,
    *,
    client: ReadOnlyHttpClient | None = None,
    lock_validator: Callable[[Path, int, Path], None] | None = None,
) -> dict[str, object]:
    selected_lock_validator = lock_validator or (
        lambda release, holder, path: _validate_lock_binding(
            release,
            holder,
            path,
            check_held=True,
        )
    )
    selected_lock_validator(request.release_root, request.lock_holder_pid, request.lock_path)
    selected_client = client or UrllibReadOnlyClient()
    try:
        decision = evaluate_request(request, selected_client)
    except (KeyError, RuntimeError, TypeError, ValueError) as error:
        decision = ProbeDecision(
            "pending",
            (f"read-only facts are not yet provable: {type(error).__name__}",),
            {"collection_state": "unavailable"},
        )
    recorded_at = datetime.now(UTC).isoformat()
    evidence = {
        "schema_version": 1,
        "evidence_type": "extreme_load_fault_semantic_probe",
        "campaign_id": request.campaign_id,
        "case_id": request.case_id,
        "scenario_id": request.scenario_id,
        "phase": request.phase,
        "check_index": request.check_index,
        "challenge": request.challenge,
        "fault_window": _fault_window(request),
        "status": decision.status,
        "targets": [target.to_dict() for target in request.targets],
        "recorded_at": recorded_at,
        "reasons": list(decision.reasons),
        "observations": dict(decision.observations),
        "maintenance_lock": {
            "holder_pid": request.lock_holder_pid,
            "lock_path": str(request.lock_path),
            "release_root": str(request.release_root),
        },
        "business_traffic_ports": [18100, 18103],
        "http_method_allowlist": ["GET", "POST", "WEBSOCKET"],
        "endpoint_allowlist": sorted(
            {
                *READ_ONLY_ENDPOINT_ALLOWLIST,
                CONTROL_JOBS_URL,
                GATEWAY_ASR_URL,
            }
        ),
    }
    relative = (
        Path(request.case_id.lower())
        / request.scenario_id
        / f"{request.phase}-{request.check_index}-{request.challenge}-{secrets.token_hex(8)}.json"
    )
    evidence_path, digest = publish_json_once(request, relative, evidence)
    verified_digest = sha256_file(evidence_path)
    if verified_digest != digest:
        raise RuntimeError("published evidence digest changed")
    release_relative = evidence_path.relative_to(request.release_root).as_posix()
    return {
        "schema_version": 1,
        "campaign_id": request.campaign_id,
        "case_id": request.case_id,
        "scenario_id": request.scenario_id,
        "phase": request.phase,
        "check_index": request.check_index,
        "challenge": request.challenge,
        "fault_window": _fault_window(request),
        "status": decision.status,
        "targets": [target.to_dict() for target in request.targets],
        "lock_binding": {
            "holder_pid": request.lock_holder_pid,
            "lock_path": str(request.lock_path),
            "release_root": str(request.release_root),
        },
        "evidence_refs": [f"release:{release_relative}#sha256:{digest}"],
    }


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--lock-only", action="store_true")
    parser.add_argument("--gateway-worker", action="store_true")
    parser.add_argument("--campaign-id")
    parser.add_argument("--case-id")
    parser.add_argument("--scenario-id")
    parser.add_argument("--phase")
    parser.add_argument("--check-index", type=int)
    parser.add_argument("--challenge")
    parser.add_argument("--target", action="append")
    parser.add_argument("--release-root", type=Path)
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--lock-holder-pid", type=int)
    parser.add_argument("--lock-path", type=Path)
    parser.add_argument("--short-teacher-video-url")
    parser.add_argument("--long-teacher-video-url")
    parser.add_argument("--long-slides-video-url")
    parser.add_argument("--fault-window-token")
    parser.add_argument("--fault-window-opened-at")
    parser.add_argument("--baseline-ref")
    parser.add_argument("--action-ref")
    parser.add_argument("--worker-state-path")
    parser.add_argument("--worker-trace-id")
    return parser.parse_args(argv)


def _require_arguments(arguments: argparse.Namespace, names: Sequence[str]) -> None:
    missing = [name for name in names if getattr(arguments, name) is None]
    if missing:
        raise ProbeValidationError("missing required arguments: " + ", ".join(missing))


def _lock_only_response(arguments: argparse.Namespace) -> dict[str, object]:
    _require_arguments(
        arguments,
        ("challenge", "release_root", "lock_holder_pid", "lock_path"),
    )
    if _CHALLENGE.fullmatch(arguments.challenge) is None:
        raise ProbeValidationError("lock challenge is invalid")
    release_root = cast(Path, arguments.release_root).absolute()
    if not release_root.is_dir() or _GIT_SHA.fullmatch(release_root.name) is None:
        raise ProbeValidationError("lock release root is invalid")
    _validate_lock_binding(
        release_root,
        arguments.lock_holder_pid,
        arguments.lock_path,
        check_held=True,
    )
    return {
        "schema_version": 1,
        "status": "held",
        "challenge": arguments.challenge,
        "release_root": str(release_root),
        "lock_path": str(arguments.lock_path),
        "holder_pid": arguments.lock_holder_pid,
    }


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parse_args(argv)
    if arguments.gateway_worker:
        _require_arguments(
            arguments,
            (
                "worker_state_path",
                "campaign_id",
                "case_id",
                "scenario_id",
                "challenge",
                "worker_trace_id",
            ),
        )
        return _gateway_worker_main(arguments)
    try:
        if arguments.lock_only:
            response = _lock_only_response(arguments)
            sys.stdout.buffer.write(canonical_json_bytes(response))
            return 0
        _require_arguments(
            arguments,
            (
                "campaign_id",
                "case_id",
                "scenario_id",
                "phase",
                "check_index",
                "challenge",
                "target",
                "release_root",
                "evidence_root",
                "lock_holder_pid",
                "lock_path",
                "short_teacher_video_url",
                "long_teacher_video_url",
                "long_slides_video_url",
            ),
        )
        request = ProbeRequest.build(
            campaign_id=arguments.campaign_id,
            case_id=arguments.case_id,
            scenario_id=arguments.scenario_id,
            phase=arguments.phase,
            check_index=arguments.check_index,
            challenge=arguments.challenge,
            targets=tuple(TargetIdentity.parse(value) for value in arguments.target),
            release_root=arguments.release_root,
            evidence_root=arguments.evidence_root,
            lock_holder_pid=arguments.lock_holder_pid,
            lock_path=arguments.lock_path,
            short_teacher_video_url=arguments.short_teacher_video_url,
            long_teacher_video_url=arguments.long_teacher_video_url,
            long_slides_video_url=arguments.long_slides_video_url,
            fault_window_token=arguments.fault_window_token,
            fault_window_opened_at=arguments.fault_window_opened_at,
            baseline_ref=arguments.baseline_ref,
            action_ref=arguments.action_ref,
        )
        response = execute_probe(request)
    except (OSError, PermissionError, ProbeValidationError, RuntimeError) as error:
        print(f"fault semantic probe blocked: {type(error).__name__}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(canonical_json_bytes(response))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
