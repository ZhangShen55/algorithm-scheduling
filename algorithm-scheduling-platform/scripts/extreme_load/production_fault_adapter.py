from __future__ import annotations

import asyncio
import fcntl
import json
import math
import os
import re
import secrets
import stat
import time
import tomllib
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Protocol, cast
from urllib.parse import urlsplit

from deploy.scripts.extreme_load_faults import (
    ContainerIdentity,
    ContainerTarget,
    FaultCheck,
    FaultPlan,
    FaultPlanRunResult,
    FaultRuntime,
    FaultScenario,
    FaultSequenceRunner,
    PlanValidationError,
    build_gpu_group_scenario,
    build_kafka_scenario,
    build_platform_scenarios,
    build_redis_scenario,
    build_single_operator_scenarios,
)

from .catalog import CampaignPhase, CaseSpec
from .plan import CampaignPlan
from .stage_runtime import StageCaseAdapter, StageCaseOutcome
from .system_probes import (
    CommandResult,
    CommandRunner,
    SshCommandRunner,
    SshTarget,
    SubprocessCommandRunner,
)

RUNTIME_CONFIG_ENV = "ALGORITHM_CAMPAIGN_RUNTIME_CONFIG"

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
_EXPECTED_TARGET_HOSTNAME = "192.168.29.11"
_OPERATOR_PROJECT = "algorithm-operators"
_PLATFORM_PROJECT = "algorithm-scheduling-platform"
_OPERATOR_CODES = (
    "asr_offline",
    "asr_online",
    "ocr",
    "vbas",
    "facerec",
    "screen_det",
    "ppt_slice",
)
_GPU_OPERATOR_CODES = _OPERATOR_CODES[:-1]
_PLATFORM_SERVICES = (
    "control-service",
    "orchestrator-service",
    "vision-orchestrator-service",
    "online-gateway-service",
)
_MIDDLEWARE_SERVICES = ("kafka", "redis")
_GPU_SERVICES = tuple(
    f"{code.replace('_', '-')}-gpu{gpu_index}"
    for gpu_index in range(3)
    for code in _GPU_OPERATOR_CODES
)
_CPU_SERVICES = tuple(f"ppt-slice-cpu{index}" for index in range(3))
_OPERATOR_SERVICES = frozenset((*_GPU_SERVICES, *_CPU_SERVICES))
_PLATFORM_TARGET_SERVICES = frozenset((*_PLATFORM_SERVICES, *_MIDDLEWARE_SERVICES))
_ALL_TARGET_SERVICES = _OPERATOR_SERVICES | _PLATFORM_TARGET_SERVICES
_FULL_CONTAINER_ID = re.compile(r"[0-9a-f]{64}")
_SAFE_ATTEMPT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_SAFE_REMOTE_PATH = re.compile(r"/[A-Za-z0-9_./-]{1,511}")
_EVIDENCE_REFERENCE = re.compile(
    r"release:(?P<path>[A-Za-z0-9_.\-/]{1,512})#sha256:(?P<sha>[0-9a-f]{64})"
)
_TOP_LEVEL_KEYS = frozenset(
    {"schema_version", "media_download", "runtime_metrics", "face_photo_residue", "fault"}
)
_FAULT_KEYS = frozenset(
    {
        "enabled",
        "target_hostname",
        "ssh_user",
        "ssh_port",
        "delegated_lock_holder_pid",
        "delegated_lock_path",
        "semantic_probe_path",
        "semantic_probe_release_root",
        "semantic_probe_evidence_root",
        "probe_poll_seconds",
        "single_operator_services",
        "operator_container_ids",
        "platform_container_ids",
    }
)
_CASE_OPERATOR_CODES = {
    "asr-offline": "asr_offline",
    "asr-online": "asr_online",
    "ocr": "ocr",
    "vbas": "vbas",
    "facerec": "facerec",
    "screen-det": "screen_det",
    "ppt-slice": "ppt_slice",
}
_CASE_PLATFORM_SERVICES = {
    "control": "control-service",
    "orchestrator": "orchestrator-service",
    "vision": "vision-orchestrator-service",
    "online-gateway": "online-gateway-service",
}


class _ConfigurationBlocked(ValueError):
    def __init__(self, state: str, reason: str) -> None:
        super().__init__(reason)
        self.state = state
        self.reason = reason


class _CaseRejected(ValueError):
    pass


class _HeldLockGuard(Protocol):
    def __enter__(self) -> _HeldLockGuard: ...

    def __exit__(self, *args: object) -> object: ...

    def held_for(self, release_root: Path) -> bool: ...


LockGuardFactory = Callable[[Path], AbstractContextManager[_HeldLockGuard]]
RuntimeFactory = Callable[[str, Callable[[], bool]], FaultRuntime]


@dataclass(frozen=True, slots=True)
class FaultWitnessMedia:
    short_teacher_video_url: str
    long_teacher_video_url: str
    long_slides_video_url: str

    def __post_init__(self) -> None:
        for field_name, value in (
            ("short_teacher_video_url", self.short_teacher_video_url),
            ("long_teacher_video_url", self.long_teacher_video_url),
            ("long_slides_video_url", self.long_slides_video_url),
        ):
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"{field_name} 必须是 HTTP/HTTPS fixture URL")
            if parsed.username is not None or parsed.password is not None:
                raise ValueError(f"{field_name} 不得在 URL 中携带凭据")


@dataclass(frozen=True, slots=True)
class FaultAdapterSettings:
    target_hostname: str
    ssh_user: str
    ssh_port: int
    delegated_lock_holder_pid: int
    delegated_lock_path: Path
    semantic_probe_path: str
    semantic_probe_release_root: str
    semantic_probe_evidence_root: str
    probe_poll_seconds: float
    single_operator_services: Mapping[str, str]
    targets: Mapping[str, ContainerTarget]

    def __post_init__(self) -> None:
        if (
            self.target_hostname != _EXPECTED_TARGET_HOSTNAME
            or self.ssh_user != "root"
            or self.ssh_port != 22
        ):
            raise ValueError("故障 SSH 目标必须是已批准的 root@192.168.29.11:22")
        if type(self.delegated_lock_holder_pid) is not int or self.delegated_lock_holder_pid <= 0:
            raise ValueError("远端委托维护锁 holder PID 必须是正整数")
        if (
            not self.delegated_lock_path.is_absolute()
            or self.delegated_lock_path.name != ".operator-lifecycle.lock"
        ):
            raise ValueError("远端委托维护锁必须是 release tag 根目录中的精确规范路径")
        _validate_remote_path(self.semantic_probe_path, "semantic_probe_path")
        _validate_remote_path(
            self.semantic_probe_release_root,
            "semantic_probe_release_root",
        )
        _validate_remote_path(
            self.semantic_probe_evidence_root,
            "semantic_probe_evidence_root",
        )
        release = PurePosixPath(self.semantic_probe_release_root)
        evidence = PurePosixPath(self.semantic_probe_evidence_root)
        if evidence == release or release not in evidence.parents:
            raise ValueError("故障语义证据根必须严格位于远端当前 release 根内")
        if (
            isinstance(self.probe_poll_seconds, bool)
            or not isinstance(self.probe_poll_seconds, (int, float))
            or not math.isfinite(self.probe_poll_seconds)
            or not 0 < self.probe_poll_seconds <= 30
        ):
            raise ValueError("故障语义探针轮询间隔必须位于 0–30 秒")
        _validate_single_operator_services(self.single_operator_services)
        if set(self.targets) != _ALL_TARGET_SERVICES:
            raise ValueError("故障目标库存必须精确覆盖 21 算子、四平台、Kafka 和 Redis")
        ids: list[str] = []
        for service, target in self.targets.items():
            target.validate()
            expected_project = (
                _OPERATOR_PROJECT if service in _OPERATOR_SERVICES else _PLATFORM_PROJECT
            )
            if target.compose_service != service or target.compose_project != expected_project:
                raise ValueError("故障目标 Compose project/service 身份不符合权威拓扑")
            ids.append(target.container_id)
        if len(ids) != len(set(ids)):
            raise ValueError("故障目标完整容器 ID 不能重复")


@dataclass(frozen=True, slots=True)
class _CampaignLockBinding:
    campaign_id: str
    release_root: Path
    guard: _HeldLockGuard

    @property
    def acquired(self) -> bool:
        return self.guard.held_for(self.release_root)


class _LocalCampaignLockGuard:
    """在负载机当前 attempt 内串行化故障 case；远端锁另行逐动作证明。"""

    _LOCK_NAME = ".campaign-fault.lock"

    def __init__(self, release_root: Path, campaign_id: str) -> None:
        self._release_root = release_root
        self._campaign_id = campaign_id
        self._directory_fd = -1
        self._lock_fd = -1
        self._held = False
        self._opened_lock: os.stat_result | None = None

    @property
    def lock_path(self) -> Path:
        return self._release_root / self._LOCK_NAME

    @staticmethod
    def _validate_root(metadata: os.stat_result) -> None:
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise ValueError("本地 Campaign attempt 根目录身份或权限无效")

    @staticmethod
    def _validate_lock(metadata: os.stat_result) -> None:
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise ValueError("本地 Campaign 维护锁身份或权限无效")

    @staticmethod
    def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
        return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)

    def _binding_document(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "campaign_id": self._campaign_id,
            "attempt_root": str(self._release_root),
        }

    def __enter__(self) -> _LocalCampaignLockGuard:
        if self._held:
            raise ValueError("本地 Campaign 维护锁已持有")
        named_root = os.lstat(self._release_root)
        self._validate_root(named_root)
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            self._directory_fd = os.open(self._release_root, directory_flags)
            opened_root = os.fstat(self._directory_fd)
            self._validate_root(opened_root)
            if not self._same_inode(named_root, opened_root):
                raise ValueError("本地 Campaign attempt 根目录在打开期间发生替换")
            created = False
            try:
                self._lock_fd = os.open(
                    self._LOCK_NAME,
                    os.O_RDWR
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=self._directory_fd,
                )
                created = True
            except FileExistsError:
                self._lock_fd = os.open(
                    self._LOCK_NAME,
                    os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=self._directory_fd,
                )
            opened_lock = os.fstat(self._lock_fd)
            named_lock = os.stat(
                self._LOCK_NAME,
                dir_fd=self._directory_fd,
                follow_symlinks=False,
            )
            self._validate_lock(opened_lock)
            self._validate_lock(named_lock)
            if not self._same_inode(opened_lock, named_lock):
                raise ValueError("本地 Campaign 维护锁在打开期间发生替换")
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError("当前 attempt 已有其他故障执行者") from error
            opened_lock = os.fstat(self._lock_fd)
            self._validate_lock(opened_lock)
            expected = self._binding_document()
            if created:
                payload = json.dumps(expected, ensure_ascii=False, sort_keys=True).encode("utf-8")
                if os.write(self._lock_fd, payload) != len(payload):
                    raise OSError("本地 Campaign 维护锁内容未完整写入")
                os.fsync(self._lock_fd)
            else:
                if opened_lock.st_size > 64 * 1024:
                    raise ValueError("本地 Campaign 维护锁内容超限")
                os.lseek(self._lock_fd, 0, os.SEEK_SET)
                try:
                    existing = json.loads(os.read(self._lock_fd, 64 * 1024))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise ValueError("本地 Campaign 维护锁内容无效") from error
                after_read = os.fstat(self._lock_fd)
                if (
                    after_read.st_size != opened_lock.st_size
                    or after_read.st_mtime_ns != opened_lock.st_mtime_ns
                    or after_read.st_ctime_ns != opened_lock.st_ctime_ns
                    or after_read.st_nlink != 1
                ):
                    raise ValueError("本地 Campaign 维护锁在读取期间发生修改")
                if existing != expected:
                    raise ValueError("本地 Campaign 维护锁不属于当前 attempt")
            self._opened_lock = os.fstat(self._lock_fd)
            self._held = True
            if not self.held_for(self._release_root):
                raise ValueError("本地 Campaign 维护锁绑定在获取后发生变化")
            return self
        except BaseException:
            self._close()
            raise

    def __exit__(self, *_: object) -> None:
        self._close()

    def held_for(self, release_root: Path) -> bool:
        if (
            not self._held
            or release_root != self._release_root
            or self._directory_fd < 0
            or self._lock_fd < 0
            or self._opened_lock is None
        ):
            return False
        try:
            named_root = os.lstat(self._release_root)
            opened_root = os.fstat(self._directory_fd)
            named_lock = os.stat(
                self._LOCK_NAME,
                dir_fd=self._directory_fd,
                follow_symlinks=False,
            )
            opened_lock = os.fstat(self._lock_fd)
            self._validate_root(named_root)
            self._validate_root(opened_root)
            self._validate_lock(named_lock)
            self._validate_lock(opened_lock)
            return (
                self._same_inode(named_root, opened_root)
                and self._same_inode(named_lock, opened_lock)
                and self._same_inode(opened_lock, self._opened_lock)
                and opened_lock.st_size == self._opened_lock.st_size
                and opened_lock.st_mtime_ns == self._opened_lock.st_mtime_ns
                and opened_lock.st_ctime_ns == self._opened_lock.st_ctime_ns
            )
        except (OSError, ValueError):
            return False

    def _close(self) -> None:
        self._held = False
        self._opened_lock = None
        if self._lock_fd >= 0:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(self._lock_fd)
                self._lock_fd = -1
        if self._directory_fd >= 0:
            os.close(self._directory_fd)
            self._directory_fd = -1


def _as_mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise ValueError(f"{field_name} 必须是字符串键对象")
    return cast(Mapping[str, object], value)


def _finite_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} 必须是有限数值")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field_name} 必须是有限数值")
    return result


def _validate_remote_path(value: str, field_name: str) -> None:
    path = PurePosixPath(value)
    if (
        _SAFE_REMOTE_PATH.fullmatch(value) is None
        or not path.is_absolute()
        or ".." in path.parts
        or value.endswith("/")
    ):
        raise ValueError(f"{field_name} 必须是无通配符和父目录跳转的绝对路径")


def _validate_single_operator_services(services: Mapping[str, str]) -> None:
    if set(services) != set(_OPERATOR_CODES):
        raise ValueError("单实例故障映射必须精确覆盖七类算子")
    for code, service in services.items():
        expected = (
            {f"{code.replace('_', '-')}-gpu{index}" for index in range(3)}
            if code in _GPU_OPERATOR_CODES
            else {f"ppt-slice-cpu{index}" for index in range(3)}
        )
        if service not in expected:
            raise ValueError(f"单实例故障服务不属于算子类型: {code}")


def _read_external_config(path: Path) -> bytes:
    if not path.is_absolute() or path.suffix.lower() != ".toml":
        raise ValueError("运行时配置必须使用绝对 TOML 路径")
    metadata = os.lstat(path)
    resolved = path.resolve(strict=True)
    if resolved == _WORKSPACE_ROOT or _WORKSPACE_ROOT in resolved.parents:
        raise ValueError("运行时配置必须位于 Git 工作区外")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("运行时配置必须是普通文件且不能是符号链接")
    if (
        metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise PermissionError("运行时配置必须归当前 UID、权限 0600 且只有一个硬链接")
    if metadata.st_size > 256 * 1024:
        raise ValueError("运行时配置超过 256 KiB")
    descriptor = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise ValueError("运行时配置在打开期间发生替换")
        content = os.read(descriptor, 256 * 1024 + 1)
        after = os.fstat(descriptor)
        if (
            len(content) > 256 * 1024
            or len(content) != opened.st_size
            or after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
            or after.st_ctime_ns != opened.st_ctime_ns
            or after.st_nlink != 1
        ):
            raise ValueError("运行时配置在读取期间发生修改或超限")
        return content
    finally:
        os.close(descriptor)


def _container_id_mapping(
    raw: object,
    *,
    expected_services: frozenset[str],
    project: str,
    field_name: str,
) -> dict[str, ContainerTarget]:
    document = _as_mapping(raw, field_name)
    if set(document) != expected_services:
        raise ValueError(f"{field_name} 服务集合不符合权威拓扑")
    targets: dict[str, ContainerTarget] = {}
    for service, raw_id in document.items():
        if type(raw_id) is not str or _FULL_CONTAINER_ID.fullmatch(raw_id) is None:
            raise ValueError(f"{field_name} 必须只包含 64 位小写完整容器 ID")
        targets[service] = ContainerTarget(raw_id, project, service)
    return targets


def _load_settings() -> FaultAdapterSettings:
    config_value = os.environ.get(RUNTIME_CONFIG_ENV)
    if not config_value:
        raise _ConfigurationBlocked(
            "config_missing",
            f"缺少 {RUNTIME_CONFIG_ENV} 显式故障运行时配置",
        )
    try:
        config_path = Path(config_value)
        if config_path.name == ".env":
            raise ValueError("故障运行时配置不能使用 .env")
        raw: object = tomllib.loads(_read_external_config(config_path).decode("utf-8"))
        document = _as_mapping(raw, "运行时配置")
        if (
            type(document.get("schema_version")) is not int
            or document.get("schema_version") != 1
            or not set(document) <= _TOP_LEVEL_KEYS
        ):
            raise ValueError("故障运行时配置顶层字段或 schema_version 不合法")
        if "fault" not in document:
            raise ValueError("运行时配置缺少 fault")
        fault = _as_mapping(document["fault"], "fault")
        if fault.get("enabled") is not True:
            raise _ConfigurationBlocked("disabled", "远程故障注入未显式 enabled=true")
        if set(fault) != _FAULT_KEYS:
            raise ValueError("fault 配置字段不完整或包含未知字段")
        single_raw = _as_mapping(fault["single_operator_services"], "single_operator_services")
        if any(type(value) is not str for value in single_raw.values()):
            raise ValueError("single_operator_services 必须是字符串映射")
        single = cast(dict[str, str], dict(single_raw))
        operator_targets = _container_id_mapping(
            fault["operator_container_ids"],
            expected_services=_OPERATOR_SERVICES,
            project=_OPERATOR_PROJECT,
            field_name="operator_container_ids",
        )
        platform_targets = _container_id_mapping(
            fault["platform_container_ids"],
            expected_services=_PLATFORM_TARGET_SERVICES,
            project=_PLATFORM_PROJECT,
            field_name="platform_container_ids",
        )
        return FaultAdapterSettings(
            target_hostname=cast(str, fault["target_hostname"]),
            ssh_user=cast(str, fault["ssh_user"]),
            ssh_port=cast(int, fault["ssh_port"]),
            delegated_lock_holder_pid=cast(int, fault["delegated_lock_holder_pid"]),
            delegated_lock_path=Path(cast(str, fault["delegated_lock_path"])),
            semantic_probe_path=cast(str, fault["semantic_probe_path"]),
            semantic_probe_release_root=cast(
                str,
                fault["semantic_probe_release_root"],
            ),
            semantic_probe_evidence_root=cast(
                str,
                fault["semantic_probe_evidence_root"],
            ),
            probe_poll_seconds=_finite_number(fault["probe_poll_seconds"], "probe_poll_seconds"),
            single_operator_services=single,
            targets={**operator_targets, **platform_targets},
        )
    except _ConfigurationBlocked:
        raise
    except (
        OSError,
        PermissionError,
        UnicodeDecodeError,
        TypeError,
        ValueError,
        tomllib.TOMLDecodeError,
    ) as error:
        raise _ConfigurationBlocked(
            "config_invalid",
            "故障运行时配置不符合工作区外 0600 TOML 和精确拓扑合同",
        ) from error


def _selected_target(settings: FaultAdapterSettings, service: str) -> ContainerTarget:
    try:
        return settings.targets[service]
    except KeyError as error:
        raise _CaseRejected("故障用例引用了库存外服务") from error


def _fault_witness_media(plan: CampaignPlan) -> FaultWitnessMedia:
    fixtures = {item.fixture_id: item.path for item in plan.fixture_manifest.fixtures}
    required = ("short-teacher", "long-teacher", "long-slides")
    missing = [fixture_id for fixture_id in required if fixture_id not in fixtures]
    if missing:
        raise _ConfigurationBlocked(
            "fixture_missing",
            "故障主动 witness 缺少媒体 fixture: " + ", ".join(missing),
        )
    try:
        return FaultWitnessMedia(
            short_teacher_video_url=fixtures["short-teacher"],
            long_teacher_video_url=fixtures["long-teacher"],
            long_slides_video_url=fixtures["long-slides"],
        )
    except ValueError as error:
        raise _ConfigurationBlocked(
            "fixture_invalid",
            "故障主动 witness 媒体必须是无内嵌凭据的 HTTP/HTTPS URL",
        ) from error


def _scenario_for_case(
    case: CaseSpec,
    settings: FaultAdapterSettings,
) -> FaultScenario:
    if case.phase is not CampaignPhase.RECOVERY or case.fixture_ids != (
        "external-fixture-manifest",
    ):
        raise _CaseRejected("故障适配器只接受 recovery 阶段的外部 fixture 用例")
    kind = case.load.get("kind")
    if kind == "single_operator_fault" and set(case.load) == {"kind", "operator"}:
        operator_value = case.load.get("operator")
        if type(operator_value) is not str or operator_value not in _CASE_OPERATOR_CODES:
            raise _CaseRejected("单实例故障 operator 不属于当前七算子")
        code = _CASE_OPERATOR_CODES[operator_value]
        targets = {
            item: _selected_target(settings, settings.single_operator_services[item])
            for item in _OPERATOR_CODES
        }
        scenario = next(
            item
            for item in build_single_operator_scenarios(targets)
            if item.targets[0].compose_service == settings.single_operator_services[code]
        )
    elif kind == "gpu_group_fault" and set(case.load) == {"kind", "gpu"}:
        gpu_index = case.load.get("gpu")
        if type(gpu_index) is not int or gpu_index not in {0, 1, 2}:
            raise _CaseRejected("GPU 组故障只允许 GPU0/GPU1/GPU2")
        scenario = build_gpu_group_scenario(
            gpu_index,
            {
                code: _selected_target(
                    settings,
                    f"{code.replace('_', '-')}-gpu{gpu_index}",
                )
                for code in _GPU_OPERATOR_CODES
            },
        )
    elif kind == "platform_fault" and set(case.load) == {"kind", "service"}:
        service_value = case.load.get("service")
        if type(service_value) is not str or service_value not in _CASE_PLATFORM_SERVICES:
            raise _CaseRejected("平台故障 service 不属于四个平台服务")
        service = _CASE_PLATFORM_SERVICES[service_value]
        scenarios = build_platform_scenarios(
            {item: _selected_target(settings, item) for item in _PLATFORM_SERVICES}
        )
        scenario = next(item for item in scenarios if item.targets[0].compose_service == service)
    elif kind == "middleware_fault" and set(case.load) == {"kind", "service"}:
        service_value = case.load.get("service")
        if service_value == "kafka":
            scenario = build_kafka_scenario(_selected_target(settings, "kafka"))
        elif service_value == "redis":
            scenario = build_redis_scenario(_selected_target(settings, "redis"))
        else:
            raise _CaseRejected("中间件故障 service 只允许 Kafka 或 Redis")
    else:
        raise _CaseRejected("阶段用例不是规范故障用例")
    if scenario.timeout_seconds > case.timeout_seconds:
        raise _CaseRejected("故障场景恢复预算超过 Campaign 用例超时")
    return scenario


class SshFaultRuntime(FaultRuntime):
    """通过 BatchMode SSH 仅对完整容器 ID 执行动作，并要求外部语义证明。"""

    def __init__(
        self,
        runner: CommandRunner,
        *,
        campaign_id: str,
        case_id: str,
        semantic_probe_path: str,
        semantic_probe_release_root: str,
        semantic_probe_evidence_root: str,
        remote_lock_holder_pid: int,
        remote_lock_path: str,
        witness_media: FaultWitnessMedia,
        probe_poll_seconds: float,
        lock_probe: Callable[[], bool],
        sleep: Callable[[float], None] = time.sleep,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        _validate_remote_path(semantic_probe_path, "semantic_probe_path")
        _validate_remote_path(
            semantic_probe_release_root,
            "semantic_probe_release_root",
        )
        _validate_remote_path(
            semantic_probe_evidence_root,
            "semantic_probe_evidence_root",
        )
        if not campaign_id or not case_id:
            raise ValueError("故障运行时必须绑定 Campaign 和用例 ID")
        if type(remote_lock_holder_pid) is not int or remote_lock_holder_pid <= 0:
            raise ValueError("远端维护锁 holder PID 必须是正整数")
        _validate_remote_path(remote_lock_path, "remote_lock_path")
        if PurePosixPath(remote_lock_path).name != ".operator-lifecycle.lock":
            raise ValueError("远端维护锁路径必须指向规范锁文件")
        self._runner = runner
        self._campaign_id = campaign_id
        self._case_id = case_id
        self._semantic_probe_path = semantic_probe_path
        self._semantic_probe_release_root = semantic_probe_release_root
        self._semantic_probe_evidence_root = semantic_probe_evidence_root
        self._remote_lock_holder_pid = remote_lock_holder_pid
        self._remote_lock_path = remote_lock_path
        self._witness_media = witness_media
        self._probe_poll_seconds = probe_poll_seconds
        self._lock_probe = lock_probe
        self._sleep = sleep
        self._monotonic_clock = monotonic_clock
        self._scenario_challenges: dict[str, str] = {}
        self._baseline_refs: dict[str, str] = {}
        self._action_refs: dict[str, str] = {}
        self._active_scenario: FaultScenario | None = None
        self._fault_windows: dict[str, tuple[str, str]] = {}
        self._stopped_container_ids: set[str] = set()
        self._compensated_container_ids: set[str] = set()
        self.check_evidence: list[Mapping[str, object]] = []

    def _run(self, argv: Sequence[str], *, timeout_seconds: float) -> CommandResult:
        result = self._runner.run(tuple(argv), timeout_seconds=min(30.0, timeout_seconds))
        if result.returncode != 0:
            raise RuntimeError("远端精确故障命令失败")
        return result

    def _require_lock(self) -> None:
        if not self._lock_probe():
            raise PlanValidationError("当前 Campaign 维护锁在故障动作期间丢失")

    def _require_remote_lock(self) -> None:
        self._require_lock()
        challenge = secrets.token_hex(16)
        result = self._run(
            (
                self._semantic_probe_path,
                "--lock-only",
                "--challenge",
                challenge,
                "--release-root",
                self._semantic_probe_release_root,
                "--lock-holder-pid",
                str(self._remote_lock_holder_pid),
                "--lock-path",
                self._remote_lock_path,
            ),
            timeout_seconds=10.0,
        )
        try:
            raw: object = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise PlanValidationError("远端维护锁探针输出不是 JSON") from error
        document = _as_mapping(raw, "远端维护锁探针输出")
        if document != {
            "schema_version": 1,
            "status": "held",
            "challenge": challenge,
            "release_root": self._semantic_probe_release_root,
            "lock_path": self._remote_lock_path,
            "holder_pid": self._remote_lock_holder_pid,
        }:
            raise PlanValidationError("远端维护锁 holder/path/release 绑定无法独立证明")

    def inspect(self, container_id: str) -> ContainerIdentity:
        if _FULL_CONTAINER_ID.fullmatch(container_id) is None:
            raise PlanValidationError("远端 Docker inspect 只接受完整容器 ID")
        try:
            self._require_remote_lock()
        except PlanValidationError:
            scenario = self._active_scenario
            target = next(
                (
                    item
                    for item in (() if scenario is None else scenario.targets)
                    if item.container_id == container_id
                ),
                None,
            )
            if target is None or container_id not in (
                self._stopped_container_ids | self._compensated_container_ids
            ):
                raise
            return ContainerIdentity(
                container_id=target.container_id,
                compose_project=target.compose_project,
                compose_service=target.compose_service,
                running=container_id in self._compensated_container_ids,
            )
        output = self._run(("docker", "inspect", container_id), timeout_seconds=30).stdout
        try:
            document: object = json.loads(output)
        except json.JSONDecodeError as error:
            raise RuntimeError("远端 Docker inspect 输出不是 JSON") from error
        if not isinstance(document, list) or len(document) != 1:
            raise RuntimeError("远端 Docker inspect 没有返回唯一容器")
        item = document[0]
        if not isinstance(item, Mapping):
            raise RuntimeError("远端 Docker inspect 容器项不是对象")
        config = item.get("Config")
        state = item.get("State")
        if not isinstance(config, Mapping) or not isinstance(state, Mapping):
            raise RuntimeError("远端 Docker inspect 缺少 Config 或 State")
        labels = config.get("Labels")
        if not isinstance(labels, Mapping) or type(state.get("Running")) is not bool:
            raise RuntimeError("远端 Docker inspect 缺少 Compose 身份或运行状态")
        return ContainerIdentity(
            container_id=str(item.get("Id", "")),
            compose_project=str(labels.get("com.docker.compose.project", "")),
            compose_service=str(labels.get("com.docker.compose.service", "")),
            running=cast(bool, state["Running"]),
        )

    def stop(self, container_id: str, timeout_seconds: float) -> None:
        self._require_remote_lock()
        scenario = self._active_scenario
        if (
            scenario is None
            or container_id not in {target.container_id for target in scenario.targets}
            or _FULL_CONTAINER_ID.fullmatch(container_id) is None
        ):
            raise PlanValidationError("容器停止缺少已记录的精确场景目标")
        created_window = scenario.scenario_id not in self._fault_windows
        if created_window:
            self._fault_windows[scenario.scenario_id] = (
                secrets.token_hex(16),
                datetime.now(UTC).isoformat(),
            )
        timeout = max(1, int(timeout_seconds) - 1)
        try:
            self._run(
                ("docker", "stop", "--time", str(timeout), container_id),
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            if created_window:
                self._fault_windows.pop(scenario.scenario_id, None)
            raise
        self._stopped_container_ids.add(container_id)
        self._compensated_container_ids.discard(container_id)

    def start(self, container_id: str, timeout_seconds: float) -> None:
        try:
            self._require_remote_lock()
        except PlanValidationError:
            if container_id in self._stopped_container_ids:
                self._compensating_start(container_id, timeout_seconds)
                return
            raise
        self._run(("docker", "start", container_id), timeout_seconds=timeout_seconds)
        self._stopped_container_ids.discard(container_id)
        self._compensated_container_ids.discard(container_id)

    def _compensating_start(self, container_id: str, timeout_seconds: float) -> None:
        scenario = self._active_scenario
        if (
            scenario is None
            or container_id not in self._stopped_container_ids
            or container_id not in {target.container_id for target in scenario.targets}
            or _FULL_CONTAINER_ID.fullmatch(container_id) is None
        ):
            raise PlanValidationError("补偿启动缺少已记录的精确停止目标")
        self._run(("docker", "start", container_id), timeout_seconds=timeout_seconds)
        self._stopped_container_ids.discard(container_id)
        self._compensated_container_ids.add(container_id)

    def restart(self, container_id: str, timeout_seconds: float) -> None:
        self._require_lock()
        scenario = self._active_scenario
        if (
            scenario is None
            or len(scenario.targets) != 1
            or scenario.targets[0].container_id != container_id
        ):
            raise PlanValidationError("受控重启缺少精确场景绑定")
        self.stop(container_id, timeout_seconds)
        action_passed = False
        try:
            action_passed = self._semantic_probe_phase(
                scenario,
                phase="action",
                check_index=0,
                timeout_seconds=min(30.0, timeout_seconds),
                probe_label="restart_window",
            )
        finally:
            self.start(container_id, timeout_seconds)
        if not action_passed:
            raise RuntimeError("受控重启窗口的北向只读语义无法证明")

    @staticmethod
    def _check_index(scenario: FaultScenario, check: FaultCheck, phase: str) -> int:
        checks = scenario.disruption_checks if phase == "disruption" else scenario.recovery_checks
        try:
            return checks.index(check) + 1
        except ValueError as error:
            raise ValueError("故障检查不属于当前场景阶段") from error

    @staticmethod
    def _validate_evidence_refs(raw: object) -> tuple[str, ...]:
        if not isinstance(raw, list) or any(type(item) is not str for item in raw):
            raise ValueError("故障语义探针 evidence_refs 必须是字符串数组")
        references = cast(tuple[str, ...], tuple(raw))
        for reference in references:
            match = _EVIDENCE_REFERENCE.fullmatch(reference)
            if match is None:
                raise ValueError("故障语义探针返回了非法证据引用")
            evidence_path = PurePosixPath(match.group("path"))
            if evidence_path.is_absolute() or ".." in evidence_path.parts:
                raise ValueError("故障语义探针返回了非法证据引用")
        return references

    def _semantic_probe_phase(
        self,
        scenario: FaultScenario,
        *,
        phase: str,
        check_index: int,
        timeout_seconds: float,
        probe_label: str,
    ) -> bool:
        deadline = self._monotonic_clock() + timeout_seconds
        challenge = self._scenario_challenges.setdefault(
            scenario.scenario_id,
            secrets.token_hex(16),
        )
        targets = [target.to_dict() for target in scenario.targets]
        target_arguments = tuple(
            argument
            for target in scenario.targets
            for argument in (
                "--target",
                f"{target.compose_project}:{target.compose_service}:{target.container_id}",
            )
        )
        while True:
            self._require_remote_lock()
            remaining = deadline - self._monotonic_clock()
            if remaining <= 0:
                raise TimeoutError("故障语义探针超过检查超时")
            reference_arguments: tuple[str, ...] = ()
            window_arguments: tuple[str, ...] = ()
            window_binding = self._fault_windows.get(scenario.scenario_id)
            if phase != "baseline":
                if window_binding is None:
                    raise PlanValidationError("故障 phase 缺少精确 stop 窗口绑定")
                window_arguments = (
                    "--fault-window-token",
                    window_binding[0],
                    "--fault-window-opened-at",
                    window_binding[1],
                )
            baseline_ref = self._baseline_refs.get(scenario.scenario_id)
            action_ref = self._action_refs.get(scenario.scenario_id)
            if baseline_ref is not None:
                reference_arguments += ("--baseline-ref", baseline_ref)
            if action_ref is not None and phase in {"disruption", "recovery"}:
                reference_arguments += ("--action-ref", action_ref)
            result = self._run(
                (
                    self._semantic_probe_path,
                    "--campaign-id",
                    self._campaign_id,
                    "--case-id",
                    self._case_id,
                    "--scenario-id",
                    scenario.scenario_id,
                    "--phase",
                    phase,
                    "--check-index",
                    str(check_index),
                    "--challenge",
                    challenge,
                    "--release-root",
                    self._semantic_probe_release_root,
                    "--evidence-root",
                    self._semantic_probe_evidence_root,
                    "--lock-holder-pid",
                    str(self._remote_lock_holder_pid),
                    "--lock-path",
                    self._remote_lock_path,
                    "--short-teacher-video-url",
                    self._witness_media.short_teacher_video_url,
                    "--long-teacher-video-url",
                    self._witness_media.long_teacher_video_url,
                    "--long-slides-video-url",
                    self._witness_media.long_slides_video_url,
                    *reference_arguments,
                    *window_arguments,
                    *target_arguments,
                ),
                timeout_seconds=min(10.0, remaining),
            )
            try:
                raw: object = json.loads(result.stdout)
            except json.JSONDecodeError as error:
                raise ValueError("故障语义探针输出不是 JSON") from error
            document = _as_mapping(raw, "故障语义探针输出")
            expected_keys = {
                "schema_version",
                "campaign_id",
                "case_id",
                "scenario_id",
                "phase",
                "check_index",
                "challenge",
                "status",
                "targets",
                "lock_binding",
                "fault_window",
                "evidence_refs",
            }
            if (
                set(document) != expected_keys
                or document.get("schema_version") != 1
                or document.get("campaign_id") != self._campaign_id
                or document.get("case_id") != self._case_id
                or document.get("scenario_id") != scenario.scenario_id
                or document.get("phase") != phase
                or document.get("check_index") != check_index
                or document.get("challenge") != challenge
                or document.get("targets") != targets
                or document.get("lock_binding")
                != {
                    "holder_pid": self._remote_lock_holder_pid,
                    "lock_path": self._remote_lock_path,
                    "release_root": self._semantic_probe_release_root,
                }
                or document.get("fault_window")
                != (
                    None
                    if window_binding is None
                    else {"token": window_binding[0], "opened_at": window_binding[1]}
                )
            ):
                raise ValueError("故障语义探针身份、挑战或精确目标不匹配")
            status = document.get("status")
            if status not in {"pending", "passed", "failed"}:
                raise ValueError("故障语义探针状态不合法")
            references = self._validate_evidence_refs(document.get("evidence_refs"))
            if status == "pending":
                self._sleep(min(self._probe_poll_seconds, max(0.0, remaining)))
                continue
            if status == "passed" and not references:
                raise ValueError("故障语义通过结果必须提供不可变证据引用")
            self.check_evidence.append(
                {
                    "phase": phase,
                    "probe": probe_label,
                    "check_index": check_index,
                    "passed": status == "passed",
                    "evidence_refs": list(references),
                }
            )
            if status == "passed" and len(references) == 1:
                if phase == "baseline":
                    self._baseline_refs[scenario.scenario_id] = references[0]
                elif phase == "action":
                    self._action_refs[scenario.scenario_id] = references[0]
            return status == "passed"

    def prepare(self, scenario: FaultScenario) -> None:
        self._active_scenario = scenario
        self._fault_windows.pop(scenario.scenario_id, None)
        if not self._semantic_probe_phase(
            scenario,
            phase="baseline",
            check_index=0,
            timeout_seconds=60.0,
            probe_label="phase_bound_baseline",
        ):
            raise RuntimeError("故障动作前北向只读基线未通过")

    def _semantic_probe(
        self,
        scenario: FaultScenario,
        check: FaultCheck,
        phase: str,
    ) -> bool:
        return self._semantic_probe_phase(
            scenario,
            phase=phase,
            check_index=self._check_index(scenario, check, phase),
            timeout_seconds=check.timeout_seconds,
            probe_label=check.probe,
        )

    def verify(self, scenario: FaultScenario, check: FaultCheck, phase: str) -> bool:
        if check.probe == "external_evidence":
            return self._semantic_probe(scenario, check, phase)
        expected_running = check.probe == "containers_running"
        deadline = self._monotonic_clock() + check.timeout_seconds
        while True:
            identities = tuple(self.inspect(target.container_id) for target in scenario.targets)
            exact = all(
                identity.container_id == target.container_id
                and identity.compose_project == target.compose_project
                and identity.compose_service == target.compose_service
                for target, identity in zip(scenario.targets, identities, strict=True)
            )
            if not exact:
                raise PlanValidationError("容器状态探针发现 Compose 身份漂移")
            if all(identity.running is expected_running for identity in identities):
                self.check_evidence.append(
                    {
                        "phase": phase,
                        "probe": check.probe,
                        "passed": True,
                        "evidence_refs": [],
                    }
                )
                return True
            remaining = deadline - self._monotonic_clock()
            if remaining <= 0:
                return False
            self._sleep(min(self._probe_poll_seconds, remaining))


def _production_runtime(
    settings: FaultAdapterSettings,
    plan: CampaignPlan,
    case_id: str,
    lock_probe: Callable[[], bool],
) -> FaultRuntime:
    remote_runner = SshCommandRunner(
        SubprocessCommandRunner(),
        SshTarget(
            host=settings.target_hostname,
            user=settings.ssh_user,
            port=settings.ssh_port,
        ),
        enabled=True,
    )
    return SshFaultRuntime(
        remote_runner,
        campaign_id=plan.campaign_id,
        case_id=case_id,
        semantic_probe_path=settings.semantic_probe_path,
        semantic_probe_release_root=settings.semantic_probe_release_root,
        semantic_probe_evidence_root=settings.semantic_probe_evidence_root,
        remote_lock_holder_pid=settings.delegated_lock_holder_pid,
        remote_lock_path=str(settings.delegated_lock_path),
        witness_media=_fault_witness_media(plan),
        probe_poll_seconds=settings.probe_poll_seconds,
        lock_probe=lock_probe,
    )


class _BlockedFaultAdapter(StageCaseAdapter):
    def __init__(self, state: str, reason: str) -> None:
        self._state = state
        self._reason = reason

    async def execute(self, case: CaseSpec) -> StageCaseOutcome:
        del case
        return StageCaseOutcome(
            "blocked",
            self._reason,
            {"configuration_state": self._state},
        )


class ProductionFaultStageAdapter(StageCaseAdapter):
    def __init__(
        self,
        plan: CampaignPlan,
        release_root: Path,
        settings: FaultAdapterSettings,
        *,
        runtime_factory: RuntimeFactory | None = None,
        lock_guard_factory: LockGuardFactory | None = None,
    ) -> None:
        self._plan = plan
        self._release_root = release_root.resolve()
        self._settings = settings
        self._runtime_factory = runtime_factory or (
            lambda case_id, lock_probe: _production_runtime(
                settings,
                plan,
                case_id,
                lock_probe,
            )
        )
        self._lock_guard_factory = lock_guard_factory or (
            lambda root: _LocalCampaignLockGuard(root, plan.campaign_id)
        )

    def _release_layout(self) -> str:
        if not self._release_root.is_absolute():
            raise _ConfigurationBlocked("release_mismatch", "release_root 必须是绝对路径")
        if (
            self._release_root.name != self._plan.git_sha
            or self._release_root.parent.name != self._plan.release_tag
        ):
            if (
                self._release_root.parent.name != "attempts"
                or self._release_root.parent.parent.name != self._plan.git_sha
                or self._release_root.parent.parent.parent.name != self._plan.release_tag
                or _SAFE_ATTEMPT_ID.fullmatch(self._release_root.name) is None
            ):
                raise _ConfigurationBlocked(
                    "release_mismatch",
                    "release_root 必须严格使用 <tag>/<sha>/attempts/<attempt_id>；"
                    "仅兼容旧 <tag>/<sha> 直连布局",
                )
            return "attempt"
        return "legacy_direct"

    def _validate_release_binding(self) -> str:
        layout = self._release_layout()
        remote_release = PurePosixPath(self._settings.semantic_probe_release_root)
        remote_evidence = PurePosixPath(self._settings.semantic_probe_evidence_root)
        remote_lock = PurePosixPath(str(self._settings.delegated_lock_path))
        if (
            remote_release.name != self._plan.git_sha
            or remote_release.parent.name != self._plan.release_tag
            or remote_evidence == remote_release
            or remote_release not in remote_evidence.parents
            or remote_lock != remote_release.parent / ".operator-lifecycle.lock"
        ):
            raise _ConfigurationBlocked(
                "release_mismatch",
                "远端故障语义证据或委托维护锁未绑定当前 Campaign release",
            )
        return layout

    def _run_sync(
        self,
        case: CaseSpec,
        scenario: FaultScenario,
    ) -> tuple[FaultPlanRunResult, FaultRuntime, bool]:
        guard_context = self._lock_guard_factory(self._release_root)
        with guard_context as guard:
            if not guard.held_for(self._release_root):
                raise _ConfigurationBlocked(
                    "lock_unavailable",
                    "当前 attempt 的本地 Campaign 维护锁未持有",
                )
            runtime = self._runtime_factory(
                case.case_id,
                lambda: guard.held_for(self._release_root),
            )
            prepare = getattr(runtime, "prepare", None)
            if prepare is not None:
                cast(Callable[[FaultScenario], None], prepare)(scenario)
            result = FaultSequenceRunner(runtime).run(
                FaultPlan(self._plan.campaign_id, (scenario,)),
                dry_run=False,
                maintenance_lock=_CampaignLockBinding(
                    self._plan.campaign_id,
                    self._release_root,
                    guard,
                ),
            )
            return result, runtime, guard.held_for(self._release_root)

    async def execute(self, case: CaseSpec) -> StageCaseOutcome:
        try:
            release_layout = self._validate_release_binding()
            scenario = _scenario_for_case(case, self._settings)
        except _CaseRejected as error:
            return StageCaseOutcome(
                "failed",
                str(error),
                {"validation_state": "case_invalid"},
            )
        except _ConfigurationBlocked as error:
            return StageCaseOutcome(
                "blocked",
                error.reason,
                {"configuration_state": error.state},
            )

        try:
            result, runtime, lock_held = await asyncio.to_thread(
                self._run_sync,
                case,
                scenario,
            )
        except _ConfigurationBlocked as error:
            return StageCaseOutcome(
                "blocked",
                error.reason,
                {"configuration_state": error.state},
            )
        except PlanValidationError as error:
            return StageCaseOutcome(
                "blocked",
                f"故障目标身份或维护锁校验失败: {error}",
                {"validation_state": "identity_or_lock_invalid"},
            )
        except ValueError as error:
            return StageCaseOutcome(
                "blocked",
                f"当前 Campaign 维护锁不可用: {error}",
                {"configuration_state": "lock_unavailable"},
            )
        except Exception as error:
            return StageCaseOutcome(
                "blocked",
                f"故障运行时无法证明可安全执行: {type(error).__name__}",
                {"runtime_state": "unprovable", "error_type": type(error).__name__},
            )

        item = result.scenarios[0]
        raw_check_evidence = getattr(runtime, "check_evidence", ())
        check_evidence = (
            list(raw_check_evidence) if isinstance(raw_check_evidence, Sequence) else []
        )
        evidence: dict[str, object] = {
            "scenario_id": scenario.scenario_id,
            "kind": scenario.kind,
            "action": scenario.action.value,
            "targets": [target.to_dict() for target in scenario.targets],
            "scenario_status": item.status,
            "check_evidence": check_evidence,
            "maintenance_lock_binding": "local_attempt_and_remote_canonical",
            "local_release_layout": release_layout,
            "target_hostname": self._settings.target_hostname,
        }
        if not lock_held:
            return StageCaseOutcome(
                "failed",
                "当前 Campaign 维护锁在故障期间丢失；已执行精确恢复但不得报告通过",
                evidence,
                recovery_succeeded=item.recovered,
            )
        if item.status == "PASS" and item.recovered:
            return StageCaseOutcome(
                "passed",
                "故障注入、业务语义检查和精确恢复均通过",
                evidence,
                recovery_succeeded=True,
            )
        return StageCaseOutcome(
            "failed",
            item.reason,
            evidence,
            recovery_succeeded=item.recovered,
        )


def fault_factory(plan: CampaignPlan, release_root: Path) -> StageCaseAdapter:
    try:
        settings = _load_settings()
        if (
            urlsplit(plan.control_origin).hostname != settings.target_hostname
            or urlsplit(plan.gateway_origin).hostname != settings.target_hostname
        ):
            raise _ConfigurationBlocked(
                "target_mismatch",
                "故障 SSH 目标与 Campaign Control/Gateway hostname 不一致",
            )
        return ProductionFaultStageAdapter(plan, release_root, settings)
    except _ConfigurationBlocked as error:
        return _BlockedFaultAdapter(error.state, error.reason)
    except (OSError, PermissionError, TypeError, ValueError) as error:
        return _BlockedFaultAdapter(
            "config_invalid",
            f"故障运行时组装失败: {type(error).__name__}",
        )
