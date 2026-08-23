from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class LoadBand(StrEnum):
    BASELINE = "baseline"
    STABLE = "stable"
    OVERLOAD = "overload"


class CampaignClassification(StrEnum):
    BASELINE = "基线"
    STABLE_CAPACITY = "稳定容量"
    EXPECTED_OVERLOAD = "预期过载"
    LOAD_GENERATOR_LIMIT = "负载机限制"
    GUARDRAIL_STOP = "护栏中止"
    NONCONFORMING = "真实不符合"
    NOT_EXECUTED = "未执行"


def percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        raise ValueError("延迟样本不能为空")
    if not math.isfinite(quantile) or not 0 < quantile <= 1:
        raise ValueError("分位点必须位于 0 与 1 之间")
    ordered = sorted(values)
    if any(not math.isfinite(value) or value < 0 for value in ordered):
        raise ValueError("延迟样本必须是有限非负数")
    index = max(0, math.ceil(len(ordered) * quantile) - 1)
    return ordered[index]


@dataclass(frozen=True, slots=True)
class PerformanceSample:
    total_requests: int
    successful_requests: int
    capacity_rejected: int
    business_rejected: int
    timeouts: int
    connection_failures: int
    unexpected_5xx: int
    undefined_errors: int
    latency_seconds: tuple[float, ...]
    duration_seconds: float
    queue_wait_p95_seconds: float
    max_kafka_lag: int
    peak_inflight: int
    peak_active_leases: int
    container_restarts: int

    def __post_init__(self) -> None:
        integer_fields = (
            "total_requests",
            "successful_requests",
            "capacity_rejected",
            "business_rejected",
            "timeouts",
            "connection_failures",
            "unexpected_5xx",
            "undefined_errors",
            "max_kafka_lag",
            "peak_inflight",
            "peak_active_leases",
            "container_restarts",
        )
        for name in integer_fields:
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} 必须是非负整数")
        if self.total_requests <= 0:
            raise ValueError("total_requests 必须是正整数")
        accounted = sum(
            (
                self.successful_requests,
                self.capacity_rejected,
                self.business_rejected,
                self.timeouts,
                self.connection_failures,
                self.unexpected_5xx,
                self.undefined_errors,
            )
        )
        if accounted != self.total_requests:
            raise ValueError("请求分类数量之和必须等于 total_requests")
        if len(self.latency_seconds) != self.total_requests:
            raise ValueError("每个请求必须有一条延迟样本")
        if any(not math.isfinite(value) or value < 0 for value in self.latency_seconds):
            raise ValueError("latency_seconds 必须是有限非负数")
        if not math.isfinite(self.duration_seconds) or self.duration_seconds <= 0:
            raise ValueError("duration_seconds 必须是有限正数")
        if not math.isfinite(self.queue_wait_p95_seconds) or self.queue_wait_p95_seconds < 0:
            raise ValueError("queue_wait_p95_seconds 必须是有限非负数")

    @property
    def p50_seconds(self) -> float:
        return percentile(self.latency_seconds, 0.50)

    @property
    def p95_seconds(self) -> float:
        return percentile(self.latency_seconds, 0.95)

    @property
    def p99_seconds(self) -> float:
        return percentile(self.latency_seconds, 0.99)

    @property
    def throughput_rps(self) -> float:
        return self.total_requests / self.duration_seconds

    @property
    def unexpected_failure_rate(self) -> float:
        return (self.unexpected_5xx + self.connection_failures) / self.total_requests

    def public_summary(self) -> dict[str, Any]:
        document = asdict(self)
        document.pop("latency_seconds")
        rejected_requests = self.capacity_rejected + self.business_rejected
        error_requests = (
            self.timeouts + self.connection_failures + self.unexpected_5xx + self.undefined_errors
        )
        document.update(
            {
                "p50_seconds": self.p50_seconds,
                "p95_seconds": self.p95_seconds,
                "p99_seconds": self.p99_seconds,
                "throughput_rps": self.throughput_rps,
                "success_rate": self.successful_requests / self.total_requests,
                "rejected_requests": rejected_requests,
                "error_requests": error_requests,
                "unexpected_failure_rate": self.unexpected_failure_rate,
            }
        )
        return document


@dataclass(frozen=True, slots=True)
class CaseEvaluation:
    case_id: str
    load_band: LoadBand
    classification: CampaignClassification
    passed: bool
    reasons: tuple[str, ...]
    sample: PerformanceSample

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "load_band": self.load_band.value,
            "classification": self.classification.value,
            "passed": self.passed,
            "reasons": list(self.reasons),
            "performance": self.sample.public_summary(),
        }


def _base_failures(
    sample: PerformanceSample,
    *,
    include_unexpected_transport: bool,
) -> list[str]:
    reasons: list[str] = []
    if include_unexpected_transport and sample.unexpected_5xx:
        reasons.append(f"出现 {sample.unexpected_5xx} 个非预期 HTTP 5xx")
    if include_unexpected_transport and sample.connection_failures:
        reasons.append(f"出现 {sample.connection_failures} 个连接失败")
    if sample.timeouts:
        reasons.append(f"出现 {sample.timeouts} 个超时")
    if sample.undefined_errors:
        reasons.append(f"出现 {sample.undefined_errors} 个未定义错误")
    if sample.container_restarts:
        reasons.append(f"出现 {sample.container_restarts} 次未预期容器重启")
    return reasons


def evaluate_case(
    *,
    case_id: str,
    load_band: LoadBand,
    sample: PerformanceSample,
    baseline: PerformanceSample | None = None,
    invariant_failures: Sequence[str] = (),
    recovered: bool = True,
    queue_drained: bool = True,
    guardrail_reason: str | None = None,
    load_generator_reason: str | None = None,
    executed: bool = True,
) -> CaseEvaluation:
    if not case_id:
        raise ValueError("case_id 不能为空")
    if guardrail_reason and load_generator_reason:
        raise ValueError("护栏中止与负载机限制不能同时声明")
    if not executed:
        return CaseEvaluation(
            case_id,
            load_band,
            CampaignClassification.NOT_EXECUTED,
            False,
            ("用例未执行",),
            sample,
        )
    if guardrail_reason:
        return CaseEvaluation(
            case_id,
            load_band,
            CampaignClassification.GUARDRAIL_STOP,
            False,
            (guardrail_reason,),
            sample,
        )
    if load_generator_reason:
        return CaseEvaluation(
            case_id,
            load_band,
            CampaignClassification.LOAD_GENERATOR_LIMIT,
            False,
            (load_generator_reason,),
            sample,
        )
    if load_band is not LoadBand.BASELINE and baseline is None:
        raise ValueError("稳定容量和预期过载评估必须提供基线")

    failures = _base_failures(
        sample,
        include_unexpected_transport=load_band is not LoadBand.STABLE,
    )
    if load_band is not LoadBand.OVERLOAD and sample.capacity_rejected:
        failures.append(f"基线/稳定容量内出现 {sample.capacity_rejected} 个容量拒绝")
    if sample.business_rejected:
        failures.append(f"性能用例出现 {sample.business_rejected} 个业务拒绝")
    failures.extend(reason for reason in invariant_failures if reason)
    if not recovered:
        failures.append("停止加压后系统未恢复就绪")
    if not queue_drained:
        failures.append("停止加压后队列未排空")

    if load_band is LoadBand.BASELINE:
        classification = (
            CampaignClassification.NONCONFORMING if failures else CampaignClassification.BASELINE
        )
    elif load_band is LoadBand.STABLE:
        assert baseline is not None
        if sample.unexpected_failure_rate > 0.001:
            failures.append(
                f"稳定容量内非预期 5xx/连接失败率超过 0.1%: {sample.unexpected_failure_rate:.4%}"
            )
        if sample.p95_seconds > baseline.p95_seconds * 3:
            failures.append("P95 超过基线 3 倍")
        if sample.p99_seconds > baseline.p99_seconds * 5:
            failures.append("P99 超过基线 5 倍")
        classification = (
            CampaignClassification.NONCONFORMING
            if failures
            else CampaignClassification.STABLE_CAPACITY
        )
    else:
        classification = (
            CampaignClassification.NONCONFORMING
            if failures
            else CampaignClassification.EXPECTED_OVERLOAD
        )

    return CaseEvaluation(
        case_id=case_id,
        load_band=load_band,
        classification=classification,
        passed=classification
        in {
            CampaignClassification.BASELINE,
            CampaignClassification.STABLE_CAPACITY,
            CampaignClassification.EXPECTED_OVERLOAD,
        },
        reasons=tuple(dict.fromkeys(failures)),
        sample=sample,
    )


@dataclass(frozen=True, slots=True)
class CampaignAggregate:
    overall_status: str
    required_case_ids: tuple[str, ...]
    evaluations: tuple[CaseEvaluation, ...]
    missing_case_ids: tuple[str, ...]
    duplicate_case_ids: tuple[str, ...]
    classification_counts: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_status": self.overall_status,
            "required_case_ids": list(self.required_case_ids),
            "missing_case_ids": list(self.missing_case_ids),
            "duplicate_case_ids": list(self.duplicate_case_ids),
            "classification_counts": dict(self.classification_counts),
            "cases": [evaluation.to_dict() for evaluation in self.evaluations],
        }


def aggregate_evaluations(
    required_case_ids: Sequence[str],
    evaluations: Sequence[CaseEvaluation],
) -> CampaignAggregate:
    required = tuple(required_case_ids)
    if not required or any(not case_id for case_id in required):
        raise ValueError("必需用例 ID 不能为空")
    if len(required) != len(set(required)):
        raise ValueError("必需用例 ID 不能重复")
    counts = Counter(item.case_id for item in evaluations)
    duplicates = tuple(sorted(case_id for case_id, count in counts.items() if count > 1))
    missing = tuple(case_id for case_id in required if counts[case_id] == 0)
    unexpected = tuple(sorted(set(counts) - set(required)))
    classifications = Counter(item.classification.value for item in evaluations)

    if duplicates or unexpected:
        overall = "不符合"
    elif missing:
        overall = "未完成"
    elif any(not item.passed for item in evaluations):
        overall = "不符合"
    else:
        overall = "符合"
    if unexpected:
        duplicates = (*duplicates, *(f"未声明:{case_id}" for case_id in unexpected))

    return CampaignAggregate(
        overall_status=overall,
        required_case_ids=required,
        evaluations=tuple(evaluations),
        missing_case_ids=missing,
        duplicate_case_ids=duplicates,
        classification_counts=dict(sorted(classifications.items())),
    )
