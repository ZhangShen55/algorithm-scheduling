from __future__ import annotations

import asyncio
import json
import re
import shlex
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .catalog import FixtureDescriptor

_SAFE_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}")
_SAFE_USER = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}")
_REMOTE_SCRIPT = r'''
import concurrent.futures
import json
import sys
import time
import urllib.request


def network_receive_bytes():
    total = 0
    with open("/proc/net/dev", encoding="utf-8") as stream:
        for line in stream:
            if ":" not in line:
                continue
            interface, values = line.split(":", 1)
            if interface.strip() == "lo":
                continue
            total += int(values.split()[0])
    return total


def download(item):
    index, fixture_id, url, timeout_seconds = item
    started = time.perf_counter()
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "extreme-load-baseline/1"})
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            connected = time.perf_counter()
            size_bytes = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                size_bytes += len(chunk)
        elapsed = time.perf_counter() - started
        return {
            "request_index": index,
            "fixture_id": fixture_id,
            "succeeded": True,
            "size_bytes": size_bytes,
            "connect_seconds": connected - started,
            "elapsed_seconds": elapsed,
            "error_type": None,
        }
    except Exception as error:
        return {
            "request_index": index,
            "fixture_id": fixture_id,
            "succeeded": False,
            "size_bytes": 0,
            "connect_seconds": 0.0,
            "elapsed_seconds": time.perf_counter() - started,
            "error_type": type(error).__name__,
        }


payload = json.load(sys.stdin)
concurrency = payload["concurrency"]
fixtures = payload["fixtures"]
timeout_seconds = payload["timeout_seconds"]
work = [
    (
        index,
        fixtures[index % len(fixtures)]["fixture_id"],
        fixtures[index % len(fixtures)]["url"],
        timeout_seconds,
    )
    for index in range(concurrency)
]
network_before = network_receive_bytes()
started = time.perf_counter()
with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
    samples = list(executor.map(download, work))
elapsed = time.perf_counter() - started
network_after = network_receive_bytes()
successful_bytes = sum(item["size_bytes"] for item in samples if item["succeeded"])
print(json.dumps({
    "schema_version": 1,
    "concurrency": concurrency,
    "wall_elapsed_seconds": elapsed,
    "target_network_receive_bytes": max(0, network_after - network_before),
    "aggregate_bytes_per_second": successful_bytes / elapsed if elapsed > 0 else 0.0,
    "samples": samples,
}, sort_keys=True))
'''


@dataclass(frozen=True, slots=True)
class SourceResourceEvidence:
    evidence_id: str
    collected_at: str
    cpu_percent: float
    memory_percent: float
    network_transmit_bytes_per_second: float
    open_connections: int

    def __post_init__(self) -> None:
        if not self.evidence_id or not self.collected_at:
            raise ValueError("源端资源证据必须有标识和采集时间")
        if not 0 <= self.cpu_percent <= 100 or not 0 <= self.memory_percent <= 100:
            raise ValueError("源端 CPU/内存百分比不合法")
        if self.network_transmit_bytes_per_second < 0 or self.open_connections < 0:
            raise ValueError("源端网络或连接指标不能为负")


class DownloadSample(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    request_index: int = Field(ge=0)
    fixture_id: str = Field(min_length=1)
    succeeded: bool
    size_bytes: int = Field(ge=0)
    connect_seconds: float = Field(ge=0)
    elapsed_seconds: float = Field(gt=0)
    error_type: str | None

    @property
    def bytes_per_second(self) -> float:
        return self.size_bytes / self.elapsed_seconds


class RemoteDownloadDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: int = Field(ge=1)
    concurrency: int
    wall_elapsed_seconds: float = Field(gt=0)
    target_network_receive_bytes: int = Field(ge=0)
    aggregate_bytes_per_second: float = Field(ge=0)
    samples: tuple[DownloadSample, ...]

    @field_validator("samples", mode="before")
    @classmethod
    def freeze_samples(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_samples(self) -> RemoteDownloadDocument:
        if self.concurrency not in {1, 3, 10, 30}:
            raise ValueError("媒体下载基线并发只允许 1/3/10/30")
        if len(self.samples) != self.concurrency:
            raise ValueError("媒体下载样本数与并发档位不一致")
        indexes = {sample.request_index for sample in self.samples}
        if indexes != set(range(self.concurrency)):
            raise ValueError("媒体下载样本索引缺失或重复")
        return self


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class CommandRunner(Protocol):
    async def run(
        self,
        argv: Sequence[str],
        *,
        stdin: bytes,
        timeout_seconds: float,
    ) -> CommandResult: ...


class AsyncSubprocessRunner:
    async def run(
        self,
        argv: Sequence[str],
        *,
        stdin: bytes,
        timeout_seconds: float,
    ) -> CommandResult:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(stdin),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            return CommandResult(124, b"", b"")
        if len(stdout) > 1024 * 1024 or len(stderr) > 1024 * 1024:
            return CommandResult(125, b"", b"")
        return CommandResult(process.returncode or 0, stdout, stderr)


@dataclass(frozen=True, slots=True)
class MediaDownloadResult:
    status: str
    reason: str
    concurrency: int
    source_evidence: SourceResourceEvidence | None = None
    document: RemoteDownloadDocument | None = None

    def __post_init__(self) -> None:
        if self.status not in {"passed", "failed", "blocked"}:
            raise ValueError("媒体下载结果状态不合法")
        if not self.reason:
            raise ValueError("媒体下载结果原因不能为空")

    @property
    def attempts(self) -> int:
        return 0 if self.document is None else len(self.document.samples)

    @property
    def successes(self) -> int:
        if self.document is None:
            return 0
        return sum(sample.succeeded for sample in self.document.samples)

    @property
    def failure_rate(self) -> float:
        return 0.0 if self.attempts == 0 else (self.attempts - self.successes) / self.attempts

    def to_evidence(self) -> dict[str, object]:
        evidence: dict[str, object] = {
            "concurrency": self.concurrency,
            "attempt_count": self.attempts,
            "success_count": self.successes,
            "failure_count": self.attempts - self.successes,
            "failure_rate": self.failure_rate,
        }
        if self.source_evidence is not None:
            evidence["source_resources"] = asdict(self.source_evidence)
        if self.document is not None:
            evidence.update(
                {
                    "wall_elapsed_seconds": self.document.wall_elapsed_seconds,
                    "target_network_receive_bytes": self.document.target_network_receive_bytes,
                    "aggregate_bytes_per_second": self.document.aggregate_bytes_per_second,
                    "files": [
                        {
                            "request_index": sample.request_index,
                            "fixture_id": sample.fixture_id,
                            "succeeded": sample.succeeded,
                            "size_bytes": sample.size_bytes,
                            "connect_seconds": sample.connect_seconds,
                            "elapsed_seconds": sample.elapsed_seconds,
                            "bytes_per_second": sample.bytes_per_second,
                            "error_type": sample.error_type,
                        }
                        for sample in self.document.samples
                    ],
                }
            )
        return evidence


class MediaDownloadAdapter(Protocol):
    target_hostname: str

    async def run(
        self,
        fixtures: Sequence[FixtureDescriptor],
        *,
        concurrency: int,
    ) -> MediaDownloadResult: ...


class SshMediaDownloadAdapter:
    def __init__(
        self,
        *,
        target_hostname: str,
        ssh_user: str,
        ssh_port: int = 22,
        enabled: bool = False,
        source_evidence: SourceResourceEvidence | None = None,
        command_runner: CommandRunner | None = None,
        download_timeout_seconds: float = 1800,
    ) -> None:
        if _SAFE_HOST.fullmatch(target_hostname) is None:
            raise ValueError("目标主机名不安全")
        if _SAFE_USER.fullmatch(ssh_user) is None:
            raise ValueError("SSH 用户名不安全")
        if not 1 <= ssh_port <= 65535 or download_timeout_seconds <= 0:
            raise ValueError("SSH 端口或下载超时不合法")
        self.target_hostname = target_hostname
        self._ssh_user = ssh_user
        self._ssh_port = ssh_port
        self._enabled = enabled
        self._source_evidence = source_evidence
        self._runner = command_runner or AsyncSubprocessRunner()
        self._download_timeout_seconds = download_timeout_seconds

    async def run(
        self,
        fixtures: Sequence[FixtureDescriptor],
        *,
        concurrency: int,
    ) -> MediaDownloadResult:
        if concurrency not in {1, 3, 10, 30}:
            raise ValueError("媒体下载基线并发只允许 1/3/10/30")
        if self._source_evidence is None:
            return MediaDownloadResult(
                "blocked",
                "缺少源端文件服务资源的显式外部证据",
                concurrency,
            )
        if not self._enabled:
            return MediaDownloadResult(
                "blocked",
                "远程媒体下载执行默认关闭",
                concurrency,
                self._source_evidence,
            )
        if not fixtures:
            raise ValueError("媒体下载基线至少需要一个 fixture")
        payload = json.dumps(
            {
                "concurrency": concurrency,
                "timeout_seconds": self._download_timeout_seconds,
                "fixtures": [
                    {"fixture_id": fixture.fixture_id, "url": fixture.path}
                    for fixture in fixtures
                ],
            },
            ensure_ascii=False,
        ).encode()
        encoded_script = shlex.quote(_REMOTE_SCRIPT)
        argv = (
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ConnectTimeout=10",
            "-p",
            str(self._ssh_port),
            f"{self._ssh_user}@{self.target_hostname}",
            f"python3 -c {encoded_script}",
        )
        command = await self._runner.run(
            argv,
            stdin=payload,
            timeout_seconds=self._download_timeout_seconds + 30,
        )
        if command.returncode != 0:
            return MediaDownloadResult(
                "failed",
                f"目标主机媒体下载适配器退出码 {command.returncode}",
                concurrency,
                self._source_evidence,
            )
        try:
            raw_document: object = json.loads(command.stdout)
            document = RemoteDownloadDocument.model_validate(raw_document)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            return MediaDownloadResult(
                "failed",
                f"目标主机媒体下载证据无效: {type(error).__name__}",
                concurrency,
                self._source_evidence,
            )
        if document.concurrency != concurrency:
            return MediaDownloadResult(
                "failed",
                "目标主机返回的并发档位与计划不一致",
                concurrency,
                self._source_evidence,
            )
        mismatched_fixtures = [
            sample.request_index
            for sample in document.samples
            if sample.fixture_id
            != fixtures[sample.request_index % len(fixtures)].fixture_id
        ]
        if mismatched_fixtures:
            return MediaDownloadResult(
                "failed",
                "目标主机返回的 fixture 绑定与请求不一致",
                concurrency,
                self._source_evidence,
            )
        failures = sum(not sample.succeeded for sample in document.samples)
        return MediaDownloadResult(
            "passed" if failures == 0 else "failed",
            "媒体下载基线完成" if failures == 0 else "媒体下载基线存在失败",
            concurrency,
            self._source_evidence,
            document,
        )
