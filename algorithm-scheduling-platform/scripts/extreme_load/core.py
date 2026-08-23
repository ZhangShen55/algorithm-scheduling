from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

JsonObject = dict[str, Any]


class ResultCategory(StrEnum):
    SUCCESS = "success"
    BUSINESS_REJECTED = "business_rejected"
    OVERLOAD = "overload"
    TIMEOUT = "timeout"
    CONNECTION_FAILURE = "connection_failure"
    UNDEFINED_5XX = "undefined_5xx"
    LOAD_GENERATOR_FAILURE = "load_generator_failure"
    GUARDRAIL_ABORT = "guardrail_abort"


class GuardrailAbort(RuntimeError):
    category = ResultCategory.GUARDRAIL_ABORT


def _validated_origin(value: str, expected_port: int) -> str:
    parsed = urlsplit(value.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("北向地址必须是 HTTP/HTTPS origin")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("北向地址不得包含凭据、查询参数或 fragment")
    if parsed.path not in {"", "/"}:
        raise ValueError("北向地址只能包含 origin，不能预置业务路径")
    if parsed.port != expected_port:
        raise ValueError(f"北向地址必须使用端口 {expected_port}")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


@dataclass(frozen=True)
class NorthboundTargets:
    control_origin: str
    gateway_origin: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "control_origin", _validated_origin(self.control_origin, 18100))
        object.__setattr__(self, "gateway_origin", _validated_origin(self.gateway_origin, 18103))

    @staticmethod
    def _business_path(path: str, prefix: str) -> str:
        valid_prefix = path == prefix or (
            prefix.endswith("/") and path.startswith(prefix)
        ) or path.startswith(f"{prefix}/")
        if not valid_prefix or "?" in path or "#" in path or "//" in path:
            raise ValueError(f"业务路径必须位于 {prefix}")
        return path

    def control_url(self, path: str) -> str:
        return f"{self.control_origin}{self._business_path(path, '/api/course-jobs')}"

    def gateway_url(self, path: str) -> str:
        return f"{self.gateway_origin}{self._business_path(path, '/api/online/')}"

    def gateway_websocket_url(self, path: str) -> str:
        http_url = self.gateway_url(path)
        parsed = urlsplit(http_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return urlunsplit((scheme, parsed.netloc, parsed.path, "", ""))


class ReproducibleIdentity:
    def __init__(self, campaign_id: str, seed: int) -> None:
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError("seed 必须是整数")
        normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", campaign_id).strip("-")
        if not normalized or len(normalized) > 80:
            raise ValueError("campaign_id 必须是 1–80 位安全标识")
        self.campaign_id = normalized
        self.seed = seed

    def _digest(self, case_id: str, index: int, purpose: str) -> str:
        if not case_id or index < 0:
            raise ValueError("case_id 不能为空且 index 不能为负数")
        source = f"{self.campaign_id}\0{self.seed}\0{case_id}\0{index}\0{purpose}"
        return hashlib.sha256(source.encode()).hexdigest()

    def task_id(self, case_id: str, index: int) -> str:
        safe_case = re.sub(r"[^a-zA-Z0-9_-]+", "-", case_id).strip("-").lower()
        prefix = f"load-{self.campaign_id}-{safe_case}-{index}"
        return f"{prefix[:180]}-{self._digest(case_id, index, 'task')[:16]}"

    def trace_id(self, case_id: str, index: int) -> str:
        return f"load-{self._digest(case_id, index, 'trace')[:32]}"

    def request_id(self, case_id: str, index: int) -> str:
        safe_case = re.sub(r"[^a-zA-Z0-9_-]+", "-", case_id).strip("-").lower()
        prefix = f"{safe_case}-{index}"
        return f"{prefix[:120]}-{self._digest(case_id, index, 'request')[:12]}"

    def random(self, namespace: str) -> random.Random:
        digest = hashlib.sha256(
            f"{self.campaign_id}\0{self.seed}\0{namespace}".encode()
        ).digest()
        return random.Random(int.from_bytes(digest[:8], "big"))


def derive_campaign_id(release_key: str, seed: int) -> str:
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed 必须是整数")
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", release_key).strip("-").lower()
    if not normalized:
        raise ValueError("release_key 必须包含安全标识字符")
    digest = hashlib.sha256(f"{normalized}\0{seed}".encode()).hexdigest()[:16]
    return f"campaign-{normalized[:48]}-{digest}"


@dataclass(frozen=True)
class HttpRequestSpec:
    request_id: str
    method: str
    url: str
    json_body: Mapping[str, Any] | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    work_type: str = "http"
    expected_business_rejection: bool = False
    expected_lease_acquisition: bool | None = None

    def __post_init__(self) -> None:
        if self.method.upper() not in {"GET", "POST", "DELETE"}:
            raise ValueError("负载请求只支持 GET/POST/DELETE")
        parsed = urlsplit(self.url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ValueError("业务负载必须使用无凭据的 HTTP/HTTPS 北向地址")
        if parsed.port not in {18100, 18103}:
            raise ValueError("业务负载只能访问 18100 或 18103")
        if parsed.port == 18100 and not (
            parsed.path == "/api/course-jobs"
            or parsed.path.startswith("/api/course-jobs/")
        ):
            raise ValueError("18100 业务负载只能访问 /api/course-jobs")
        if parsed.port == 18103 and not parsed.path.startswith("/api/online/"):
            raise ValueError("18103 业务负载只能访问 /api/online/")


@dataclass(frozen=True)
class LoadResult:
    request_id: str
    category: ResultCategory
    elapsed_seconds: float
    status_code: int | None
    business_code: int | None
    evidence: Mapping[str, Any]


def classify_response(status_code: int, body: Mapping[str, Any] | None) -> ResultCategory:
    business_code = body.get("code") if body else None
    if status_code >= 500 or business_code == 50000:
        if business_code == 50301 or (status_code == 503 and business_code is None):
            return ResultCategory.OVERLOAD
        return ResultCategory.UNDEFINED_5XX
    if status_code == 429 or business_code == 50301:
        return ResultCategory.OVERLOAD
    if 200 <= status_code < 300 and business_code in {None, 0}:
        return ResultCategory.SUCCESS
    return ResultCategory.BUSINESS_REJECTED


_SENSITIVE_KEYS = {
    "image",
    "imagelist",
    "photo",
    "photos",
    "storagepath",
    "text",
    "value",
    "embedding",
    "password",
    "token",
    "authorization",
    "cookie",
    "secret",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "audio",
    "pcm",
}


def _redacted_summary(value: Any) -> str:
    if isinstance(value, str):
        size = len(value.encode())
    elif isinstance(value, (bytes, bytearray)):
        size = len(value)
    else:
        size = len(json.dumps(value, ensure_ascii=False, default=str).encode())
    return f"<已脱敏:{size}字节>"


def redact_for_evidence(value: Any, *, key: str | None = None) -> Any:
    if key is not None and key.lower() in _SENSITIVE_KEYS:
        return _redacted_summary(value)
    if isinstance(value, Mapping):
        return {
            str(item_key): redact_for_evidence(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_for_evidence(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return _redacted_summary(value)


def evidence_contains_sensitive_material(value: Any, *, key: str | None = None) -> bool:
    if key is not None and key.lower() in _SENSITIVE_KEYS:
        return not (isinstance(value, str) and value.startswith("<已脱敏:"))
    if isinstance(value, Mapping):
        return any(
            evidence_contains_sensitive_material(item, key=str(item_key))
            for item_key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(evidence_contains_sensitive_material(item) for item in value)
    if isinstance(value, (bytes, bytearray)):
        return True
    if isinstance(value, str):
        lowered = value.lower()
        if lowered.startswith("data:") and ";base64," in lowered:
            return True
        if len(value) >= 256 and re.fullmatch(r"[A-Za-z0-9+/=\r\n]+", value):
            return True
    return False


@dataclass(frozen=True)
class HttpClientPool:
    max_connections: int
    max_keepalive_connections: int
    connect_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 60.0
    write_timeout_seconds: float = 60.0
    pool_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.max_connections <= 0:
            raise ValueError("max_connections 必须为正数")
        if not 0 < self.max_keepalive_connections <= self.max_connections:
            raise ValueError("max_keepalive_connections 必须在连接池上限内")
        if min(
            self.connect_timeout_seconds,
            self.read_timeout_seconds,
            self.write_timeout_seconds,
            self.pool_timeout_seconds,
        ) <= 0:
            raise ValueError("HTTP 客户端超时必须是有界正数")

    def build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            limits=httpx.Limits(
                max_connections=self.max_connections,
                max_keepalive_connections=self.max_keepalive_connections,
            ),
            timeout=httpx.Timeout(
                connect=self.connect_timeout_seconds,
                read=self.read_timeout_seconds,
                write=self.write_timeout_seconds,
                pool=self.pool_timeout_seconds,
            ),
        )


class _RateGate:
    def __init__(self, requests_per_second: float | None) -> None:
        if requests_per_second is not None and requests_per_second <= 0:
            raise ValueError("requests_per_second 必须为正数")
        self._interval = 0.0 if requests_per_second is None else 1 / requests_per_second
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        if self._interval == 0:
            return
        async with self._lock:
            now = time.monotonic()
            if self._next > now:
                await asyncio.sleep(self._next - now)
                now = time.monotonic()
            self._next = max(self._next, now) + self._interval


class AsyncLoadRunner:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        max_concurrency: int,
        request_timeout_seconds: float = 30.0,
    ) -> None:
        if max_concurrency <= 0:
            raise ValueError("max_concurrency 必须为正整数")
        if request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds 必须为正数")
        self._client = client
        self._max_concurrency = max_concurrency
        self._request_timeout_seconds = request_timeout_seconds

    async def run(
        self,
        requests: Iterable[HttpRequestSpec],
        *,
        requests_per_second: float | None = None,
        duration_seconds: float | None = None,
        abort_event: asyncio.Event | None = None,
    ) -> list[LoadResult]:
        if duration_seconds is not None and duration_seconds <= 0:
            raise ValueError("duration_seconds 必须为正数")
        queue: asyncio.Queue[HttpRequestSpec | None] = asyncio.Queue(
            maxsize=max(1, self._max_concurrency * 2)
        )
        results: list[LoadResult] = []
        gate = _RateGate(requests_per_second)
        deadline = (
            None if duration_seconds is None else time.monotonic() + duration_seconds
        )
        stopped_early = False

        async def produce() -> None:
            nonlocal stopped_early
            for item in requests:
                if abort_event is not None and abort_event.is_set():
                    stopped_early = True
                    break
                if deadline is not None and time.monotonic() >= deadline:
                    stopped_early = True
                    break
                await queue.put(item)
            for _ in range(self._max_concurrency):
                await queue.put(None)

        async def consume() -> None:
            while True:
                item = await queue.get()
                try:
                    if item is None:
                        return
                    if abort_event is not None and abort_event.is_set():
                        continue
                    await gate.wait()
                    if abort_event is not None and abort_event.is_set():
                        continue
                    results.append(await self._execute(item))
                finally:
                    queue.task_done()

        producer = asyncio.create_task(produce(), name="extreme-load-producer")
        workers = [
            asyncio.create_task(consume(), name=f"extreme-load-worker-{index}")
            for index in range(self._max_concurrency)
        ]
        try:
            await producer
            await queue.join()
            await asyncio.gather(*workers)
        except BaseException:
            producer.cancel()
            for worker in workers:
                worker.cancel()
            await asyncio.gather(producer, *workers, return_exceptions=True)
            raise
        if abort_event is not None and abort_event.is_set() and stopped_early:
            results.append(
                LoadResult(
                    request_id="guardrail-abort",
                    category=ResultCategory.GUARDRAIL_ABORT,
                    elapsed_seconds=0,
                    status_code=None,
                    business_code=None,
                    evidence={"reason": "资源护栏已停止新负载"},
                )
            )
        return results

    async def _execute(self, request: HttpRequestSpec) -> LoadResult:
        started = time.perf_counter()
        status_code: int | None = None
        business_code: int | None = None
        evidence: JsonObject = {"method": request.method.upper(), "url": request.url}
        try:
            response = await asyncio.wait_for(
                self._client.request(
                    request.method,
                    request.url,
                    json=request.json_body,
                    headers=request.headers,
                ),
                timeout=self._request_timeout_seconds,
            )
            status_code = response.status_code
            # 查询大 ASR 结果时只记录响应字节数，不把完整文本写入普通证据。
            evidence["response_size_bytes"] = len(response.content)
            try:
                parsed = response.json()
            except ValueError:
                parsed = None
            body = parsed if isinstance(parsed, dict) else None
            raw_code = body.get("code") if body else None
            business_code = raw_code if isinstance(raw_code, int) else None
            category = classify_response(status_code, body)
            if body is not None:
                evidence["response"] = redact_for_evidence(body)
        except (TimeoutError, httpx.TimeoutException):
            category = ResultCategory.TIMEOUT
        except httpx.TransportError:
            category = ResultCategory.CONNECTION_FAILURE
        except Exception as exc:  # 负载机自身异常必须与被测平台失败分开
            category = ResultCategory.LOAD_GENERATOR_FAILURE
            evidence["error_type"] = type(exc).__name__
        return LoadResult(
            request_id=request.request_id,
            category=category,
            elapsed_seconds=time.perf_counter() - started,
            status_code=status_code,
            business_code=business_code,
            evidence=evidence,
        )


@dataclass(frozen=True)
class WorkerShard:
    index: int
    total: int

    def __post_init__(self) -> None:
        if self.total <= 0 or self.index < 0 or self.index >= self.total:
            raise ValueError("worker 分片范围不合法")

    def select(self, items: Sequence[str]) -> tuple[str, ...]:
        return tuple(
            item
            for position, item in enumerate(items)
            if position % self.total == self.index
        )


class WorkerReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    campaign_id: str = Field(min_length=1)
    worker_id: str = Field(min_length=1)
    request_ids: tuple[str, ...]
    clock_offset_ms: float

    @field_validator("request_ids", mode="before")
    @classmethod
    def freeze_request_ids(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


@dataclass(frozen=True)
class ValidationVerdict:
    passed: bool
    reasons: tuple[str, ...] = ()


def validate_worker_reports(
    reports: Sequence[WorkerReport],
    expected_workers: int,
    max_clock_drift_ms: float,
) -> ValidationVerdict:
    if expected_workers <= 0 or max_clock_drift_ms < 0:
        raise ValueError("worker 数量必须为正数且时钟偏差上限不能为负")
    reasons: list[str] = []
    if (
        len(reports) != expected_workers
        or len({item.worker_id for item in reports}) != expected_workers
    ):
        reasons.append("worker 缺失或重复")
    if len({item.campaign_id for item in reports}) != 1:
        reasons.append("worker 不属于同一 Campaign ID")
    all_ids = [request_id for report in reports for request_id in report.request_ids]
    if len(all_ids) != len(set(all_ids)):
        reasons.append("请求 ID 跨分片重复")
    if any(abs(report.clock_offset_ms) > max_clock_drift_ms for report in reports):
        reasons.append("负载机时钟漂移超限")
    return ValidationVerdict(passed=not reasons, reasons=tuple(reasons))


class LoadHostSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cpu_percent: float = Field(ge=0, le=100)
    memory_percent: float = Field(ge=0, le=100)
    open_sockets: int = Field(ge=0)
    file_descriptor_soft_limit: int = Field(gt=0)
    network_utilization_percent: float = Field(ge=0, le=100)


@dataclass(frozen=True)
class LoadHostVerdict:
    ready: bool
    classification: str
    reasons: tuple[str, ...]


def assess_load_host(snapshot: LoadHostSnapshot) -> LoadHostVerdict:
    reasons: list[str] = []
    if snapshot.cpu_percent >= 90:
        reasons.append("CPU 使用率过高")
    if snapshot.memory_percent >= 90:
        reasons.append("内存使用率过高")
    if snapshot.open_sockets >= snapshot.file_descriptor_soft_limit * 0.9:
        reasons.append("socket/文件句柄接近上限")
    if snapshot.network_utilization_percent >= 90:
        reasons.append("负载机网络接近上限")
    return LoadHostVerdict(
        ready=not reasons,
        classification="ready" if not reasons else "load_generator_limit",
        reasons=tuple(reasons),
    )
