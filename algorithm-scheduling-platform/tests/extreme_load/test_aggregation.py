from __future__ import annotations

import pytest

from scripts.extreme_load.aggregation import (
    CampaignClassification,
    LoadBand,
    PerformanceSample,
    aggregate_evaluations,
    evaluate_case,
    percentile,
)


def _sample(**overrides: object) -> PerformanceSample:
    values: dict[str, object] = {
        "total_requests": 1_000,
        "successful_requests": 1_000,
        "capacity_rejected": 0,
        "business_rejected": 0,
        "timeouts": 0,
        "connection_failures": 0,
        "unexpected_5xx": 0,
        "undefined_errors": 0,
        "latency_seconds": tuple([1.0] * 1_000),
        "duration_seconds": 10.0,
        "queue_wait_p95_seconds": 0.1,
        "max_kafka_lag": 0,
        "peak_inflight": 10,
        "peak_active_leases": 10,
        "container_restarts": 0,
    }
    values.update(overrides)
    return PerformanceSample(**values)  # type: ignore[arg-type]


def test_percentile_uses_nearest_rank() -> None:
    assert percentile((1.0, 2.0, 3.0, 4.0), 0.95) == 4.0
    with pytest.raises(ValueError, match="不能为空"):
        percentile((), 0.95)


def test_stable_capacity_accepts_relative_threshold_boundaries() -> None:
    baseline = _sample(latency_seconds=tuple([1.0] * 1_000))
    stable = _sample(
        successful_requests=999,
        connection_failures=1,
        latency_seconds=tuple([3.0] * 950 + [5.0] * 50),
    )

    evaluation = evaluate_case(
        case_id="XL-IMG-030",
        load_band=LoadBand.STABLE,
        sample=stable,
        baseline=baseline,
    )

    assert evaluation.classification is CampaignClassification.STABLE_CAPACITY
    assert evaluation.passed


@pytest.mark.parametrize(
    "sample",
    (
        _sample(successful_requests=998, connection_failures=2),
        _sample(latency_seconds=tuple([3.1] * 1_000)),
        _sample(latency_seconds=tuple([1.0] * 950 + [5.1] * 50)),
        _sample(container_restarts=1),
    ),
)
def test_stable_capacity_rejects_engineering_gate_violations(
    sample: PerformanceSample,
) -> None:
    evaluation = evaluate_case(
        case_id="XL-STABLE",
        load_band=LoadBand.STABLE,
        sample=sample,
        baseline=_sample(),
    )

    assert evaluation.classification is CampaignClassification.NONCONFORMING
    assert not evaluation.passed
    assert evaluation.reasons


def test_expected_overload_allows_defined_capacity_rejection() -> None:
    evaluation = evaluate_case(
        case_id="XL-OVERLOAD",
        load_band=LoadBand.OVERLOAD,
        sample=_sample(
            successful_requests=700,
            capacity_rejected=300,
            latency_seconds=tuple([1.0] * 1_000),
        ),
        baseline=_sample(),
        recovered=True,
        queue_drained=True,
    )

    assert evaluation.classification is CampaignClassification.EXPECTED_OVERLOAD
    assert evaluation.passed


def test_stable_and_overload_require_a_baseline() -> None:
    for load_band in (LoadBand.STABLE, LoadBand.OVERLOAD):
        with pytest.raises(ValueError, match="必须提供基线"):
            evaluate_case(
                case_id="XL-NO-BASELINE",
                load_band=load_band,
                sample=_sample(),
            )


def test_overload_does_not_hide_timeout_or_undefined_error() -> None:
    evaluation = evaluate_case(
        case_id="XL-BAD-OVERLOAD",
        load_band=LoadBand.OVERLOAD,
        sample=_sample(
            successful_requests=698,
            capacity_rejected=300,
            timeouts=1,
            undefined_errors=1,
        ),
        baseline=_sample(),
    )

    assert evaluation.classification is CampaignClassification.NONCONFORMING
    assert not evaluation.passed


@pytest.mark.parametrize("load_band", (LoadBand.BASELINE, LoadBand.STABLE))
def test_baseline_and_stable_capacity_do_not_accept_capacity_rejection(
    load_band: LoadBand,
) -> None:
    evaluation = evaluate_case(
        case_id="XL-NOT-STABLE",
        load_band=load_band,
        sample=_sample(successful_requests=999, capacity_rejected=1),
        baseline=None if load_band is LoadBand.BASELINE else _sample(),
    )

    assert evaluation.classification is CampaignClassification.NONCONFORMING
    assert not evaluation.passed


def test_guardrail_and_load_generator_limits_are_not_blame_shifted() -> None:
    guardrail = evaluate_case(
        case_id="XL-GUARD",
        load_band=LoadBand.STABLE,
        sample=_sample(),
        baseline=_sample(),
        guardrail_reason="磁盘达到红线",
    )
    generator = evaluate_case(
        case_id="XL-CLIENT",
        load_band=LoadBand.STABLE,
        sample=_sample(),
        baseline=_sample(),
        load_generator_reason="负载机文件句柄耗尽",
    )

    assert guardrail.classification is CampaignClassification.GUARDRAIL_STOP
    assert generator.classification is CampaignClassification.LOAD_GENERATOR_LIMIT
    assert not guardrail.passed and not generator.passed

    with pytest.raises(ValueError, match="不能同时声明"):
        evaluate_case(
            case_id="XL-AMBIGUOUS",
            load_band=LoadBand.STABLE,
            sample=_sample(),
            baseline=_sample(),
            guardrail_reason="磁盘达到红线",
            load_generator_reason="负载机文件句柄耗尽",
        )


def test_public_summary_separates_success_rejection_and_errors() -> None:
    sample = _sample(
        successful_requests=995,
        capacity_rejected=2,
        business_rejected=1,
        timeouts=1,
        connection_failures=1,
    )

    public = sample.public_summary()

    assert public["success_rate"] == 0.995
    assert public["rejected_requests"] == 3
    assert public["error_requests"] == 2


def test_aggregate_fails_closed_on_missing_or_duplicate_cases() -> None:
    evaluation = evaluate_case(
        case_id="XL-001",
        load_band=LoadBand.BASELINE,
        sample=_sample(),
    )

    missing = aggregate_evaluations(("XL-001", "XL-002"), (evaluation,))
    duplicate = aggregate_evaluations(("XL-001",), (evaluation, evaluation))

    assert missing.overall_status == "未完成"
    assert missing.missing_case_ids == ("XL-002",)
    assert duplicate.overall_status == "不符合"
    assert duplicate.duplicate_case_ids == ("XL-001",)
