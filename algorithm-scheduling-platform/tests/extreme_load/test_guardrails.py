from __future__ import annotations

from scripts.extreme_load.guardrails import (
    GiB,
    GuardrailController,
    GuardrailLevel,
    GuardrailObservation,
    GuardrailPolicy,
    GuardrailState,
    StorageObservation,
    evaluate_guardrails,
)


def _storage(*, free_gib: int, total_gib: int = 1_000) -> StorageObservation:
    return StorageObservation(
        name="host",
        total_bytes=total_gib * GiB,
        free_bytes=free_gib * GiB,
    )


def test_disk_warning_and_stop_use_absolute_or_percentage_threshold() -> None:
    policy = GuardrailPolicy()

    warning = evaluate_guardrails(
        GuardrailObservation(storage=(_storage(free_gib=149),)),
        policy,
    )
    critical = evaluate_guardrails(
        GuardrailObservation(storage=(_storage(free_gib=99),)),
        policy,
    )
    percentage_warning = evaluate_guardrails(
        GuardrailObservation(storage=(_storage(free_gib=200, total_gib=2_000),)),
        policy,
    )
    percentage_critical = evaluate_guardrails(
        GuardrailObservation(storage=(_storage(free_gib=190, total_gib=2_000),)),
        policy,
    )

    assert warning.level is GuardrailLevel.WARNING
    assert critical.level is GuardrailLevel.STOP
    assert percentage_warning.level is GuardrailLevel.WARNING
    assert percentage_critical.level is GuardrailLevel.STOP
    assert not warning.may_start_next_step
    assert not critical.may_generate_load


def test_threshold_equality_does_not_trigger_below_contract() -> None:
    assessment = evaluate_guardrails(
        GuardrailObservation(storage=(_storage(free_gib=150),)),
        GuardrailPolicy(),
    )

    assert assessment.level is GuardrailLevel.CLEAR


def test_serious_runtime_signals_stop_new_load() -> None:
    cases = (
        GuardrailObservation(gpu_critical_errors=("GPU0:Xid-79",)),
        GuardrailObservation(host_oom=True),
        GuardrailObservation(restart_loop_containers=("ocr-gpu0",)),
        GuardrailObservation(database_health={"postgresql": False}),
        GuardrailObservation(evidence_writable=False),
        GuardrailObservation(maintenance_lock_owned=False),
    )

    for observation in cases:
        assessment = evaluate_guardrails(observation, GuardrailPolicy())
        assert assessment.level is GuardrailLevel.STOP
        assert assessment.reasons


def test_guardrail_state_machine_preserves_then_recovers_and_drains() -> None:
    controller = GuardrailController()
    controller.apply(
        evaluate_guardrails(
            GuardrailObservation(host_oom=True),
            GuardrailPolicy(),
        )
    )
    assert controller.state is GuardrailState.STOP_NEW_LOAD
    assert not controller.may_generate_load

    controller.evidence_preserved()
    controller.recovery_started()
    controller.recovery_completed()
    controller.drain_completed()

    assert controller.history[-1].current is GuardrailState.RECOVERED
    assert [transition.current for transition in controller.history] == [
        GuardrailState.STOP_NEW_LOAD,
        GuardrailState.PRESERVING_EVIDENCE,
        GuardrailState.RECOVERING,
        GuardrailState.DRAINING,
        GuardrailState.RECOVERED,
    ]


def test_recovery_failure_is_terminal() -> None:
    controller = GuardrailController()
    controller.apply(
        evaluate_guardrails(
            GuardrailObservation(database_health={"redis": False}),
            GuardrailPolicy(),
        )
    )
    controller.evidence_preserved()
    controller.recovery_started()
    controller.recovery_failed("Redis 未恢复")

    assert controller.state is GuardrailState.FAILED
    assert not controller.may_generate_load
    assert controller.history[-1].reason == "Redis 未恢复"


def test_evidence_preservation_failure_is_terminal() -> None:
    controller = GuardrailController()
    controller.apply(
        evaluate_guardrails(
            GuardrailObservation(evidence_writable=False),
            GuardrailPolicy(),
        )
    )

    controller.evidence_preservation_failed("证据目录失去原子写能力")

    assert controller.state is GuardrailState.FAILED
    assert not controller.may_generate_load
