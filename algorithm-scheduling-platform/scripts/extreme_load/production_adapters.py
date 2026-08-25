from __future__ import annotations

import json
import math
import os
import re
import stat
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from .catalog import CampaignPhase, CaseSpec, FixtureDescriptor, FixtureKind
from .face_photo_residue import SshFacePhotoResidueAdapter
from .guardrails import GuardrailAssessment, GuardrailLevel
from .media_download import SourceResourceEvidence, SshMediaDownloadAdapter
from .metrics import SamplingSchedule
from .plan import CampaignPlan
from .runtime_metrics import RuntimeMetricsAdapter
from .stage_runtime import (
    StageCaseAdapter,
    StageCaseOutcome,
    StageCaseStatus,
    StageCheckpoint,
    StageMetricsAdapter,
)
from .system_probes import (
    TARGET_DATA_DIRECTORY_PATHS,
    ControlMetricsProbe,
    DockerMetricsProbe,
    GatewayMetricsProbe,
    HttpxProbeClient,
    KafkaLagProbe,
    LoadHostProbe,
    NvidiaSmiProbe,
    SshCommandRunner,
    SshTarget,
    SubprocessCommandRunner,
    TargetHostProbe,
)

RUNTIME_CONFIG_ENV = "ALGORITHM_CAMPAIGN_RUNTIME_CONFIG"

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
_EXPECTED_CONTROL_HOSTNAME = "192.168.29.11"
_EXPECTED_MEDIA_SOURCE_HOSTNAME = "192.168.29.12"
_EXPECTED_MEDIA_SOURCE_PORT = 5555
_EXPECTED_FIXTURE_IDS = ("long-teacher", "long-student", "long-slides")
_ALLOWED_CONCURRENCY = frozenset({1, 3, 10, 30})
_CONFIG_KEYS = frozenset(
    {
        "enabled",
        "target_hostname",
        "ssh_user",
        "ssh_port",
        "download_timeout_seconds",
        "source_resource_evidence_path",
    }
)
_RUNTIME_TOP_LEVEL_KEYS = frozenset(
    {"schema_version", "media_download", "runtime_metrics", "face_photo_residue", "fault"}
)
_METRICS_CONFIG_KEYS = frozenset(
    {
        "enabled",
        "target_hostname",
        "ssh_user",
        "ssh_port",
        "filesystem_paths",
        "compose_projects",
        "regular_seconds",
        "burst_seconds",
        "probe_timeout_seconds",
        "probe_attempts",
        "probe_retry_delay_seconds",
        "restart_loop_threshold",
        "restart_loop_window_seconds",
        "database_services",
        "critical_container_services",
        "expected_gpu_by_pid",
        "kafka_compose_project",
        "kafka_compose_service",
        "kafka_consumer_groups",
        "kafka_probe_timeout_seconds",
        "kafka_probe_attempts",
        "kafka_probe_retry_delay_seconds",
    }
)
_SOURCE_EVIDENCE_KEYS = frozenset(
    {
        "evidence_id",
        "collected_at",
        "cpu_percent",
        "memory_percent",
        "network_transmit_bytes_per_second",
        "open_connections",
    }
)
_FACE_RESIDUE_CONFIG_KEYS = frozenset(
    {
        "enabled",
        "target_hostname",
        "ssh_user",
        "ssh_port",
        "probe_timeout_seconds",
        "fixture_evidence_id",
        "person_photo_sha256",
        "person_photo_size_bytes",
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
    }
)
_FULL_CONTAINER_ID = re.compile(r"[0-9a-f]{64}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_FACEREC_SERVICES = ("facerec-gpu0", "facerec-gpu1", "facerec-gpu2")


class _ConfigurationBlocked(ValueError):
    def __init__(self, state: str, reason: str) -> None:
        super().__init__(reason)
        self.state = state
        self.reason = reason


class _CaseRejected(ValueError):
    def __init__(self, state: str, reason: str) -> None:
        super().__init__(reason)
        self.state = state
        self.reason = reason


@dataclass(frozen=True, slots=True)
class _MediaDownloadSettings:
    target_hostname: str
    ssh_user: str
    ssh_port: int
    download_timeout_seconds: float
    source_evidence: SourceResourceEvidence


@dataclass(frozen=True, slots=True)
class _RuntimeMetricsSettings:
    target_hostname: str
    ssh_user: str
    ssh_port: int
    filesystem_paths: tuple[str, ...]
    compose_projects: tuple[str, ...]
    schedule: SamplingSchedule
    probe_timeout_seconds: float
    probe_attempts: int
    probe_retry_delay_seconds: float
    restart_loop_threshold: int
    restart_loop_window_seconds: float
    database_services: tuple[str, ...]
    critical_container_services: tuple[str, ...]
    expected_gpu_by_pid: Mapping[int, str]
    kafka_compose_project: str
    kafka_compose_service: str
    kafka_consumer_groups: tuple[str, ...]
    kafka_probe_timeout_seconds: float
    kafka_probe_attempts: int
    kafka_probe_retry_delay_seconds: float


@dataclass(frozen=True, slots=True)
class _FacePhotoResidueSettings:
    target_hostname: str
    ssh_user: str
    ssh_port: int
    probe_timeout_seconds: float
    fixture_evidence_id: str
    person_photo_sha256: str
    person_photo_size_bytes: int
    facerec_container_ids: Mapping[str, str]
    mongodb_container_id: str
    online_gateway_container_id: str


def _as_string_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise ValueError("配置对象必须使用字符串字段")
    return cast(Mapping[str, object], value)


def _is_within_workspace(path: Path) -> bool:
    return path == _WORKSPACE_ROOT or _WORKSPACE_ROOT in path.parents


def _read_external_0600(path: Path, *, suffix: str, max_bytes: int) -> bytes:
    if not path.is_absolute() or path.suffix.lower() != suffix:
        raise ValueError("外部文件必须使用绝对路径和预期扩展名")
    try:
        metadata = os.lstat(path)
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError("外部文件不存在或无法解析") from error
    if _is_within_workspace(resolved):
        raise ValueError("运行时输入必须位于 Git 工作区外")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("外部文件必须是普通文件且不能是符号链接")
    if metadata.st_uid != os.getuid():
        raise PermissionError("外部文件必须归当前 UID 所有")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise PermissionError("外部文件权限必须精确为 0600")
    if metadata.st_nlink != 1:
        raise PermissionError("外部文件必须是单硬链接文件")
    if metadata.st_size > max_bytes:
        raise ValueError("外部文件超过大小上限")

    descriptor = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise ValueError("外部文件在打开期间发生替换")
        if (
            opened.st_uid != metadata.st_uid
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_nlink != 1
        ):
            raise PermissionError("外部文件在打开期间的身份或权限不合法")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        after = os.fstat(descriptor)
        if (
            len(content) > max_bytes
            or len(content) != opened.st_size
            or after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
            or after.st_ctime_ns != opened.st_ctime_ns
        ):
            raise ValueError("外部文件在读取期间发生修改或超限")
        return content
    finally:
        os.close(descriptor)


def _number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} 必须是有限数值")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field_name} 必须是有限数值")
    return result


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field_name} 必须是非空字符串数组")
    result = tuple(value)
    if any(type(item) is not str or not item for item in result) or len(result) != len(set(result)):
        raise ValueError(f"{field_name} 必须是唯一非空字符串数组")
    return cast(tuple[str, ...], result)


def _string(value: object, field_name: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{field_name} 必须是非空字符串")
    return value


def _runtime_document() -> Mapping[str, object]:
    config_value = os.environ.get(RUNTIME_CONFIG_ENV)
    if not config_value:
        raise _ConfigurationBlocked(
            "config_missing",
            f"缺少 {RUNTIME_CONFIG_ENV} 显式运行时配置",
        )
    config_path = Path(config_value)
    if config_path.name == ".env":
        raise ValueError("运行时配置不能使用 .env")
    content = _read_external_0600(config_path, suffix=".toml", max_bytes=64 * 1024)
    raw: object = tomllib.loads(content.decode("utf-8"))
    document = _as_string_mapping(raw)
    keys = frozenset(document)
    if (
        "schema_version" not in keys
        or not keys <= _RUNTIME_TOP_LEVEL_KEYS
        or type(document["schema_version"]) is not int
        or document["schema_version"] != 1
    ):
        raise ValueError("运行时配置顶层字段或 schema_version 不合法")
    return document


def _load_source_evidence(path_value: object) -> SourceResourceEvidence:
    if type(path_value) is not str or not path_value:
        raise _ConfigurationBlocked("source_evidence_unavailable", "缺少源端资源证据 JSON")
    try:
        content = _read_external_0600(
            Path(path_value),
            suffix=".json",
            max_bytes=64 * 1024,
        )
        raw: object = json.loads(content)
        document = _as_string_mapping(raw)
        if frozenset(document) != _SOURCE_EVIDENCE_KEYS:
            raise ValueError("源端资源证据字段不完整或包含未知字段")
        evidence_id = document["evidence_id"]
        collected_at = document["collected_at"]
        open_connections = document["open_connections"]
        if type(evidence_id) is not str or not evidence_id:
            raise ValueError("源端资源证据标识不合法")
        if type(collected_at) is not str or not collected_at:
            raise ValueError("源端资源证据时间不合法")
        parsed_time = datetime.fromisoformat(collected_at)
        if parsed_time.tzinfo is None or parsed_time.utcoffset() is None:
            raise ValueError("源端资源证据时间必须包含时区")
        if type(open_connections) is not int or open_connections < 0:
            raise ValueError("源端连接数必须是非负整数")
        return SourceResourceEvidence(
            evidence_id=evidence_id,
            collected_at=collected_at,
            cpu_percent=_number(document["cpu_percent"], "cpu_percent"),
            memory_percent=_number(document["memory_percent"], "memory_percent"),
            network_transmit_bytes_per_second=_number(
                document["network_transmit_bytes_per_second"],
                "network_transmit_bytes_per_second",
            ),
            open_connections=open_connections,
        )
    except _ConfigurationBlocked:
        raise
    except (
        OSError,
        PermissionError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        raise _ConfigurationBlocked(
            "source_evidence_unavailable",
            "源端资源证据缺失或不符合工作区外 0600 JSON 合同",
        ) from error


def _load_settings() -> _MediaDownloadSettings:
    try:
        document = _runtime_document()
        if "media_download" not in document:
            raise ValueError("运行时配置缺少 media_download")
        media = _as_string_mapping(document["media_download"])
        enabled = media.get("enabled")
        if enabled is not True:
            raise _ConfigurationBlocked(
                "disabled",
                "媒体下载远程执行未显式 enabled=true",
            )
        if frozenset(media) != _CONFIG_KEYS:
            raise ValueError("media_download 配置字段不完整或包含未知字段")
        target_hostname = media["target_hostname"]
        ssh_user = media["ssh_user"]
        ssh_port = media["ssh_port"]
        if target_hostname != _EXPECTED_CONTROL_HOSTNAME or ssh_user != "root" or ssh_port != 22:
            raise ValueError("SSH 目标必须是已批准的 root@192.168.29.11:22")
        timeout = _number(media["download_timeout_seconds"], "download_timeout_seconds")
        if not 0 < timeout <= 86_400:
            raise ValueError("下载超时必须位于 0–86400 秒")
        source_evidence = _load_source_evidence(media["source_resource_evidence_path"])
        return _MediaDownloadSettings(
            target_hostname=_EXPECTED_CONTROL_HOSTNAME,
            ssh_user="root",
            ssh_port=22,
            download_timeout_seconds=timeout,
            source_evidence=source_evidence,
        )
    except _ConfigurationBlocked:
        raise
    except (
        OSError,
        PermissionError,
        UnicodeDecodeError,
        ValueError,
        tomllib.TOMLDecodeError,
    ) as error:
        raise _ConfigurationBlocked(
            "config_invalid",
            "媒体下载运行时配置不符合工作区外 0600 TOML 合同",
        ) from error


def _load_metrics_settings() -> _RuntimeMetricsSettings:
    try:
        document = _runtime_document()
        if "runtime_metrics" not in document:
            raise ValueError("运行时配置缺少 runtime_metrics")
        metrics = _as_string_mapping(document["runtime_metrics"])
        if metrics.get("enabled") is not True:
            raise _ConfigurationBlocked(
                "disabled",
                "运行时远程指标探针未显式 enabled=true",
            )
        if frozenset(metrics) != _METRICS_CONFIG_KEYS:
            raise ValueError("runtime_metrics 配置字段不完整或包含未知字段")
        target_hostname = metrics["target_hostname"]
        ssh_user = metrics["ssh_user"]
        ssh_port = metrics["ssh_port"]
        if target_hostname != _EXPECTED_CONTROL_HOSTNAME or ssh_user != "root" or ssh_port != 22:
            raise ValueError("SSH 目标必须是已批准的 root@192.168.29.11:22")
        timeout = _number(metrics["probe_timeout_seconds"], "probe_timeout_seconds")
        if not 0 < timeout <= 30:
            raise ValueError("探针超时必须位于 0–30 秒")
        probe_attempts = metrics["probe_attempts"]
        if type(probe_attempts) is not int or not 1 <= probe_attempts <= 2:
            raise ValueError("指标采集尝试次数必须位于 1–2")
        probe_retry_delay_seconds = _number(
            metrics["probe_retry_delay_seconds"],
            "probe_retry_delay_seconds",
        )
        if not 0 <= probe_retry_delay_seconds <= 5:
            raise ValueError("指标采集重试间隔必须位于 0–5 秒")
        kafka_probe_timeout = _number(
            metrics["kafka_probe_timeout_seconds"],
            "kafka_probe_timeout_seconds",
        )
        if not 15 <= kafka_probe_timeout <= 30:
            raise ValueError("Kafka lag 探针超时必须位于 15–30 秒")
        restart_threshold = metrics["restart_loop_threshold"]
        if type(restart_threshold) is not int or restart_threshold <= 0:
            raise ValueError("容器重启阈值必须是正整数")
        kafka_probe_attempts = metrics["kafka_probe_attempts"]
        if type(kafka_probe_attempts) is not int or not 1 <= kafka_probe_attempts <= 2:
            raise ValueError("Kafka lag 探针尝试次数必须位于 1–2")
        kafka_probe_retry_delay_seconds = _number(
            metrics["kafka_probe_retry_delay_seconds"],
            "kafka_probe_retry_delay_seconds",
        )
        if not 0 <= kafka_probe_retry_delay_seconds <= 5:
            raise ValueError("Kafka lag 探针重试间隔必须位于 0–5 秒")
        expected_raw = _as_string_mapping(metrics["expected_gpu_by_pid"])
        expected: dict[int, str] = {}
        for raw_pid, raw_uuid in expected_raw.items():
            if not raw_pid.isdecimal() or type(raw_uuid) is not str or not raw_uuid:
                raise ValueError("expected_gpu_by_pid 必须是 PID 字符串到 GPU UUID")
            pid = int(raw_pid)
            if pid <= 0 or pid in expected:
                raise ValueError("expected_gpu_by_pid 包含非法或重复 PID")
            expected[pid] = raw_uuid
        return _RuntimeMetricsSettings(
            target_hostname=_EXPECTED_CONTROL_HOSTNAME,
            ssh_user="root",
            ssh_port=22,
            filesystem_paths=_string_tuple(metrics["filesystem_paths"], "filesystem_paths"),
            compose_projects=_string_tuple(metrics["compose_projects"], "compose_projects"),
            schedule=SamplingSchedule(
                regular_seconds=_number(metrics["regular_seconds"], "regular_seconds"),
                burst_seconds=_number(metrics["burst_seconds"], "burst_seconds"),
            ),
            probe_timeout_seconds=timeout,
            probe_attempts=probe_attempts,
            probe_retry_delay_seconds=probe_retry_delay_seconds,
            restart_loop_threshold=restart_threshold,
            restart_loop_window_seconds=_number(
                metrics["restart_loop_window_seconds"],
                "restart_loop_window_seconds",
            ),
            database_services=_string_tuple(metrics["database_services"], "database_services"),
            critical_container_services=_string_tuple(
                metrics["critical_container_services"],
                "critical_container_services",
            ),
            expected_gpu_by_pid=expected,
            kafka_compose_project=_string(
                metrics["kafka_compose_project"], "kafka_compose_project"
            ),
            kafka_compose_service=_string(
                metrics["kafka_compose_service"], "kafka_compose_service"
            ),
            kafka_consumer_groups=_string_tuple(
                metrics["kafka_consumer_groups"],
                "kafka_consumer_groups",
            ),
            kafka_probe_timeout_seconds=kafka_probe_timeout,
            kafka_probe_attempts=kafka_probe_attempts,
            kafka_probe_retry_delay_seconds=kafka_probe_retry_delay_seconds,
        )
    except _ConfigurationBlocked:
        raise
    except (
        OSError,
        PermissionError,
        UnicodeDecodeError,
        ValueError,
        tomllib.TOMLDecodeError,
    ) as error:
        raise _ConfigurationBlocked(
            "config_invalid",
            "运行时指标配置不符合工作区外 0600 TOML 合同",
        ) from error


def _load_face_photo_residue_settings() -> _FacePhotoResidueSettings:
    try:
        document = _runtime_document()
        if "face_photo_residue" not in document:
            raise ValueError("运行时配置缺少 face_photo_residue")
        residue = _as_string_mapping(document["face_photo_residue"])
        if residue.get("enabled") is not True:
            raise _ConfigurationBlocked(
                "disabled",
                "FaceRec 原图残留远程探针未显式 enabled=true",
            )
        if frozenset(residue) != _FACE_RESIDUE_CONFIG_KEYS:
            raise ValueError("face_photo_residue 配置字段不完整或包含未知字段")
        if (
            residue["target_hostname"] != _EXPECTED_CONTROL_HOSTNAME
            or residue["ssh_user"] != "root"
            or residue["ssh_port"] != 22
        ):
            raise ValueError("SSH 目标必须是已批准的 root@192.168.29.11:22")
        if (
            residue["facerec_compose_project"] != "algorithm-operators"
            or residue["mongodb_compose_project"] != "algorithm-scheduling-platform"
            or residue["mongodb_compose_service"] != "mongodb"
            or residue["mongodb_database"] != "facerecapi"
            or residue["mongodb_collection"] != "persons"
            or residue["online_gateway_compose_project"]
            != "algorithm-scheduling-platform"
            or residue["online_gateway_compose_service"] != "online-gateway-service"
        ):
            raise ValueError("FaceRec 或 MongoDB Compose/数据库身份不合法")
        container_ids = _as_string_mapping(residue["facerec_container_ids"])
        if set(container_ids) != set(_FACEREC_SERVICES):
            raise ValueError("FaceRec 容器 ID 必须精确覆盖三个服务")
        frozen_ids = {service: container_ids[service] for service in _FACEREC_SERVICES}
        mongodb_id = residue["mongodb_container_id"]
        online_gateway_id = residue["online_gateway_container_id"]
        if any(
            type(value) is not str or _FULL_CONTAINER_ID.fullmatch(value) is None
            for value in (*frozen_ids.values(), mongodb_id, online_gateway_id)
        ):
            raise ValueError("FaceRec/MongoDB 必须使用完整容器 ID")
        if len({*frozen_ids.values(), mongodb_id, online_gateway_id}) != 5:
            raise ValueError("FaceRec/MongoDB/Gateway 容器 ID 不能重复")
        photo_sha256 = residue["person_photo_sha256"]
        photo_size = residue["person_photo_size_bytes"]
        if type(photo_sha256) is not str or _SHA256.fullmatch(photo_sha256) is None:
            raise ValueError("人物原图摘要不合法")
        if type(photo_size) is not int or photo_size <= 0:
            raise ValueError("人物原图大小必须是正整数")
        if _string_tuple(residue["container_photo_paths"], "container_photo_paths") != (
            "/app/media/person_photos",
        ):
            raise ValueError("FaceRec 人物照片扫描目录不合法")
        if _string_tuple(residue["container_log_paths"], "container_log_paths") != (
            "/app/logs",
        ):
            raise ValueError("FaceRec 日志扫描目录不合法")
        if _string_tuple(residue["persistent_paths"], "persistent_paths") != (
            "/data/result",
        ):
            raise ValueError("FaceRec 持久扫描目录不合法")
        timeout = _number(residue["probe_timeout_seconds"], "probe_timeout_seconds")
        if not 0 < timeout <= 900:
            raise ValueError("残留探针超时必须位于 0–900 秒")
        return _FacePhotoResidueSettings(
            target_hostname=_EXPECTED_CONTROL_HOSTNAME,
            ssh_user="root",
            ssh_port=22,
            probe_timeout_seconds=timeout,
            fixture_evidence_id=_string(
                residue["fixture_evidence_id"], "fixture_evidence_id"
            ),
            person_photo_sha256=photo_sha256,
            person_photo_size_bytes=photo_size,
            facerec_container_ids=cast(Mapping[str, str], frozen_ids),
            mongodb_container_id=cast(str, mongodb_id),
            online_gateway_container_id=cast(str, online_gateway_id),
        )
    except _ConfigurationBlocked:
        raise
    except (
        OSError,
        PermissionError,
        UnicodeDecodeError,
        ValueError,
        tomllib.TOMLDecodeError,
    ) as error:
        raise _ConfigurationBlocked(
            "config_invalid",
            "FaceRec 原图残留配置不符合工作区外 0600 TOML 合同",
        ) from error


def _case_fixtures(plan: CampaignPlan, case: CaseSpec) -> tuple[int, Sequence[FixtureDescriptor]]:
    if case.phase is not CampaignPhase.BASELINE:
        raise _CaseRejected("case_invalid", "媒体下载适配器只接受 baseline 阶段用例")
    if set(case.load) != {"kind", "concurrency"} or case.load.get("kind") != "media_download":
        raise _CaseRejected("case_invalid", "阶段用例不是规范 media_download 用例")
    concurrency = case.load.get("concurrency")
    if type(concurrency) is not int or concurrency not in _ALLOWED_CONCURRENCY:
        raise _CaseRejected("case_invalid", "媒体下载并发只允许 1/3/10/30")
    if case.fixture_ids != _EXPECTED_FIXTURE_IDS:
        raise _CaseRejected("fixture_invalid", "媒体下载用例必须绑定规范 T/S/P 长课 fixture")

    manifest = {fixture.fixture_id: fixture for fixture in plan.fixture_manifest.fixtures}
    fixtures: list[FixtureDescriptor] = []
    for fixture_id in case.fixture_ids:
        fixture = manifest.get(fixture_id)
        if fixture is None or fixture.kind is not FixtureKind.LONG_COURSE:
            raise _CaseRejected("fixture_invalid", "媒体下载用例的长课 fixture 缺失或类型不符")
        if fixture.duration_seconds is None or not 2_700 <= fixture.duration_seconds <= 3_600:
            raise _CaseRejected("fixture_invalid", "媒体下载长课 fixture 时长必须为 45–60 分钟")
        parsed = urlsplit(fixture.path)
        try:
            port = parsed.port
        except ValueError as error:
            raise _CaseRejected("fixture_invalid", "媒体下载 fixture URL 不合法") from error
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname != _EXPECTED_MEDIA_SOURCE_HOSTNAME
            or port != _EXPECTED_MEDIA_SOURCE_PORT
            or not parsed.path
            or parsed.fragment
        ):
            raise _CaseRejected(
                "fixture_invalid",
                "媒体下载 fixture 必须使用已批准媒体源的 HTTP/HTTPS URL",
            )
        fixtures.append(fixture)
    return concurrency, tuple(fixtures)


class _ProductionMediaDownloadStageAdapter(StageCaseAdapter):
    def __init__(
        self,
        plan: CampaignPlan,
        *,
        adapter: SshMediaDownloadAdapter | None,
        blocked_state: str | None = None,
        blocked_reason: str | None = None,
    ) -> None:
        self._plan = plan
        self._adapter = adapter
        self._blocked_state = blocked_state
        self._blocked_reason = blocked_reason

    async def execute(self, case: CaseSpec) -> StageCaseOutcome:
        try:
            concurrency, fixtures = _case_fixtures(self._plan, case)
        except _CaseRejected as error:
            return StageCaseOutcome(
                "failed",
                error.reason,
                {"validation_state": error.state},
            )

        if self._adapter is None:
            return StageCaseOutcome(
                "blocked",
                self._blocked_reason or "媒体下载运行时适配器不可用",
                {"configuration_state": self._blocked_state or "unavailable"},
            )

        control_hostname = urlsplit(self._plan.control_origin).hostname
        if (
            control_hostname != _EXPECTED_CONTROL_HOSTNAME
            or self._adapter.target_hostname != control_hostname
        ):
            return StageCaseOutcome(
                "blocked",
                "SSH 目标主机与 Campaign Control hostname 不一致",
                {"configuration_state": "target_mismatch"},
            )
        try:
            result = await self._adapter.run(fixtures, concurrency=concurrency)
        except Exception as error:
            return StageCaseOutcome(
                "failed",
                f"媒体下载适配器执行异常: {type(error).__name__}",
                {"error_type": type(error).__name__},
            )
        if result.concurrency != concurrency:
            return StageCaseOutcome(
                "failed",
                "媒体下载适配器返回的并发档位与用例不一致",
                {"validation_state": "result_mismatch"},
            )
        status = cast(StageCaseStatus, result.status)
        evidence = result.to_evidence()
        evidence.update(
            {
                "target_hostname": _EXPECTED_CONTROL_HOSTNAME,
                "ssh_port": 22,
            }
        )
        return StageCaseOutcome(status, result.reason, evidence)


def media_download_factory(
    plan: CampaignPlan,
    release_root: Path,
) -> StageCaseAdapter:
    del release_root
    try:
        settings = _load_settings()
    except _ConfigurationBlocked as error:
        return _ProductionMediaDownloadStageAdapter(
            plan,
            adapter=None,
            blocked_state=error.state,
            blocked_reason=error.reason,
        )
    adapter = SshMediaDownloadAdapter(
        target_hostname=settings.target_hostname,
        ssh_user=settings.ssh_user,
        ssh_port=settings.ssh_port,
        enabled=True,
        source_evidence=settings.source_evidence,
        download_timeout_seconds=settings.download_timeout_seconds,
    )
    return _ProductionMediaDownloadStageAdapter(plan, adapter=adapter)


class _ProductionFacePhotoResidueStageAdapter(StageCaseAdapter):
    def __init__(
        self,
        plan: CampaignPlan,
        *,
        adapter: SshFacePhotoResidueAdapter | None,
        blocked_state: str | None = None,
        blocked_reason: str | None = None,
    ) -> None:
        self._plan = plan
        self._adapter = adapter
        self._blocked_state = blocked_state
        self._blocked_reason = blocked_reason

    async def execute(self, case: CaseSpec) -> StageCaseOutcome:
        if (
            case.phase is not CampaignPhase.ONLINE
            or case.load != {"kind": "face_photo_residue"}
            or case.fixture_ids != ("person-photo",)
        ):
            return StageCaseOutcome(
                "failed",
                "阶段用例不是规范 FaceRec 原图残留用例",
                {"validation_state": "case_invalid"},
            )
        fixtures = {
            fixture.fixture_id: fixture for fixture in self._plan.fixture_manifest.fixtures
        }
        fixture = fixtures.get("person-photo")
        if fixture is None or fixture.kind is not FixtureKind.PERSON_PHOTO:
            return StageCaseOutcome(
                "failed",
                "person-photo fixture manifest 缺失或类型不符",
                {"validation_state": "fixture_invalid"},
            )
        if self._adapter is None:
            return StageCaseOutcome(
                "blocked",
                self._blocked_reason or "FaceRec 原图残留运行时适配器不可用",
                {"configuration_state": self._blocked_state or "unavailable"},
            )
        control_hostname = urlsplit(self._plan.control_origin).hostname
        gateway_hostname = urlsplit(self._plan.gateway_origin).hostname
        if (
            control_hostname != _EXPECTED_CONTROL_HOSTNAME
            or gateway_hostname != _EXPECTED_CONTROL_HOSTNAME
            or self._adapter.target_hostname != _EXPECTED_CONTROL_HOSTNAME
        ):
            return StageCaseOutcome(
                "blocked",
                "残留探针 SSH 目标与 Campaign Control/Gateway hostname 不一致",
                {"configuration_state": "target_mismatch"},
            )
        try:
            result = await self._adapter.run()
        except Exception as error:
            return StageCaseOutcome(
                "failed",
                f"FaceRec 原图残留适配器执行异常: {type(error).__name__}",
                {"error_type": type(error).__name__},
            )
        return StageCaseOutcome(
            cast(StageCaseStatus, result.status),
            result.reason,
            result.to_evidence(),
        )


def face_photo_residue_factory(
    plan: CampaignPlan,
    release_root: Path,
) -> StageCaseAdapter:
    del release_root
    try:
        settings = _load_face_photo_residue_settings()
        fixtures = {
            fixture.fixture_id: fixture for fixture in plan.fixture_manifest.fixtures
        }
        fixture = fixtures.get("person-photo")
        if (
            fixture is None
            or fixture.kind is not FixtureKind.PERSON_PHOTO
            or fixture.sha256 != settings.person_photo_sha256
            or fixture.size_bytes != settings.person_photo_size_bytes
        ):
            raise _ConfigurationBlocked(
                "fixture_mismatch",
                "runtime TOML 原图摘要/大小未与 person-photo manifest 精确绑定",
            )
        adapter = SshFacePhotoResidueAdapter(
            target_hostname=settings.target_hostname,
            ssh_user=settings.ssh_user,
            ssh_port=settings.ssh_port,
            facerec_compose_project="algorithm-operators",
            facerec_container_ids=settings.facerec_container_ids,
            mongodb_compose_project="algorithm-scheduling-platform",
            mongodb_compose_service="mongodb",
            mongodb_container_id=settings.mongodb_container_id,
            mongodb_database="facerecapi",
            mongodb_collection="persons",
            online_gateway_compose_project="algorithm-scheduling-platform",
            online_gateway_compose_service="online-gateway-service",
            online_gateway_container_id=settings.online_gateway_container_id,
            container_photo_paths=("/app/media/person_photos",),
            container_log_paths=("/app/logs",),
            persistent_paths=("/data/result",),
            fixture_id="person-photo",
            fixture_evidence_id=settings.fixture_evidence_id,
            person_photo_sha256=settings.person_photo_sha256,
            person_photo_size_bytes=settings.person_photo_size_bytes,
            enabled=True,
            probe_timeout_seconds=settings.probe_timeout_seconds,
        )
        return _ProductionFacePhotoResidueStageAdapter(plan, adapter=adapter)
    except _ConfigurationBlocked as error:
        return _ProductionFacePhotoResidueStageAdapter(
            plan,
            adapter=None,
            blocked_state=error.state,
            blocked_reason=error.reason,
        )
    except (OSError, ValueError) as error:
        return _ProductionFacePhotoResidueStageAdapter(
            plan,
            adapter=None,
            blocked_state="config_invalid",
            blocked_reason=f"FaceRec 原图残留探针组装失败: {type(error).__name__}",
        )


class _BlockedMetricsAdapter(StageMetricsAdapter):
    def __init__(self, state: str, reason: str) -> None:
        self._state = state
        self._reason = reason

    async def assess(
        self,
        case: CaseSpec,
        checkpoint: StageCheckpoint,
    ) -> GuardrailAssessment:
        del case, checkpoint
        return GuardrailAssessment(GuardrailLevel.STOP, (self._reason,))

    async def execute(self, case: CaseSpec) -> StageCaseOutcome:
        del case
        return StageCaseOutcome(
            "blocked",
            self._reason,
            {"configuration_state": self._state},
        )


def metrics_factory(
    plan: CampaignPlan,
    release_root: Path,
) -> StageMetricsAdapter:
    try:
        settings = _load_metrics_settings()
        control_hostname = urlsplit(plan.control_origin).hostname
        gateway_hostname = urlsplit(plan.gateway_origin).hostname
        if (
            control_hostname != settings.target_hostname
            or gateway_hostname != settings.target_hostname
        ):
            raise _ConfigurationBlocked(
                "target_mismatch",
                "指标 SSH 目标与 Campaign Control/Gateway hostname 不一致",
            )
    except _ConfigurationBlocked as error:
        return _BlockedMetricsAdapter(error.state, error.reason)

    command_runner = SubprocessCommandRunner()
    try:
        remote_runner = SshCommandRunner(
            command_runner,
            SshTarget(
                host=settings.target_hostname,
                user=settings.ssh_user,
                port=settings.ssh_port,
            ),
            enabled=True,
        )
        http_client = HttpxProbeClient()
        timeout = settings.probe_timeout_seconds
        kafka_lag_probe = KafkaLagProbe(
            remote_runner,
            compose_project=settings.kafka_compose_project,
            compose_service=settings.kafka_compose_service,
            consumer_groups=settings.kafka_consumer_groups,
            timeout_seconds=settings.kafka_probe_timeout_seconds,
            attempts=settings.kafka_probe_attempts,
            retry_delay_seconds=settings.kafka_probe_retry_delay_seconds,
        )
        target_host_probe = TargetHostProbe(
            remote_runner,
            filesystem_paths=settings.filesystem_paths,
            directory_paths=TARGET_DATA_DIRECTORY_PATHS,
            timeout_seconds=timeout,
        )
        return RuntimeMetricsAdapter(
            plan,
            release_root.resolve(),
            load_host_probe=LoadHostProbe(
                command_runner,
                timeout_seconds=timeout,
            ),
            target_host_probe=target_host_probe,
            directory_size_probe=target_host_probe.collect_directory_sizes,
            docker_probe=DockerMetricsProbe(
                remote_runner,
                compose_projects=settings.compose_projects,
                timeout_seconds=timeout,
            ),
            gpu_probe=NvidiaSmiProbe(remote_runner, timeout_seconds=timeout),
            control_probe=ControlMetricsProbe(
                http_client,
                plan.control_origin,
                timeout_seconds=timeout,
                include_kafka_lag=False,
            ),
            kafka_lag_probe=kafka_lag_probe,
            gateway_probe=GatewayMetricsProbe(
                http_client,
                plan.gateway_origin,
                timeout_seconds=timeout,
            ),
            schedule=settings.schedule,
            database_services=settings.database_services,
            critical_container_services=settings.critical_container_services,
            restart_loop_threshold=settings.restart_loop_threshold,
            restart_loop_window_seconds=settings.restart_loop_window_seconds,
            probe_attempts=settings.probe_attempts,
            probe_retry_delay_seconds=settings.probe_retry_delay_seconds,
            expected_gpu_by_pid=settings.expected_gpu_by_pid,
        )
    except (OSError, ValueError) as error:
        return _BlockedMetricsAdapter(
            "config_invalid",
            f"运行时指标探针组装失败: {type(error).__name__}",
        )
