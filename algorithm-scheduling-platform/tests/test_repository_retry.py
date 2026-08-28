from __future__ import annotations

from collections.abc import Callable

import pytest
from sqlalchemy.exc import DBAPIError

from packages.platform_common.repository import (
    CourseRepository,
    PostgresRetryPolicy,
    TransientInfrastructureError,
)


class SqlStateError(Exception):
    def __init__(self, sqlstate: str) -> None:
        self.sqlstate = sqlstate
        super().__init__(sqlstate)


class BeginContext:
    def __init__(self, enter: Callable[[], object]) -> None:
        self._enter = enter

    def __enter__(self) -> object:
        return self._enter()

    def __exit__(self, *args: object) -> None:
        return None


class ScriptedEngine:
    def __init__(self, sqlstates: list[str]) -> None:
        self.sqlstates = list(sqlstates)
        self.attempts = 0

    def begin(self) -> BeginContext:
        def enter() -> object:
            self.attempts += 1
            if self.sqlstates:
                original = SqlStateError(self.sqlstates.pop(0))
                raise DBAPIError(None, None, original, False)
            return object()

        return BeginContext(enter)


def _repository(engine: ScriptedEngine, *, max_attempts: int = 3) -> CourseRepository:
    return CourseRepository(
        engine,  # type: ignore[arg-type]
        postgres_retry=PostgresRetryPolicy(
            max_attempts=max_attempts,
            base_delay_seconds=0,
            max_delay_seconds=0,
            jitter_ratio=0,
        ),
    )


@pytest.mark.parametrize("sqlstate", ("40P01", "40001"))
def test_retryable_transaction_uses_new_transaction_then_recovers(
    sqlstate: str,
) -> None:
    engine = ScriptedEngine([sqlstate])
    repository = _repository(engine)

    result = repository._run_retryable_transaction("test-operation", lambda _: "ok")

    assert result == "ok"
    assert engine.attempts == 2


def test_retryable_transaction_reports_typed_error_when_attempts_exhausted() -> None:
    engine = ScriptedEngine(["40P01", "40P01", "40P01"])
    repository = _repository(engine)

    with pytest.raises(TransientInfrastructureError) as exc_info:
        repository._run_retryable_transaction("claim_ready_node", lambda _: None)

    assert exc_info.value.operation == "claim_ready_node"
    assert exc_info.value.sqlstate == "40P01"
    assert exc_info.value.attempts == 3
    assert engine.attempts == 3


@pytest.mark.parametrize("sqlstate", ("28P01", "42P01", "23514", "08006"))
def test_non_retryable_sqlstate_is_not_hidden(sqlstate: str) -> None:
    engine = ScriptedEngine([sqlstate])
    repository = _repository(engine)

    with pytest.raises(DBAPIError):
        repository._run_retryable_transaction("invalid-operation", lambda _: None)

    assert engine.attempts == 1


def test_retry_observer_records_retry_recovery_and_exhaustion() -> None:
    events: list[dict[str, str]] = []
    recovered_engine = ScriptedEngine(["40P01"])
    recovered = CourseRepository(
        recovered_engine,  # type: ignore[arg-type]
        postgres_retry=PostgresRetryPolicy(
            max_attempts=2,
            base_delay_seconds=0,
            max_delay_seconds=0,
            jitter_ratio=0,
        ),
        postgres_retry_observer=lambda **event: events.append(event),
    )

    assert recovered._run_retryable_transaction("claim", lambda _: "ok") == "ok"
    assert events == [
        {"operation": "claim", "sqlstate": "40P01", "outcome": "retry"},
        {"operation": "claim", "sqlstate": "40P01", "outcome": "recovered"},
    ]

    events.clear()
    exhausted_engine = ScriptedEngine(["40001", "40001"])
    exhausted = CourseRepository(
        exhausted_engine,  # type: ignore[arg-type]
        postgres_retry=PostgresRetryPolicy(
            max_attempts=2,
            base_delay_seconds=0,
            max_delay_seconds=0,
            jitter_ratio=0,
        ),
        postgres_retry_observer=lambda **event: events.append(event),
    )

    with pytest.raises(TransientInfrastructureError):
        exhausted._run_retryable_transaction("aggregate", lambda _: None)
    assert events[-1] == {
        "operation": "aggregate",
        "sqlstate": "40001",
        "outcome": "exhausted",
    }
