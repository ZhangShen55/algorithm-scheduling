from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx

from .metrics import (
    ContainerMetrics,
    GatewayCounters,
    GpuMetrics,
    GpuProcessMetrics,
    InstanceCapacityMetrics,
)

_SAFE_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
_SAFE_COMPOSE_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_NETWORK_INTERFACE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\*?")
_CONTAINER_ID = re.compile(r"[0-9a-f]{64}")
_GPU_UUID = re.compile(r"GPU-[A-Za-z0-9-]{8,80}")
_REMOTE_TOKEN = re.compile(r"[A-Za-z0-9_./:=+,%@-]{1,512}")
_HOST = re.compile(
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|"
    r"(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4})"
)
_PROMETHEUS_SAMPLE = re.compile(
    r"(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)"
    r"(?:\{(?P<labels>.*)\})?\s+"
    r"(?P<value>[-+]?(?:(?:[0-9]+(?:\.[0-9]*)?)|(?:\.[0-9]+))"
    r"(?:[eE][-+]?[0-9]+)?)"
)
_NUMBER = re.compile(r"[0-9]+")
_FLOAT = re.compile(r"(?:[0-9]+(?:\.[0-9]*)?)|(?:\.[0-9]+)")
_DOCKER_STATS = re.compile(
    r"(?P<id>[0-9a-f]{64})\s+"
    r"(?P<name>\S+)\s+"
    r"(?P<cpu>(?:[0-9]+(?:\.[0-9]*)?)|(?:\.[0-9]+))%\s+"
    r"(?P<memory>\S+)\s+/\s+\S+\s+\S+%\s+"
    r"\S+\s+/\s+\S+\s+\S+\s+/\s+\S+\s+[0-9]+"
)
_MEMORY_SIZE = re.compile(
    r"(?P<value>(?:[0-9]+(?:\.[0-9]*)?)|(?:\.[0-9]+))"
    r"(?P<unit>B|kB|KB|KiB|MB|MiB|GB|GiB|TB|TiB)"
)
_REDACT_KEY_VALUE = re.compile(
    r"(?i)(password|passwd|token|secret|authorization|credential)"
    r"([=:]\s*)([^\s,;]+)"
)
_REDACT_URI = re.compile(r"(?i)(https?://)([^/@:\s]+):([^/@\s]+)@")
TARGET_DATA_DIRECTORY_PATHS = ("/data/course", "/data/result")


class ProbeError(RuntimeError):
    pass


class ProbeDisabledError(ProbeError):
    pass


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    def run(self, argv: Sequence[str], *, timeout_seconds: float) -> CommandResult: ...


def _bounded_timeout(timeout_seconds: float) -> float:
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or not 0 < timeout_seconds <= 30
    ):
        raise ValueError("探针超时必须是 0–30 秒的有限数")
    return float(timeout_seconds)


def _safe_argv(argv: Sequence[str]) -> tuple[str, ...]:
    frozen = tuple(argv)
    if not frozen or len(frozen) > 256:
        raise ValueError("探针命令参数数量不合法")
    if any(not item or "\0" in item or "\n" in item or "\r" in item for item in frozen):
        raise ValueError("探针命令包含空参数或控制字符")
    return frozen


def _redact(text: str) -> str:
    safe = _REDACT_URI.sub(r"\1<redacted>:<redacted>@", text)
    safe = _REDACT_KEY_VALUE.sub(r"\1\2<redacted>", safe)
    safe = "".join(character if character.isprintable() else " " for character in safe)
    return safe[:512]


class SubprocessCommandRunner:
    def __init__(self, *, max_output_bytes: int = 4 * 1024 * 1024) -> None:
        if type(max_output_bytes) is not int or not 1 <= max_output_bytes <= 16 * 1024 * 1024:
            raise ValueError("子进程输出上限必须位于 1–16777216 字节")
        self.max_output_bytes = max_output_bytes

    def run(self, argv: Sequence[str], *, timeout_seconds: float) -> CommandResult:
        command = _safe_argv(argv)
        timeout = _bounded_timeout(timeout_seconds)
        environment = {"LC_ALL": "C", "LANG": "C"}
        for name in ("PATH", "HOME", "SSH_AUTH_SOCK", "SSH_AGENT_PID", "DOCKER_HOST"):
            value = os.environ.get(name)
            if value:
                environment[name] = value
        try:
            completed = subprocess.run(  # noqa: S603 - argv is explicit and shell stays disabled.
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=timeout,
                check=False,
                shell=False,
                close_fds=True,
                env=environment,
            )
        except subprocess.TimeoutExpired as error:
            raise ProbeError("探针命令超时") from error
        except OSError as error:
            raise ProbeError(f"探针命令无法启动: {type(error).__name__}") from error
        stdout = bytes(completed.stdout)
        stderr = bytes(completed.stderr)
        if len(stdout) + len(stderr) > self.max_output_bytes:
            raise ProbeError("探针命令输出超过安全上限")
        try:
            return CommandResult(
                completed.returncode,
                stdout.decode("utf-8", errors="strict"),
                stderr.decode("utf-8", errors="strict"),
            )
        except UnicodeDecodeError as error:
            raise ProbeError("探针命令输出不是 UTF-8") from error


def _run_checked(
    runner: CommandRunner,
    argv: Sequence[str],
    *,
    timeout_seconds: float,
) -> str:
    result = runner.run(argv, timeout_seconds=_bounded_timeout(timeout_seconds))
    if result.returncode != 0:
        detail = _redact(result.stderr.strip()) or "无脱敏错误详情"
        raise ProbeError(f"只读探针命令失败，退出码 {result.returncode}: {detail}")
    return result.stdout


@dataclass(frozen=True, slots=True)
class SshTarget:
    host: str
    user: str
    port: int = 22
    identity_file: Path | None = None

    def __post_init__(self) -> None:
        if (
            _HOST.fullmatch(self.host) is None
            or _SAFE_COMPOSE_IDENTITY.fullmatch(self.user) is None
        ):
            raise ValueError("SSH 主机或用户不是安全标识")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("SSH 端口必须位于 1–65535")
        if self.identity_file is not None and not self.identity_file.is_absolute():
            raise ValueError("SSH identity_file 必须是绝对路径")


class SshCommandRunner:
    """Explicit opt-in, BatchMode-only SSH adapter; passwords are never accepted."""

    def __init__(
        self,
        base_runner: CommandRunner,
        target: SshTarget,
        *,
        enabled: bool = False,
    ) -> None:
        self.base_runner = base_runner
        self.target = target
        self.enabled = enabled

    def run(self, argv: Sequence[str], *, timeout_seconds: float) -> CommandResult:
        if not self.enabled:
            raise ProbeDisabledError("SSH 探针默认关闭，必须显式 enabled=True")
        remote = _safe_argv(argv)
        if any(_REMOTE_TOKEN.fullmatch(item) is None for item in remote):
            raise ValueError("SSH 远端探针参数不在安全字符集内")
        timeout = _bounded_timeout(timeout_seconds)
        command = [
            "ssh",
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"ConnectTimeout={max(1, math.ceil(timeout))}",
            "-p",
            str(self.target.port),
        ]
        if self.target.identity_file is not None:
            command.extend(("-i", str(self.target.identity_file)))
        command.extend(("--", f"{self.target.user}@{self.target.host}", "env", "LC_ALL=C", *remote))
        return self.base_runner.run(command, timeout_seconds=timeout)


@dataclass(frozen=True, slots=True)
class LoadHostMetrics:
    cpu_percent: float
    memory_total_bytes: int
    memory_available_bytes: int
    network_receive_bytes: int
    network_transmit_bytes: int
    open_socket_count: int
    open_file_handle_count: int

    def __post_init__(self) -> None:
        if not math.isfinite(self.cpu_percent) or not 0 <= self.cpu_percent <= 100:
            raise ValueError("负载机 CPU 利用率必须位于 0–100")
        for name in (
            "memory_total_bytes",
            "memory_available_bytes",
            "network_receive_bytes",
            "network_transmit_bytes",
            "open_socket_count",
            "open_file_handle_count",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"负载机 {name} 必须是非负整数")
        if self.memory_available_bytes > self.memory_total_bytes:
            raise ValueError("负载机可用内存不能大于总内存")


def _parse_non_negative_int(raw: object, name: str) -> int:
    if type(raw) is not int or raw < 0:
        raise ValueError(f"{name} 必须是非负整数")
    return raw


def _parse_positive_int(raw: object, name: str) -> int:
    value = _parse_non_negative_int(raw, name)
    if value == 0:
        raise ValueError(f"{name} 必须是正整数")
    return value


def _parse_ps_cpu(text: str, cpu_count: int) -> float:
    if type(cpu_count) is not int or cpu_count <= 0:
        raise ValueError("逻辑 CPU 数必须是正整数")
    values: list[float] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _FLOAT.fullmatch(stripped) is None:
            raise ValueError("ps CPU 输出格式错误")
        value = float(stripped)
        if not math.isfinite(value) or value < 0:
            raise ValueError("ps CPU 输出包含非法数值")
        values.append(value)
    if not values:
        raise ValueError("ps CPU 输出为空")
    normalized = sum(values) / cpu_count
    if normalized > 100.000001:
        raise ValueError("ps CPU 汇总超过逻辑 CPU 上限")
    return min(normalized, 100.0)


def _parse_linux_memory(text: str) -> tuple[int, int]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        match = re.fullmatch(r"([A-Za-z_()]+):\s+([0-9]+)\s+kB", line)
        if match is None:
            continue
        key, raw = match.groups()
        if key in values:
            raise ValueError(f"/proc/meminfo 字段重复: {key}")
        values[key] = int(raw) * 1024
    if "MemTotal" not in values or "MemAvailable" not in values:
        raise ValueError("/proc/meminfo 缺少 MemTotal 或 MemAvailable")
    if values["MemAvailable"] > values["MemTotal"]:
        raise ValueError("/proc/meminfo 可用内存大于总内存")
    return values["MemTotal"], values["MemAvailable"]


def _parse_linux_network(text: str) -> tuple[int, int]:
    received = 0
    transmitted = 0
    seen: set[str] = set()
    for line in text.splitlines()[2:]:
        if not line.strip():
            continue
        if ":" not in line:
            raise ValueError("/proc/net/dev 行缺少接口分隔符")
        interface, raw_values = line.split(":", 1)
        name = interface.strip()
        if _SAFE_COMPOSE_IDENTITY.fullmatch(name) is None or name in seen:
            raise ValueError("/proc/net/dev 接口名称非法或重复")
        fields = raw_values.split()
        if len(fields) != 16 or any(_NUMBER.fullmatch(item) is None for item in fields):
            raise ValueError("/proc/net/dev 计数字段格式错误")
        seen.add(name)
        received += int(fields[0])
        transmitted += int(fields[8])
    if not seen:
        raise ValueError("/proc/net/dev 没有网络接口")
    return received, transmitted


def _parse_darwin_memory(total_text: str, vm_text: str) -> tuple[int, int]:
    total_raw = total_text.strip()
    if _NUMBER.fullmatch(total_raw) is None:
        raise ValueError("hw.memsize 输出格式错误")
    total = int(total_raw)
    lines = vm_text.splitlines()
    if not lines:
        raise ValueError("vm_stat 输出为空")
    page_match = re.fullmatch(
        r"Mach Virtual Memory Statistics: \(page size of ([0-9]+) bytes\)", lines[0]
    )
    if page_match is None:
        raise ValueError("vm_stat 页大小格式错误")
    page_size = int(page_match.group(1))
    values: dict[str, int] = {}
    for line in lines[1:]:
        match = re.fullmatch(r"([^:]+):\s+([0-9]+)\.\s*", line)
        if match is None:
            if line.strip():
                raise ValueError("vm_stat 计数行格式错误")
            continue
        key, raw = match.groups()
        if key in values:
            raise ValueError(f"vm_stat 字段重复: {key}")
        values[key] = int(raw)
    available_keys = ("Pages free", "Pages inactive", "Pages speculative")
    if any(key not in values for key in available_keys):
        raise ValueError("vm_stat 缺少可用内存计数字段")
    available = sum(values[key] for key in available_keys) * page_size
    if available > total:
        raise ValueError("vm_stat 可用内存大于总内存")
    return total, available


def _parse_linux_file_handles(text: str) -> int:
    fields = text.split()
    if len(fields) != 3 or any(_NUMBER.fullmatch(item) is None for item in fields):
        raise ValueError("/proc/sys/fs/file-nr 格式错误")
    return int(fields[0])


def _parse_single_integer(text: str, name: str) -> int:
    raw = text.strip()
    if _NUMBER.fullmatch(raw) is None:
        raise ValueError(f"{name} 输出格式错误")
    return int(raw)


def _parse_linux_socket_lines(text: str) -> int:
    lines = [line for line in text.splitlines() if line.strip()]
    return len(lines)


def _parse_darwin_socket_lines(text: str) -> int:
    return sum(
        1
        for line in text.splitlines()
        if line.split() and re.fullmatch(r"(?:tcp|udp)[0-9]*", line.split()[0])
    )


def _parse_darwin_network(text: str) -> tuple[int, int]:
    header: list[str] | None = None
    counters: dict[str, tuple[int, int]] = {}
    for line in text.splitlines():
        fields = line.split()
        if not fields:
            continue
        if fields[0] == "Name":
            if header is None:
                header = fields
            elif fields != header:
                raise ValueError("netstat 网络表头发生变化")
            if tuple(fields[-8:]) != (
                "Ipkts",
                "Ierrs",
                "Ibytes",
                "Opkts",
                "Oerrs",
                "Obytes",
                "Coll",
                "Drop",
            ):
                raise ValueError("netstat 网络表头缺少固定计数列")
            continue
        if header is None:
            raise ValueError("netstat 网络输出缺少表头")
        if len(fields) not in {len(header), len(header) - 1}:
            raise ValueError("netstat 网络计数列数错误")
        name = fields[0]
        if _NETWORK_INTERFACE.fullmatch(name) is None:
            raise ValueError("netstat 网络接口名非法")
        inbound = fields[-6]
        outbound = fields[-3]
        if _NUMBER.fullmatch(inbound) is None or _NUMBER.fullmatch(outbound) is None:
            raise ValueError("netstat 网络字节计数格式错误")
        current = (int(inbound), int(outbound))
        previous = counters.get(name, (0, 0))
        counters[name] = (max(previous[0], current[0]), max(previous[1], current[1]))
    if not counters:
        raise ValueError("netstat 网络输出没有接口")
    return sum(item[0] for item in counters.values()), sum(item[1] for item in counters.values())


class LoadHostProbe:
    def __init__(
        self,
        runner: CommandRunner,
        *,
        platform: str | None = None,
        cpu_count: int | None = None,
        timeout_seconds: float = 5,
    ) -> None:
        self.runner = runner
        self.platform = platform or sys.platform
        self.cpu_count = cpu_count or os.cpu_count() or 1
        self.timeout_seconds = _bounded_timeout(timeout_seconds)
        if self.platform not in {"linux", "darwin"}:
            raise ValueError(f"不支持的负载机平台: {self.platform}")

    def _run(self, *argv: str) -> str:
        return _run_checked(self.runner, argv, timeout_seconds=self.timeout_seconds)

    def collect(self) -> LoadHostMetrics:
        cpu = _parse_ps_cpu(self._run("ps", "-A", "-o", "%cpu="), self.cpu_count)
        if self.platform == "linux":
            memory_total, memory_available = _parse_linux_memory(self._run("cat", "/proc/meminfo"))
            received, transmitted = _parse_linux_network(self._run("cat", "/proc/net/dev"))
            sockets = _parse_linux_socket_lines(self._run("ss", "-H", "-a"))
            handles = _parse_linux_file_handles(self._run("cat", "/proc/sys/fs/file-nr"))
        else:
            memory_total, memory_available = _parse_darwin_memory(
                self._run("sysctl", "-n", "hw.memsize"),
                self._run("vm_stat"),
            )
            received, transmitted = _parse_darwin_network(self._run("netstat", "-ibdn"))
            sockets = _parse_darwin_socket_lines(self._run("netstat", "-an", "-f", "inet"))
            handles = _parse_single_integer(
                self._run("sysctl", "-n", "kern.num_files"),
                "kern.num_files",
            )
        return LoadHostMetrics(
            cpu_percent=cpu,
            memory_total_bytes=memory_total,
            memory_available_bytes=memory_available,
            network_receive_bytes=received,
            network_transmit_bytes=transmitted,
            open_socket_count=sockets,
            open_file_handle_count=handles,
        )


@dataclass(frozen=True, slots=True)
class FilesystemMetrics:
    requested_path: str
    mountpoint: str
    total_bytes: int
    available_bytes: int

    def __post_init__(self) -> None:
        if (
            not PurePosixPath(self.requested_path).is_absolute()
            or not PurePosixPath(self.mountpoint).is_absolute()
        ):
            raise ValueError("文件系统指标路径必须是绝对路径")
        _parse_positive_int(self.total_bytes, "filesystem.total_bytes")
        _parse_non_negative_int(self.available_bytes, "filesystem.available_bytes")
        if self.available_bytes > self.total_bytes:
            raise ValueError("文件系统可用空间不能大于总空间")


@dataclass(frozen=True, slots=True)
class DirectorySizeMetrics:
    requested_path: str
    size_bytes: int

    def __post_init__(self) -> None:
        if self.requested_path not in TARGET_DATA_DIRECTORY_PATHS:
            raise ValueError("目录字节指标只允许固定数据目录")
        _parse_non_negative_int(self.size_bytes, "directory.size_bytes")


@dataclass(frozen=True, slots=True)
class TargetHostMetrics:
    filesystems: tuple[FilesystemMetrics, ...]
    directory_sizes: tuple[DirectorySizeMetrics, ...]
    memory_total_bytes: int
    memory_available_bytes: int
    oom_events: int

    def __post_init__(self) -> None:
        if not self.filesystems:
            raise ValueError("目标机至少需要一个文件系统指标")
        requested = [item.requested_path for item in self.filesystems]
        if len(requested) != len(set(requested)):
            raise ValueError("目标机文件系统指标路径重复")
        directory_paths = tuple(item.requested_path for item in self.directory_sizes)
        if directory_paths and directory_paths != TARGET_DATA_DIRECTORY_PATHS:
            raise ValueError("目标机目录字节指标必须为空或完整覆盖固定数据目录")
        _parse_positive_int(self.memory_total_bytes, "target.memory_total_bytes")
        _parse_non_negative_int(self.memory_available_bytes, "target.memory_available_bytes")
        _parse_non_negative_int(self.oom_events, "target.oom_events")
        if self.memory_available_bytes > self.memory_total_bytes:
            raise ValueError("目标机可用内存不能大于总内存")


def _parse_df(text: str, requested_path: str) -> FilesystemMetrics:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) != 2 or not lines[0].startswith("Filesystem "):
        raise ValueError("df 输出必须包含一个表头和一条数据")
    fields = lines[1].split()
    if len(fields) != 6 or any(_NUMBER.fullmatch(fields[index]) is None for index in (1, 2, 3)):
        raise ValueError("df 数据列格式错误")
    if not fields[4].endswith("%") or _NUMBER.fullmatch(fields[4][:-1]) is None:
        raise ValueError("df 使用率格式错误")
    return FilesystemMetrics(
        requested_path=requested_path,
        mountpoint=fields[5],
        total_bytes=int(fields[1]),
        available_bytes=int(fields[3]),
    )


def _parse_oom_events(text: str) -> int:
    found: int | None = None
    for line in text.splitlines():
        match = re.fullmatch(r"oom_kill\s+([0-9]+)", line)
        if match is None:
            continue
        if found is not None:
            raise ValueError("/proc/vmstat oom_kill 字段重复")
        found = int(match.group(1))
    if found is None:
        raise ValueError("/proc/vmstat 缺少 oom_kill")
    return found


def _parse_directory_size(text: str, requested_path: str) -> DirectorySizeMetrics:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError("du 输出必须恰好包含一条目录记录")
    fields = lines[0].split()
    if len(fields) != 2 or _NUMBER.fullmatch(fields[0]) is None:
        raise ValueError("du 目录字节输出格式错误")
    if fields[1] != requested_path:
        raise ValueError("du 目录字节输出路径不匹配")
    return DirectorySizeMetrics(requested_path=requested_path, size_bytes=int(fields[0]))


class TargetHostProbe:
    def __init__(
        self,
        ssh_runner: SshCommandRunner,
        *,
        filesystem_paths: Sequence[str],
        directory_paths: Sequence[str] = TARGET_DATA_DIRECTORY_PATHS,
        timeout_seconds: float = 5,
    ) -> None:
        paths = tuple(filesystem_paths)
        if not paths or len(paths) != len(set(paths)):
            raise ValueError("目标机文件系统路径不能为空或重复")
        for item in paths:
            parsed = PurePosixPath(item)
            if not parsed.is_absolute() or _REMOTE_TOKEN.fullmatch(item) is None:
                raise ValueError("目标机文件系统路径必须是安全绝对路径")
        directories = tuple(directory_paths)
        if directories != TARGET_DATA_DIRECTORY_PATHS:
            raise ValueError("目标机递归目录字节探针只允许固定数据目录")
        self.runner = ssh_runner
        self.filesystem_paths = paths
        self.directory_paths = directories
        self.timeout_seconds = _bounded_timeout(timeout_seconds)

    def _run(self, *argv: str) -> str:
        return _run_checked(self.runner, argv, timeout_seconds=self.timeout_seconds)

    def collect(self) -> TargetHostMetrics:
        filesystems = tuple(
            _parse_df(self._run("df", "-B1", "-P", "--", item), item)
            for item in self.filesystem_paths
        )
        memory_total, memory_available = _parse_linux_memory(self._run("cat", "/proc/meminfo"))
        oom_events = _parse_oom_events(self._run("cat", "/proc/vmstat"))
        return TargetHostMetrics(
            filesystems=filesystems,
            directory_sizes=(),
            memory_total_bytes=memory_total,
            memory_available_bytes=memory_available,
            oom_events=oom_events,
        )

    def collect_directory_sizes(self) -> tuple[DirectorySizeMetrics, ...]:
        return tuple(
            _parse_directory_size(
                self._run("du", "-s", "-B1", "--", item),
                item,
            )
            for item in self.directory_paths
        )


def _memory_bytes(raw: str) -> int:
    match = _MEMORY_SIZE.fullmatch(raw)
    if match is None:
        raise ValueError(f"内存大小格式错误: {_redact(raw)}")
    factors = {
        "B": 1,
        "kB": 1000,
        "KB": 1000,
        "KiB": 1024,
        "MB": 1000**2,
        "MiB": 1024**2,
        "GB": 1000**3,
        "GiB": 1024**3,
        "TB": 1000**4,
        "TiB": 1024**4,
    }
    try:
        value = Decimal(match.group("value")) * factors[match.group("unit")]
    except InvalidOperation as error:
        raise ValueError("内存大小不是有限数值") from error
    if value != value.to_integral_value() or value < 0:
        raise ValueError("内存大小不能精确转换为字节")
    return int(value)


@dataclass(frozen=True, slots=True)
class _ContainerIdentity:
    container_id: str
    project: str
    service: str
    restart_count: int
    healthy: bool


def _parse_container_ids(text: str) -> tuple[str, ...]:
    ids = tuple(line.strip() for line in text.splitlines() if line.strip())
    if len(ids) != len(set(ids)) or any(_CONTAINER_ID.fullmatch(item) is None for item in ids):
        raise ValueError("Docker 容器列表包含重复或非完整 ID")
    return ids


def _parse_inspect(
    text: str,
    expected_ids: Sequence[str],
    compose_projects: frozenset[str],
) -> dict[str, _ContainerIdentity]:
    try:
        document: Any = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError("docker inspect 输出不是 JSON") from error
    if type(document) is not list:
        raise ValueError("docker inspect 顶层必须是数组")
    identities: dict[str, _ContainerIdentity] = {}
    observed_ids: set[str] = set()
    for raw in document:
        if type(raw) is not dict:
            raise ValueError("docker inspect 项必须是对象")
        container_id = raw.get("Id")
        if not isinstance(container_id, str) or _CONTAINER_ID.fullmatch(container_id) is None:
            raise ValueError("docker inspect 缺少完整容器 ID")
        if container_id in observed_ids:
            raise ValueError("docker inspect 容器 ID 重复")
        observed_ids.add(container_id)
        config = raw.get("Config")
        state = raw.get("State")
        if type(config) is not dict or type(state) is not dict:
            raise ValueError("docker inspect 缺少 Config 或 State")
        labels = config.get("Labels")
        if labels is None:
            labels = {}
        if type(labels) is not dict:
            raise ValueError("docker inspect Labels 必须是对象")
        project = labels.get("com.docker.compose.project")
        if project not in compose_projects:
            continue
        service = labels.get("com.docker.compose.service")
        if not isinstance(service, str) or _SAFE_COMPOSE_IDENTITY.fullmatch(service) is None:
            raise ValueError("受监控 Compose 容器缺少精确 service 身份")
        restart_count = _parse_non_negative_int(raw.get("RestartCount"), "RestartCount")
        running = state.get("Running")
        if type(running) is not bool:
            raise ValueError("docker inspect State.Running 必须是布尔值")
        health = state.get("Health")
        health_status: object = None
        if health is not None:
            if type(health) is not dict:
                raise ValueError("docker inspect State.Health 必须是对象")
            health_status = health.get("Status")
            if health_status not in {"healthy", "starting", "unhealthy"}:
                raise ValueError("docker inspect 健康状态不合法")
        identities[container_id] = _ContainerIdentity(
            container_id=container_id,
            project=project,
            service=service,
            restart_count=restart_count,
            healthy=running and health_status in {None, "healthy"},
        )
    if observed_ids != set(expected_ids):
        raise ValueError("docker inspect 结果与请求容器 ID 不一致")
    return identities


def _parse_docker_stats(
    text: str,
    identities: Mapping[str, _ContainerIdentity],
) -> tuple[ContainerMetrics, ...]:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines or not lines[0].startswith("CONTAINER ID "):
        raise ValueError("docker stats 缺少固定表头")
    observed: dict[str, ContainerMetrics] = {}
    for line in lines[1:]:
        match = _DOCKER_STATS.fullmatch(line)
        if match is None:
            raise ValueError("docker stats 数据行格式错误")
        container_id = match.group("id")
        identity = identities.get(container_id)
        if identity is None or container_id in observed:
            raise ValueError("docker stats 包含未知或重复容器 ID")
        observed[container_id] = ContainerMetrics(
            container_id=container_id,
            compose_project=identity.project,
            compose_service=identity.service,
            cpu_percent=float(match.group("cpu")),
            memory_bytes=_memory_bytes(match.group("memory")),
            restart_count=identity.restart_count,
            healthy=identity.healthy,
        )
    if set(observed) != set(identities):
        raise ValueError("docker stats 缺少受监控 Compose 容器")
    return tuple(observed[item] for item in sorted(observed))


class DockerMetricsProbe:
    def __init__(
        self,
        runner: CommandRunner,
        *,
        compose_projects: Sequence[str],
        timeout_seconds: float = 10,
    ) -> None:
        projects = frozenset(compose_projects)
        if not projects or any(_SAFE_COMPOSE_IDENTITY.fullmatch(item) is None for item in projects):
            raise ValueError("Docker 探针必须声明精确 Compose project")
        self.runner = runner
        self.compose_projects = projects
        self.timeout_seconds = _bounded_timeout(timeout_seconds)

    def _run(self, *argv: str) -> str:
        return _run_checked(self.runner, argv, timeout_seconds=self.timeout_seconds)

    def collect(self) -> tuple[ContainerMetrics, ...]:
        ids = _parse_container_ids(self._run("docker", "ps", "-q", "--no-trunc"))
        if not ids:
            return ()
        identities = _parse_inspect(
            self._run("docker", "inspect", *ids),
            ids,
            self.compose_projects,
        )
        if not identities:
            return ()
        stats = self._run("docker", "stats", "--no-stream", "--no-trunc", *sorted(identities))
        return _parse_docker_stats(stats, identities)


def _parse_consumer_group_lag(text: str, expected_group: str) -> int:
    header: tuple[str, ...] | None = None
    lag_index = -1
    group_index = -1
    observed: set[tuple[str, int]] = set()
    total_lag = 0
    for line in text.splitlines():
        fields = tuple(line.split())
        if not fields or line.startswith("Consumer group '"):
            continue
        if {"GROUP", "TOPIC", "PARTITION", "LAG"}.issubset(fields):
            if header is not None:
                raise ValueError("Kafka consumer group 输出包含重复表头")
            header = fields
            lag_index = fields.index("LAG")
            group_index = fields.index("GROUP")
            continue
        if header is None or len(fields) != len(header):
            raise ValueError("Kafka consumer group 输出结构不合法")
        row = dict(zip(header, fields, strict=True))
        if fields[group_index] != expected_group:
            raise ValueError("Kafka consumer group 输出组名不匹配")
        topic = row["TOPIC"]
        partition_raw = row["PARTITION"]
        lag_raw = fields[lag_index]
        if (
            _SAFE_IDENTITY.fullmatch(topic) is None
            or _NUMBER.fullmatch(partition_raw) is None
            or _NUMBER.fullmatch(lag_raw) is None
        ):
            raise ValueError("Kafka consumer group 分区或 lag 不可证明")
        key = (topic, int(partition_raw))
        if key in observed:
            raise ValueError("Kafka consumer group 输出分区重复")
        observed.add(key)
        total_lag += int(lag_raw)
    if header is None or not observed:
        raise ValueError("Kafka consumer group 没有可证明的分区 lag")
    return total_lag


class KafkaLagProbe:
    def __init__(
        self,
        runner: CommandRunner,
        *,
        compose_project: str,
        compose_service: str,
        consumer_groups: Sequence[str],
        timeout_seconds: float = 10,
    ) -> None:
        groups = tuple(consumer_groups)
        if (
            _SAFE_COMPOSE_IDENTITY.fullmatch(compose_project) is None
            or _SAFE_COMPOSE_IDENTITY.fullmatch(compose_service) is None
            or not groups
            or len(groups) != len(set(groups))
            or any(_SAFE_IDENTITY.fullmatch(item) is None for item in groups)
        ):
            raise ValueError("Kafka lag 探针必须声明精确 Compose 身份和 consumer group")
        self.runner = runner
        self.compose_project = compose_project
        self.compose_service = compose_service
        self.consumer_groups = groups
        self.timeout_seconds = _bounded_timeout(timeout_seconds)

    def _run(self, *argv: str) -> str:
        return _run_checked(self.runner, argv, timeout_seconds=self.timeout_seconds)

    def _container_id(self) -> str:
        ids = _parse_container_ids(self._run("docker", "ps", "-q", "--no-trunc"))
        if not ids:
            raise ProbeError("Kafka lag 探针未找到运行容器")
        identities = _parse_inspect(
            self._run("docker", "inspect", *ids),
            ids,
            frozenset({self.compose_project}),
        )
        candidates = tuple(
            item
            for item in identities.values()
            if item.service == self.compose_service and item.healthy
        )
        if len(candidates) != 1:
            raise ProbeError("Kafka lag 探针需要唯一健康的精确 Kafka 容器")
        return candidates[0].container_id

    def collect(self) -> int:
        container_id = self._container_id()
        total = 0
        for group in self.consumer_groups:
            output = self._run(
                "docker",
                "exec",
                container_id,
                "/opt/kafka/bin/kafka-consumer-groups.sh",
                "--bootstrap-server",
                "kafka:29092",
                "--describe",
                "--group",
                group,
            )
            total += _parse_consumer_group_lag(output, group)
        return total


def _csv_rows(text: str, columns: int, name: str) -> tuple[tuple[str, ...], ...]:
    try:
        rows = tuple(
            tuple(field.strip() for field in row)
            for row in csv.reader(io.StringIO(text), strict=True)
            if row and any(field.strip() for field in row)
        )
    except csv.Error as error:
        raise ValueError(f"{name} CSV 格式错误") from error
    if any(len(row) != columns for row in rows):
        raise ValueError(f"{name} CSV 列数错误")
    return rows


def _gpu_number(raw: str, name: str) -> float:
    if _FLOAT.fullmatch(raw) is None:
        raise ValueError(f"{name} 不是非负数")
    value = float(raw)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} 不是有限非负数")
    return value


def _process_name(raw: str) -> str:
    basename = PurePosixPath(raw.strip()).name
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}", basename):
        return basename
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"redacted-process-{digest}"


class NvidiaSmiProbe:
    def __init__(self, runner: CommandRunner, *, timeout_seconds: float = 10) -> None:
        self.runner = runner
        self.timeout_seconds = _bounded_timeout(timeout_seconds)

    def _run(self, *argv: str) -> str:
        return _run_checked(self.runner, argv, timeout_seconds=self.timeout_seconds)

    def collect(self) -> tuple[GpuMetrics, ...]:
        gpu_rows = _csv_rows(
            self._run(
                "nvidia-smi",
                "--query-gpu=uuid,utilization.gpu,memory.used",
                "--format=csv,noheader,nounits",
            ),
            3,
            "nvidia-smi GPU",
        )
        process_rows = _csv_rows(
            self._run(
                "nvidia-smi",
                "--query-compute-apps=gpu_uuid,pid,process_name,used_memory",
                "--format=csv,noheader,nounits",
            ),
            4,
            "nvidia-smi 进程",
        )
        gpus: dict[str, tuple[float, int]] = {}
        for uuid, utilization, memory_mib in gpu_rows:
            if _GPU_UUID.fullmatch(uuid) is None or uuid in gpus:
                raise ValueError("nvidia-smi GPU UUID 非法或重复")
            utilization_value = _gpu_number(utilization, "GPU 利用率")
            if utilization_value > 100:
                raise ValueError("GPU 利用率不能大于 100")
            memory_value = _gpu_number(memory_mib, "GPU 已用显存")
            if not memory_value.is_integer():
                raise ValueError("GPU 已用显存必须是整数 MiB")
            gpus[uuid] = (utilization_value, int(memory_value) * 1024**2)
        if not gpus:
            raise ValueError("nvidia-smi 未返回 GPU")
        processes: dict[str, list[GpuProcessMetrics]] = {uuid: [] for uuid in gpus}
        seen_processes: set[tuple[str, int]] = set()
        for uuid, pid_raw, name, memory_mib in process_rows:
            if uuid not in gpus:
                raise ValueError("nvidia-smi 进程引用未知 GPU UUID")
            if _NUMBER.fullmatch(pid_raw) is None:
                raise ValueError("nvidia-smi 进程 PID 格式错误")
            pid = int(pid_raw)
            process_key = (uuid, pid)
            if process_key in seen_processes:
                raise ValueError("nvidia-smi GPU 进程重复")
            seen_processes.add(process_key)
            memory_value = _gpu_number(memory_mib, "GPU 进程显存")
            if not memory_value.is_integer():
                raise ValueError("GPU 进程显存必须是整数 MiB")
            processes[uuid].append(
                GpuProcessMetrics(
                    pid=pid,
                    name=_process_name(name),
                    memory_bytes=int(memory_value) * 1024**2,
                )
            )
        return tuple(
            GpuMetrics(
                uuid=uuid,
                utilization_percent=gpus[uuid][0],
                memory_used_bytes=gpus[uuid][1],
                processes=tuple(sorted(processes[uuid], key=lambda item: item.pid)),
            )
            for uuid in sorted(gpus)
        )


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    content_type: str
    body: bytes


class HttpClient(Protocol):
    def get(self, url: str, *, timeout_seconds: float, max_bytes: int) -> HttpResponse: ...


class HttpxProbeClient:
    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self.transport = transport

    def get(self, url: str, *, timeout_seconds: float, max_bytes: int) -> HttpResponse:
        timeout = _bounded_timeout(timeout_seconds)
        if type(max_bytes) is not int or not 1 <= max_bytes <= 4 * 1024 * 1024:
            raise ValueError("HTTP 探针响应上限必须位于 1–4194304 字节")
        try:
            with httpx.Client(
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
                transport=self.transport,
            ) as client:
                with client.stream("GET", url) as response:
                    if response.status_code != 200:
                        raise ProbeError(f"HTTP 探针返回非 200 状态: {response.status_code}")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > max_bytes:
                            raise ProbeError("HTTP 探针响应超过安全上限")
                    return HttpResponse(
                        status_code=response.status_code,
                        content_type=response.headers.get("content-type", ""),
                        body=bytes(body),
                    )
        except ProbeError:
            raise
        except httpx.HTTPError as error:
            raise ProbeError(f"HTTP 探针失败: {type(error).__name__}") from error


def _probe_origin(origin: str, expected_port: int) -> str:
    parsed = urlsplit(origin.rstrip("/"))
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.port != expected_port
    ):
        raise ValueError(f"指标 origin 必须是无凭据的 {expected_port} HTTP/HTTPS origin")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _response_text(
    response: HttpResponse,
    *,
    content_types: Sequence[str],
    max_bytes: int,
) -> str:
    if response.status_code != 200:
        raise ProbeError(f"HTTP 探针返回非 200 状态: {response.status_code}")
    if len(response.body) > max_bytes:
        raise ProbeError("HTTP 探针响应超过安全上限")
    media_type = response.content_type.split(";", 1)[0].strip().lower()
    if media_type not in content_types:
        raise ProbeError(f"HTTP 探针 Content-Type 不合法: {_redact(media_type)}")
    try:
        return response.body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ProbeError("HTTP 探针响应不是 UTF-8") from error


def _parse_json_response(response: HttpResponse, *, max_bytes: int) -> object:
    text = _response_text(response, content_types=("application/json",), max_bytes=max_bytes)
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError("HTTP 指标响应不是 JSON") from error


@dataclass(frozen=True, slots=True)
class PrometheusSample:
    name: str
    labels: Mapping[str, str]
    value: float


def _decode_label_value(raw: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(raw):
        character = raw[index]
        if character != "\\":
            if character in {'"', "\n", "\r"}:
                raise ValueError("Prometheus label 包含未转义控制字符")
            result.append(character)
            index += 1
            continue
        index += 1
        if index >= len(raw) or raw[index] not in {"\\", '"', "n"}:
            raise ValueError("Prometheus label 转义非法")
        result.append("\n" if raw[index] == "n" else raw[index])
        index += 1
    return "".join(result)


def _parse_labels(raw: str | None) -> dict[str, str]:
    if raw is None or not raw.strip():
        return {}
    labels: dict[str, str] = {}
    position = 0
    pair = re.compile(r'\s*([A-Za-z_][A-Za-z0-9_]*)="((?:\\.|[^"\\])*)"\s*(,|$)')
    while position < len(raw):
        match = pair.match(raw, position)
        if match is None:
            raise ValueError("Prometheus label 列表格式错误")
        key, encoded, delimiter = match.groups()
        if key in labels:
            raise ValueError(f"Prometheus label 重复: {key}")
        labels[key] = _decode_label_value(encoded)
        position = match.end()
        if delimiter == "," and position == len(raw):
            raise ValueError("Prometheus label 列表不能以逗号结尾")
        if delimiter == "" and position != len(raw):
            raise ValueError("Prometheus label 列表尾部非法")
    return labels


def parse_prometheus(text: str) -> tuple[PrometheusSample, ...]:
    if len(text.encode("utf-8")) > 2 * 1024 * 1024:
        raise ValueError("Prometheus 指标超过解析上限")
    samples: list[PrometheusSample] = []
    seen: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        if len(line) > 8192:
            raise ValueError("Prometheus 单行超过解析上限")
        match = _PROMETHEUS_SAMPLE.fullmatch(line)
        if match is None:
            raise ValueError("Prometheus 样本行格式错误")
        labels = _parse_labels(match.group("labels"))
        value = float(match.group("value"))
        if not math.isfinite(value) or value < 0:
            raise ValueError("Prometheus 样本必须是有限非负数")
        key = (match.group("name"), tuple(sorted(labels.items())))
        if key in seen:
            raise ValueError("Prometheus 样本时间序列重复")
        seen.add(key)
        samples.append(PrometheusSample(match.group("name"), labels, value))
    return tuple(samples)


def _sample_integer(sample: PrometheusSample) -> int:
    if not sample.value.is_integer():
        raise ValueError(f"Prometheus 计数指标不是整数: {sample.name}")
    return int(sample.value)


def _require_labels(sample: PrometheusSample, expected: frozenset[str]) -> None:
    if frozenset(sample.labels) != expected:
        raise ValueError(f"Prometheus 指标 label 集合不合法: {sample.name}")
    if any(_SAFE_IDENTITY.fullmatch(value) is None for value in sample.labels.values()):
        raise ValueError(f"Prometheus 指标 label 值不安全: {sample.name}")


@dataclass(frozen=True, slots=True)
class ControlPlaneMetrics:
    task_queue_depth: int
    outbox_pending: int
    kafka_lag: int
    instances: tuple[InstanceCapacityMetrics, ...]

    def __post_init__(self) -> None:
        _parse_non_negative_int(self.task_queue_depth, "control.task_queue_depth")
        _parse_non_negative_int(self.outbox_pending, "control.outbox_pending")
        _parse_non_negative_int(self.kafka_lag, "control.kafka_lag")
        ids = [item.instance_id for item in self.instances]
        if len(ids) != len(set(ids)):
            raise ValueError("Control 实例指标重复")


class ControlMetricsProbe:
    def __init__(
        self,
        client: HttpClient,
        control_origin: str,
        *,
        timeout_seconds: float = 5,
        max_bytes: int = 2 * 1024 * 1024,
        kafka_lag_source: Callable[[], int] | None = None,
    ) -> None:
        self.client = client
        self.origin = _probe_origin(control_origin, 18100)
        self.timeout_seconds = _bounded_timeout(timeout_seconds)
        if type(max_bytes) is not int or not 1 <= max_bytes <= 4 * 1024 * 1024:
            raise ValueError("Control 指标响应上限不合法")
        self.max_bytes = max_bytes
        self.kafka_lag_source = kafka_lag_source

    def _get(self, path: str) -> HttpResponse:
        return self.client.get(
            f"{self.origin}{path}",
            timeout_seconds=self.timeout_seconds,
            max_bytes=self.max_bytes,
        )

    def collect_instances(self) -> tuple[InstanceCapacityMetrics, ...]:
        document = _parse_json_response(
            self._get("/ops/operator-instances/snapshot"),
            max_bytes=self.max_bytes,
        )
        if type(document) is not list:
            raise ValueError("Control 实例快照顶层必须是数组")
        instances: list[InstanceCapacityMetrics] = []
        seen: set[str] = set()
        for raw in document:
            if type(raw) is not dict:
                raise ValueError("Control 实例快照项必须是对象")
            instance_id = raw.get("instance_id")
            operator_code = raw.get("operator_code")
            if (
                not isinstance(instance_id, str)
                or _SAFE_IDENTITY.fullmatch(instance_id) is None
                or not isinstance(operator_code, str)
                or _SAFE_IDENTITY.fullmatch(operator_code) is None
            ):
                raise ValueError("Control 实例快照身份不安全")
            if instance_id in seen:
                raise ValueError("Control 实例快照 ID 重复")
            seen.add(instance_id)
            instances.append(
                InstanceCapacityMetrics(
                    instance_id=instance_id,
                    inflight=_parse_non_negative_int(
                        raw.get("reported_inflight"),
                        "reported_inflight",
                    ),
                    active_leases=_parse_non_negative_int(
                        raw.get("active_lease_count"),
                        "active_lease_count",
                    ),
                    declared_capacity=_parse_positive_int(
                        raw.get("declared_capacity"),
                        "declared_capacity",
                    ),
                )
            )
        return tuple(sorted(instances, key=lambda item: item.instance_id))

    def _collect_queue(self) -> tuple[int, int]:
        document = _parse_json_response(self._get("/ops/queues"), max_bytes=self.max_bytes)
        if type(document) is not dict or type(document.get("queues")) is not list:
            raise ValueError("Control 队列响应结构错误")
        depth = 0
        for raw in document["queues"]:
            if type(raw) is not dict:
                raise ValueError("Control 队列项必须是对象")
            depth += _parse_non_negative_int(raw.get("count"), "queue.count")
        return depth, _parse_non_negative_int(document.get("outbox_pending"), "outbox_pending")

    def _collect_kafka_lag(self) -> int:
        text = _response_text(
            self._get("/metrics"),
            content_types=("text/plain", "application/openmetrics-text"),
            max_bytes=self.max_bytes,
        )
        lag = 0
        observed = 0
        for sample in parse_prometheus(text):
            if sample.name != "algorithm_kafka_consumer_lag":
                continue
            _require_labels(sample, frozenset({"topic", "consumer_group", "partition"}))
            lag += _sample_integer(sample)
            observed += 1
        if observed == 0:
            raise ProbeError("Control metrics 缺少可证明的 Kafka lag 时序")
        return lag

    def collect(self) -> ControlPlaneMetrics:
        queue_depth, outbox_pending = self._collect_queue()
        kafka_lag = (
            self._collect_kafka_lag()
            if self.kafka_lag_source is None
            else _parse_non_negative_int(self.kafka_lag_source(), "external.kafka_lag")
        )
        return ControlPlaneMetrics(
            task_queue_depth=queue_depth,
            outbox_pending=outbox_pending,
            kafka_lag=kafka_lag,
            instances=self.collect_instances(),
        )


@dataclass(frozen=True, slots=True)
class GatewayMetrics:
    counters: GatewayCounters
    instance_requests: Mapping[str, int]

    def __post_init__(self) -> None:
        for instance_id, count in self.instance_requests.items():
            if _SAFE_IDENTITY.fullmatch(instance_id) is None:
                raise ValueError("Gateway 实例请求指标 ID 不安全")
            _parse_non_negative_int(count, "gateway.instance_requests")


class GatewayMetricsProbe:
    def __init__(
        self,
        client: HttpClient,
        gateway_origin: str,
        *,
        timeout_seconds: float = 5,
        max_bytes: int = 2 * 1024 * 1024,
    ) -> None:
        self.client = client
        self.origin = _probe_origin(gateway_origin, 18103)
        self.timeout_seconds = _bounded_timeout(timeout_seconds)
        if type(max_bytes) is not int or not 1 <= max_bytes <= 4 * 1024 * 1024:
            raise ValueError("Gateway 指标响应上限不合法")
        self.max_bytes = max_bytes

    def collect(self) -> GatewayMetrics:
        response = self.client.get(
            f"{self.origin}/metrics",
            timeout_seconds=self.timeout_seconds,
            max_bytes=self.max_bytes,
        )
        text = _response_text(
            response,
            content_types=("text/plain", "application/openmetrics-text"),
            max_bytes=self.max_bytes,
        )
        requests_total = 0
        acquired = 0
        rejected = 0
        released = 0
        instance_requests: dict[str, int] = {}
        for sample in parse_prometheus(text):
            if sample.name == "algorithm_operator_request_latency_seconds_count":
                _require_labels(
                    sample,
                    frozenset({"operator_code", "capability", "instance_id"}),
                )
                count = _sample_integer(sample)
                requests_total += count
                instance_id = sample.labels["instance_id"]
                instance_requests[instance_id] = instance_requests.get(instance_id, 0) + count
            elif sample.name == "algorithm_capacity_lease_events_total":
                _require_labels(
                    sample,
                    frozenset({"capability", "outcome", "instance_id"}),
                )
                count = _sample_integer(sample)
                outcome = sample.labels["outcome"]
                if outcome == "acquired":
                    acquired += count
                elif outcome == "rejected":
                    rejected += count
                elif outcome == "released":
                    released += count
        return GatewayMetrics(
            counters=GatewayCounters(
                requests_total=requests_total,
                lease_acquired_total=acquired,
                lease_rejected_total=rejected,
                lease_released_total=released,
            ),
            instance_requests=dict(sorted(instance_requests.items())),
        )
