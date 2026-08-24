from __future__ import annotations

import hashlib
import json
import math
import re
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .media_download import AsyncSubprocessRunner, CommandResult

_SAFE_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}")
_SAFE_USER = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}")
_SAFE_EVIDENCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,255}")
_CONTAINER_ID = re.compile(r"[0-9a-f]{64}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_FACEREC_SERVICES = ("facerec-gpu0", "facerec-gpu1", "facerec-gpu2")

_REMOTE_SCRIPT = r'''
import hashlib
import json
import os
import re
import subprocess
import sys


CONTAINER_ID = re.compile(r"[0-9a-f]{64}")
SAFE_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
SHA256 = re.compile(r"[0-9a-f]{64}")
MEDIA_MARKERS = (
    b"data:image/",
    b"/9j/",
    b"iVBORw0KGgo",
    b"R0lGOD",
    b"UklGR",
    b"\xff\xd8\xff",
    b"\x89PNG\r\n\x1a\n",
    b"GIF87a",
    b"GIF89a",
)
INNER_SCAN = r"""
import hashlib
import json
import os
import stat
import sys

try:
    import tomllib
except ImportError:
    import tomli as tomllib


MEDIA_MARKERS = (
    b"data:image/",
    b"/9j/",
    b"iVBORw0KGgo",
    b"R0lGOD",
    b"UklGR",
    b"\xff\xd8\xff",
    b"\x89PNG\r\n\x1a\n",
    b"GIF87a",
    b"GIF89a",
)


def iter_entries(root):
    if not os.path.exists(root):
        return
    if os.path.islink(root):
        yield root, "symlink"
        return
    if os.path.isfile(root):
        yield root, "file"
        return
    for current, directories, filenames in os.walk(root, followlinks=False):
        kept = []
        for name in directories:
            path = os.path.join(current, name)
            if os.path.islink(path):
                yield path, "symlink"
            else:
                kept.append(name)
        directories[:] = kept
        for name in filenames:
            path = os.path.join(current, name)
            try:
                mode = os.lstat(path).st_mode
            except OSError:
                raise RuntimeError("container scan entry unavailable")
            if stat.S_ISLNK(mode):
                yield path, "symlink"
            elif stat.S_ISREG(mode):
                yield path, "file"


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def has_media_marker(path):
    carry = b""
    with open(path, "rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            candidate = carry + chunk
            if any(marker in candidate for marker in MEDIA_MARKERS):
                return True
            carry = candidate[-32:]
    return False


payload = json.load(sys.stdin)
expected_size = payload["person_photo_size_bytes"]
expected_sha256 = payload["person_photo_sha256"]
result = {
    "photo_paths_observed": len(payload["photo_paths"]),
    "photo_paths_existing": 0,
    "photo_regular_files": 0,
    "photo_symlinks": 0,
    "photo_forbidden_digest_matches": 0,
    "log_paths_observed": len(payload["log_paths"]),
    "log_paths_existing": 0,
    "log_regular_files": 0,
    "log_symlinks": 0,
    "log_sensitive_marker_files": 0,
    "log_forbidden_digest_matches": 0,
    "save_person_photo_false": None,
}
for root in payload["photo_paths"]:
    if os.path.exists(root):
        result["photo_paths_existing"] += 1
    for path, kind in iter_entries(root) or ():
        if kind == "symlink":
            result["photo_symlinks"] += 1
            continue
        result["photo_regular_files"] += 1
        if os.path.getsize(path) == expected_size and sha256_file(path) == expected_sha256:
            result["photo_forbidden_digest_matches"] += 1
for root in payload["log_paths"]:
    if os.path.isdir(root) and not os.path.islink(root):
        result["log_paths_existing"] += 1
    for path, kind in iter_entries(root) or ():
        if kind == "symlink":
            result["log_symlinks"] += 1
            continue
        result["log_regular_files"] += 1
        if has_media_marker(path):
            result["log_sensitive_marker_files"] += 1
        if os.path.getsize(path) == expected_size and sha256_file(path) == expected_sha256:
            result["log_forbidden_digest_matches"] += 1
if payload.get("verify_save_person_photo") is True:
    config_path = os.environ.get("CONFIG_PATH")
    if not config_path or not os.path.isabs(config_path):
        raise RuntimeError("facerec config path is unavailable")
    with open(config_path, "rb") as stream:
        config = tomllib.load(stream)
    image = config.get("image")
    result["save_person_photo_false"] = (
        isinstance(image, dict) and image.get("save_person_photo") is False
    )
print(json.dumps(result, sort_keys=True))
"""


def checked(argv, *, input_text=None, timeout_seconds=30):
    completed = subprocess.run(
        argv,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
        shell=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("readonly observation command failed")
    if len(completed.stdout.encode()) > 512 * 1024:
        raise RuntimeError("readonly observation output exceeded limit")
    return completed.stdout


def inspect_container(container_id, expected_project, expected_service):
    if CONTAINER_ID.fullmatch(container_id) is None:
        raise RuntimeError("container id is not complete")
    raw = checked(["docker", "container", "inspect", container_id])
    document = json.loads(raw)
    if not isinstance(document, list) or len(document) != 1:
        raise RuntimeError("container inspect observation is incomplete")
    record = document[0]
    labels = (record.get("Config") or {}).get("Labels") or {}
    if (
        record.get("Id") != container_id
        or (record.get("State") or {}).get("Running") is not True
        or labels.get("com.docker.compose.project") != expected_project
        or labels.get("com.docker.compose.service") != expected_service
    ):
        raise RuntimeError("container compose identity mismatch")
    return record


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


payload = json.load(sys.stdin)
if set(payload) != {
    "schema_version",
    "facerec_compose_project",
    "facerec_container_ids",
    "mongodb_compose_project",
    "mongodb_compose_service",
    "mongodb_container_id",
    "mongodb_database",
    "mongodb_collection",
    "online_gateway_compose_project",
    "online_gateway_compose_service",
    "online_gateway_container_id",
    "container_photo_paths",
    "container_log_paths",
    "persistent_paths",
    "person_photo_sha256",
    "person_photo_size_bytes",
}:
    raise RuntimeError("remote observation payload fields mismatch")
if payload["schema_version"] != 1 or SHA256.fullmatch(payload["person_photo_sha256"]) is None:
    raise RuntimeError("remote observation payload is invalid")
if payload["person_photo_size_bytes"] <= 0:
    raise RuntimeError("remote observation fixture size is invalid")

facerec_project = payload["facerec_compose_project"]
facerec_ids = payload["facerec_container_ids"]
if set(facerec_ids) != {"facerec-gpu0", "facerec-gpu1", "facerec-gpu2"}:
    raise RuntimeError("facerec service set is incomplete")
if SAFE_IDENTITY.fullmatch(facerec_project) is None:
    raise RuntimeError("facerec compose project is invalid")

totals = {
    "facerec_container_count": 0,
    "facerec_identity_verified_count": 0,
    "facerec_save_person_photo_false_count": 0,
    "online_gateway_container_count": 0,
    "online_gateway_identity_verified_count": 0,
    "container_photo_paths_observed": 0,
    "container_photo_paths_existing": 0,
    "container_photo_regular_files": 0,
    "container_photo_symlinks": 0,
    "container_photo_forbidden_digest_matches": 0,
    "log_paths_observed": 0,
    "log_paths_existing": 0,
    "log_regular_files": 0,
    "log_symlinks": 0,
    "log_sensitive_marker_files": 0,
    "log_forbidden_digest_matches": 0,
}
for service in ("facerec-gpu0", "facerec-gpu1", "facerec-gpu2"):
    container_id = facerec_ids[service]
    inspect_container(container_id, facerec_project, service)
    totals["facerec_container_count"] += 1
    totals["facerec_identity_verified_count"] += 1
    scan_payload = json.dumps({
        "photo_paths": payload["container_photo_paths"],
        "log_paths": payload["container_log_paths"],
        "person_photo_sha256": payload["person_photo_sha256"],
        "person_photo_size_bytes": payload["person_photo_size_bytes"],
        "verify_save_person_photo": True,
    })
    scan_raw = checked(
        ["docker", "exec", "-i", container_id, "python3", "-c", INNER_SCAN],
        input_text=scan_payload,
    )
    scan = json.loads(scan_raw)
    if scan.get("save_person_photo_false") is not True:
        raise RuntimeError("facerec save_person_photo is not false")
    totals["facerec_save_person_photo_false_count"] += 1
    for source, target in (
        ("photo_paths_observed", "container_photo_paths_observed"),
        ("photo_paths_existing", "container_photo_paths_existing"),
        ("photo_regular_files", "container_photo_regular_files"),
        ("photo_symlinks", "container_photo_symlinks"),
        ("photo_forbidden_digest_matches", "container_photo_forbidden_digest_matches"),
        ("log_paths_observed", "log_paths_observed"),
        ("log_paths_existing", "log_paths_existing"),
        ("log_regular_files", "log_regular_files"),
        ("log_symlinks", "log_symlinks"),
        ("log_sensitive_marker_files", "log_sensitive_marker_files"),
        ("log_forbidden_digest_matches", "log_forbidden_digest_matches"),
    ):
        value = scan.get(source)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise RuntimeError("container observation count is invalid")
        totals[target] += value

gateway_id = payload["online_gateway_container_id"]
inspect_container(
    gateway_id,
    payload["online_gateway_compose_project"],
    payload["online_gateway_compose_service"],
)
totals["online_gateway_container_count"] = 1
totals["online_gateway_identity_verified_count"] = 1
gateway_scan_raw = checked(
    ["docker", "exec", "-i", gateway_id, "python3", "-c", INNER_SCAN],
    input_text=json.dumps({
        "photo_paths": [],
        "log_paths": payload["container_log_paths"],
        "person_photo_sha256": payload["person_photo_sha256"],
        "person_photo_size_bytes": payload["person_photo_size_bytes"],
        "verify_save_person_photo": False,
    }),
)
gateway_scan = json.loads(gateway_scan_raw)
for source, target in (
    ("log_paths_observed", "log_paths_observed"),
    ("log_paths_existing", "log_paths_existing"),
    ("log_regular_files", "log_regular_files"),
    ("log_symlinks", "log_symlinks"),
    ("log_sensitive_marker_files", "log_sensitive_marker_files"),
    ("log_forbidden_digest_matches", "log_forbidden_digest_matches"),
):
    value = gateway_scan.get(source)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RuntimeError("gateway log observation count is invalid")
    totals[target] += value

mongodb_id = payload["mongodb_container_id"]
mongodb_record = inspect_container(
    mongodb_id,
    payload["mongodb_compose_project"],
    payload["mongodb_compose_service"],
)
environment = {}
for item in (mongodb_record.get("Config") or {}).get("Env") or []:
    name, separator, value = item.partition("=")
    if separator:
        environment[name] = value
mongo_user = environment.get("MONGO_INITDB_ROOT_USERNAME")
mongo_password = environment.get("MONGO_INITDB_ROOT_PASSWORD")
if not mongo_user or not mongo_password:
    raise RuntimeError("mongodb runtime credentials are unavailable")
mongo_script = r"""
const targetDb = db.getSiblingDB(%s);
const targetCollection = targetDb.getCollection(%s);
const documentCount = targetCollection.countDocuments({});
const featureCount = targetCollection.countDocuments({embedding: {$exists: true, $ne: null}});
const nonemptyPhotoPathCount = targetCollection.countDocuments({
  photo_path: {$exists: true, $nin: ["", null]}
});
const forbiddenPhotoFieldCount = targetCollection.countDocuments({$or: [
  {photo: {$exists: true}}, {photos: {$exists: true}}, {image: {$exists: true}},
  {image_data: {$exists: true}}, {base64: {$exists: true}},
  {photo_data: {$exists: true}}, {raw_photo: {$exists: true}}
]});
print(JSON.stringify({
  person_document_count: documentCount,
  feature_document_count: featureCount,
  nonempty_photo_path_count: nonemptyPhotoPathCount,
  forbidden_photo_field_count: forbiddenPhotoFieldCount
}));
""" % (json.dumps(payload["mongodb_database"]), json.dumps(payload["mongodb_collection"]))
mongo_raw = checked([
    "docker", "exec", mongodb_id,
    "mongosh", "--quiet",
    "--username", mongo_user,
    "--password", mongo_password,
    "--authenticationDatabase", "admin",
    "--eval", mongo_script,
])
mongo_lines = [line for line in mongo_raw.splitlines() if line.strip()]
if not mongo_lines:
    raise RuntimeError("mongodb observation output is missing")
mongo = json.loads(mongo_lines[-1])
for field in (
    "person_document_count",
    "feature_document_count",
    "nonempty_photo_path_count",
    "forbidden_photo_field_count",
):
    value = mongo.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RuntimeError("mongodb observation count is invalid")

persistent = {
    "persistent_paths_observed": len(payload["persistent_paths"]),
    "persistent_paths_existing": 0,
    "persistent_regular_files": 0,
    "persistent_symlinks": 0,
    "persistent_person_photo_named_files": 0,
    "persistent_forbidden_digest_matches": 0,
}
for root in payload["persistent_paths"]:
    if not os.path.isdir(root) or os.path.islink(root):
        continue
    persistent["persistent_paths_existing"] += 1
    for current, directories, filenames in os.walk(root, followlinks=False):
        kept = []
        for name in directories:
            path = os.path.join(current, name)
            if os.path.islink(path):
                persistent["persistent_symlinks"] += 1
            else:
                kept.append(name)
        directories[:] = kept
        named_photo_directory = any(
            part.lower() in {"person_photos", "person-photo", "person_photo"}
            for part in current.split(os.sep)
        )
        for name in filenames:
            path = os.path.join(current, name)
            if os.path.islink(path):
                persistent["persistent_symlinks"] += 1
                continue
            if not os.path.isfile(path):
                continue
            persistent["persistent_regular_files"] += 1
            if named_photo_directory:
                persistent["persistent_person_photo_named_files"] += 1
            if (
                os.path.getsize(path) == payload["person_photo_size_bytes"]
                and sha256_file(path) == payload["person_photo_sha256"]
            ):
                persistent["persistent_forbidden_digest_matches"] += 1

print(json.dumps({
    "schema_version": 1,
    **totals,
    "mongodb_identity_verified": True,
    **mongo,
    **persistent,
}, sort_keys=True))
'''


class FacePhotoResidueRemoteDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: int
    facerec_container_count: int = Field(ge=0)
    facerec_identity_verified_count: int = Field(ge=0)
    facerec_save_person_photo_false_count: int = Field(ge=0)
    online_gateway_container_count: int = Field(ge=0)
    online_gateway_identity_verified_count: int = Field(ge=0)
    container_photo_paths_observed: int = Field(ge=0)
    container_photo_paths_existing: int = Field(ge=0)
    container_photo_regular_files: int = Field(ge=0)
    container_photo_symlinks: int = Field(ge=0)
    container_photo_forbidden_digest_matches: int = Field(ge=0)
    log_paths_observed: int = Field(ge=0)
    log_paths_existing: int = Field(ge=0)
    log_regular_files: int = Field(ge=0)
    log_symlinks: int = Field(ge=0)
    log_sensitive_marker_files: int = Field(ge=0)
    log_forbidden_digest_matches: int = Field(ge=0)
    mongodb_identity_verified: bool
    person_document_count: int = Field(ge=0)
    feature_document_count: int = Field(ge=0)
    nonempty_photo_path_count: int = Field(ge=0)
    forbidden_photo_field_count: int = Field(ge=0)
    persistent_paths_observed: int = Field(ge=0)
    persistent_paths_existing: int = Field(ge=0)
    persistent_regular_files: int = Field(ge=0)
    persistent_symlinks: int = Field(ge=0)
    persistent_person_photo_named_files: int = Field(ge=0)
    persistent_forbidden_digest_matches: int = Field(ge=0)

    @model_validator(mode="after")
    def schema_is_current(self) -> FacePhotoResidueRemoteDocument:
        if self.schema_version != 1:
            raise ValueError("人脸原图残留证据 schema_version 不合法")
        return self


class ResidueCommandRunner(Protocol):
    async def run(
        self,
        argv: Sequence[str],
        *,
        stdin: bytes,
        timeout_seconds: float,
    ) -> CommandResult: ...


@dataclass(frozen=True, slots=True)
class FacePhotoResidueResult:
    status: str
    reason: str
    fixture_id: str
    fixture_evidence_id: str
    observation_binding_sha256: str
    expected_container_count: int
    expected_log_observations: int
    expected_persistent_observations: int
    document: FacePhotoResidueRemoteDocument | None = None

    def __post_init__(self) -> None:
        if self.status not in {"passed", "failed"} or not self.reason:
            raise ValueError("人脸原图残留结果状态或原因不合法")

    def to_evidence(self) -> dict[str, object]:
        evidence: dict[str, object] = {
            "fixture_id": self.fixture_id,
            "fixture_evidence_id": self.fixture_evidence_id,
            "observation_binding_sha256": self.observation_binding_sha256,
            "expected_container_count": self.expected_container_count,
            "expected_log_observations": self.expected_log_observations,
            "expected_persistent_observations": self.expected_persistent_observations,
        }
        if self.document is not None:
            evidence["observations"] = self.document.model_dump()
        return evidence


class SshFacePhotoResidueAdapter:
    def __init__(
        self,
        *,
        target_hostname: str,
        ssh_user: str,
        ssh_port: int,
        facerec_compose_project: str,
        facerec_container_ids: Mapping[str, str],
        mongodb_compose_project: str,
        mongodb_compose_service: str,
        mongodb_container_id: str,
        mongodb_database: str,
        mongodb_collection: str,
        online_gateway_compose_project: str,
        online_gateway_compose_service: str,
        online_gateway_container_id: str,
        container_photo_paths: Sequence[str],
        container_log_paths: Sequence[str],
        persistent_paths: Sequence[str],
        fixture_id: str,
        fixture_evidence_id: str,
        person_photo_sha256: str,
        person_photo_size_bytes: int,
        enabled: bool = False,
        command_runner: ResidueCommandRunner | None = None,
        probe_timeout_seconds: float = 120,
    ) -> None:
        if _SAFE_HOST.fullmatch(target_hostname) is None:
            raise ValueError("目标主机名不安全")
        if _SAFE_USER.fullmatch(ssh_user) is None:
            raise ValueError("SSH 用户名不安全")
        if type(ssh_port) is not int or not 1 <= ssh_port <= 65535:
            raise ValueError("SSH 端口不合法")
        if (
            isinstance(probe_timeout_seconds, bool)
            or not isinstance(probe_timeout_seconds, (int, float))
            or not math.isfinite(probe_timeout_seconds)
            or not 0 < probe_timeout_seconds <= 900
        ):
            raise ValueError("残留探针超时必须位于 0–900 秒")
        frozen_ids = dict(facerec_container_ids)
        if tuple(sorted(frozen_ids)) != tuple(sorted(_FACEREC_SERVICES)):
            raise ValueError("FaceRec 容器必须精确覆盖 gpu0/gpu1/gpu2")
        all_ids = (
            *frozen_ids.values(),
            mongodb_container_id,
            online_gateway_container_id,
        )
        if any(_CONTAINER_ID.fullmatch(value) is None for value in all_ids):
            raise ValueError("残留探针必须使用完整容器 ID")
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("残留探针容器 ID 不能重复")
        if _SAFE_EVIDENCE_ID.fullmatch(fixture_evidence_id) is None:
            raise ValueError("fixture evidence ID 不安全")
        if _SHA256.fullmatch(person_photo_sha256) is None:
            raise ValueError("人物原图 SHA256 不合法")
        if type(person_photo_size_bytes) is not int or person_photo_size_bytes <= 0:
            raise ValueError("人物原图大小必须是正整数")
        for values, name in (
            (container_photo_paths, "容器人物照片目录"),
            (container_log_paths, "FaceRec 日志目录"),
            (persistent_paths, "持久目录"),
        ):
            if not values or any(
                not value.startswith("/") or ".." in value.split("/") for value in values
            ):
                raise ValueError(f"{name} 必须是非空绝对路径数组")
            if len(values) != len(set(values)):
                raise ValueError(f"{name} 不能重复")

        self.target_hostname = target_hostname
        self._ssh_user = ssh_user
        self._ssh_port = ssh_port
        self._enabled = enabled
        self._runner = command_runner or AsyncSubprocessRunner()
        self._timeout = float(probe_timeout_seconds)
        self._fixture_id = fixture_id
        self._fixture_evidence_id = fixture_evidence_id
        self._photo_sha256 = person_photo_sha256
        self._photo_size_bytes = person_photo_size_bytes
        self._photo_paths = tuple(container_photo_paths)
        self._log_paths = tuple(container_log_paths)
        self._persistent_paths = tuple(persistent_paths)
        self._payload = {
            "schema_version": 1,
            "facerec_compose_project": facerec_compose_project,
            "facerec_container_ids": {
                service: frozen_ids[service] for service in _FACEREC_SERVICES
            },
            "mongodb_compose_project": mongodb_compose_project,
            "mongodb_compose_service": mongodb_compose_service,
            "mongodb_container_id": mongodb_container_id,
            "mongodb_database": mongodb_database,
            "mongodb_collection": mongodb_collection,
            "online_gateway_compose_project": online_gateway_compose_project,
            "online_gateway_compose_service": online_gateway_compose_service,
            "online_gateway_container_id": online_gateway_container_id,
            "container_photo_paths": list(self._photo_paths),
            "container_log_paths": list(self._log_paths),
            "persistent_paths": list(self._persistent_paths),
            "person_photo_sha256": person_photo_sha256,
            "person_photo_size_bytes": person_photo_size_bytes,
        }
        self._binding_sha256 = hashlib.sha256(
            json.dumps(self._payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def _result(
        self,
        status: str,
        reason: str,
        document: FacePhotoResidueRemoteDocument | None = None,
    ) -> FacePhotoResidueResult:
        return FacePhotoResidueResult(
            status=status,
            reason=reason,
            fixture_id=self._fixture_id,
            fixture_evidence_id=self._fixture_evidence_id,
            observation_binding_sha256=self._binding_sha256,
            expected_container_count=len(_FACEREC_SERVICES),
            expected_log_observations=(len(_FACEREC_SERVICES) + 1)
            * len(self._log_paths),
            expected_persistent_observations=len(self._persistent_paths),
            document=document,
        )

    async def run(self) -> FacePhotoResidueResult:
        if not self._enabled:
            return self._result("failed", "人脸原图残留远程探针未显式启用")
        argv = (
            "ssh",
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"ConnectTimeout={min(10, max(1, math.ceil(self._timeout)))}",
            "-p",
            str(self._ssh_port),
            "--",
            f"{self._ssh_user}@{self.target_hostname}",
            f"python3 -c {shlex.quote(_REMOTE_SCRIPT)}",
        )
        command = await self._runner.run(
            argv,
            stdin=json.dumps(self._payload, sort_keys=True).encode(),
            timeout_seconds=self._timeout,
        )
        if command.returncode != 0:
            return self._result(
                "failed",
                f"人脸原图残留观察失败，退出码 {command.returncode}",
            )
        try:
            raw: object = json.loads(command.stdout)
            document = FacePhotoResidueRemoteDocument.model_validate(raw)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return self._result("failed", "人脸原图残留观察证据缺失或无效")

        observations_complete = (
            document.facerec_container_count == len(_FACEREC_SERVICES)
            and document.facerec_identity_verified_count == len(_FACEREC_SERVICES)
            and document.facerec_save_person_photo_false_count
            == len(_FACEREC_SERVICES)
            and document.online_gateway_container_count == 1
            and document.online_gateway_identity_verified_count == 1
            and document.mongodb_identity_verified
            and document.container_photo_paths_observed
            == len(_FACEREC_SERVICES) * len(self._photo_paths)
            and document.log_paths_observed
            == (len(_FACEREC_SERVICES) + 1) * len(self._log_paths)
            and document.log_paths_existing == document.log_paths_observed
            and document.persistent_paths_observed == len(self._persistent_paths)
            and document.persistent_paths_existing == document.persistent_paths_observed
        )
        if not observations_complete:
            return self._result("failed", "FaceRec、MongoDB、日志或持久目录观察不完整", document)
        if (
            document.person_document_count == 0
            or document.feature_document_count != document.person_document_count
        ):
            return self._result("failed", "MongoDB 人物特征观察缺失或不完整", document)
        residue_count = sum(
            (
                document.container_photo_regular_files,
                document.container_photo_symlinks,
                document.container_photo_forbidden_digest_matches,
                document.log_symlinks,
                document.log_sensitive_marker_files,
                document.log_forbidden_digest_matches,
                document.nonempty_photo_path_count,
                document.forbidden_photo_field_count,
                document.persistent_symlinks,
                document.persistent_person_photo_named_files,
                document.persistent_forbidden_digest_matches,
            )
        )
        if residue_count:
            return self._result("failed", "发现人脸原图残留或敏感媒体标记", document)
        return self._result("passed", "四类人脸原图残留观察完整且未发现残留", document)
