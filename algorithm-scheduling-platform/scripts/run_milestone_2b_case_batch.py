#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import json
import os
import re
import select
import signal
import socket
import stat
import sys
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
if str(PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATFORM_ROOT))

from scripts.aggregate_milestone_2b_cases import (  # noqa: E402
    _load_release_json_with_metadata,
    _real_directory_entries,
    _release_root_lock,
    _require_real_subdirectory,
    collect_case_executions,
    publish_json_once,
)
from scripts.milestone_2b_case_catalog import (  # noqa: E402
    CASE_PHASES,
    CASE_SAFETY_LEVELS,
    CaseDefinition,
    load_case_catalog,
)
from scripts.milestone_2b_case_runners.base import (  # noqa: E402
    CaseContext,
    CaseOutcome,
    CaseRunner,
    CaseStatus,
)
from scripts.milestone_2b_case_runners.campaign import (  # noqa: E402
    CAMPAIGN_EVIDENCE_NAME,
    validate_campaign_source_evidence,
)
from scripts.milestone_2b_case_runners.evidence import (  # noqa: E402
    claim_case_once,
    publish_framework_failure_evidence,
    release_identity,
)
from scripts.milestone_2b_case_runners.process import (  # noqa: E402
    maximum_command_termination_budget_seconds,
)
from scripts.milestone_2b_case_runners.safety import (  # noqa: E402
    CommandTaskTerminationError,
    DelegatedMaintenanceLockGuard,
    MaintenanceLock,
    MaintenanceLockGuard,
    _case_execution_scope,
)
from scripts.milestone_2b_report_contract import (  # noqa: E402
    DECLARATION_CATEGORY_BY_CASE_ID,
    EXECUTION_RECORD_FIELDS,
    validate_raw_execution_evidence,
)

RUNNER_PATTERN = re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*")
RUNNER_PACKAGE = "scripts.milestone_2b_case_runners"
RunnerFunction = Callable[[CaseContext, CaseDefinition], Awaitable[CaseOutcome]]
CleanupFunction = Callable[[CaseContext, CaseDefinition], Awaitable[None]]
CASE_TASK_CANCEL_GRACE_SECONDS = 0.1
SUPERVISOR_RESULT_GRACE_SECONDS = 0.25
SUPERVISOR_SIGNAL_GRACE_SECONDS = 0.25
SUPERVISOR_FIXED_GRACE_SECONDS = 2.0
SUPERVISOR_MESSAGE_LIMIT = 65_536
SUPERVISOR_BLOCKED_SIGNALS = (
    signal.SIGINT,
    signal.SIGTERM,
    signal.SIGHUP,
)
SEALED_RELEASE_PATHS = (
    Path("negative/cases.json"),
    Path("load/cases.json"),
    Path("summary/cases.json"),
    Path("summary/report.json"),
    Path("summary/report.md"),
)


@dataclass(frozen=True, slots=True)
class BatchResult:
    outcomes: Mapping[str, CaseOutcome]
    completed: tuple[str, ...]
    skipped: tuple[str, ...]
    missing: tuple[str, ...]
    exit_code: int


@dataclass(frozen=True, slots=True)
class _FunctionRunner:
    function: RunnerFunction
    cleanup: CleanupFunction | None = None

    async def run(
        self, context: CaseContext, case: CaseDefinition
    ) -> CaseOutcome:
        return await self.function(context, case)


@dataclass(frozen=True, slots=True)
class _CaseExecution:
    case_id: str
    outcome: CaseOutcome | None
    terminal: bool


@dataclass(frozen=True, slots=True)
class _SupervisorResult:
    exit_code: int
    error: str | None


class _SupervisorSignal(BaseException):
    def __init__(self, signal_number: int) -> None:
        super().__init__(signal_number)
        self.signal_number = signal_number


class _SupervisorSignalState:
    def __init__(self) -> None:
        self._defer_depth = 0
        self._pending_signal_number: int | None = None

    def handle(self, signal_number: int) -> None:
        if self._defer_depth:
            if self._pending_signal_number is None:
                self._pending_signal_number = signal_number
            return
        raise self._exception_for(signal_number)

    @contextmanager
    def defer(self) -> Iterator[None]:
        self._defer_depth += 1
        try:
            yield
        finally:
            self._defer_depth -= 1

    def pop_pending(self) -> BaseException | None:
        signal_number = self._pending_signal_number
        self._pending_signal_number = None
        if signal_number is None:
            return None
        return self._exception_for(signal_number)

    @staticmethod
    def _exception_for(signal_number: int) -> BaseException:
        if signal_number == signal.SIGINT:
            return KeyboardInterrupt()
        return _SupervisorSignal(signal_number)


def resolve_runner(runner_name: str) -> CaseRunner:
    if RUNNER_PATTERN.fullmatch(runner_name) is None:
        raise ValueError(f"runner name is not a safe module.method: {runner_name}")
    module_name, method_name = runner_name.split(".", 1)
    qualified_module = f"{RUNNER_PACKAGE}.{module_name}"
    try:
        module = importlib.import_module(qualified_module)
    except ImportError as exc:
        raise ValueError(f"unknown runner module: {runner_name}") from exc
    try:
        candidate = getattr(module, method_name)
    except AttributeError as exc:
        raise ValueError(f"unknown runner method: {runner_name}") from exc
    run_method = getattr(candidate, "run", None)
    if callable(run_method):
        return cast(CaseRunner, candidate)
    if callable(candidate):
        cleanup = getattr(candidate, "cleanup", None)
        if cleanup is not None and not inspect.iscoroutinefunction(cleanup):
            raise ValueError(f"runner cleanup is not async: {runner_name}")
        return _FunctionRunner(
            cast(RunnerFunction, candidate),
            cast(CleanupFunction | None, cleanup),
        )
    raise ValueError(f"runner target is not callable: {runner_name}")


def select_cases(
    cases: Sequence[CaseDefinition],
    phase: str,
    case_id: str | None,
) -> tuple[CaseDefinition, ...]:
    if phase not in CASE_PHASES:
        raise ValueError(f"unknown phase: {phase}")
    selected = (
        tuple(cases)
        if phase == "final"
        else tuple(case for case in cases if case.phase == phase)
    )
    if case_id is None:
        if not selected:
            raise ValueError(f"phase has no cases: {phase}")
        return selected
    matching = tuple(case for case in cases if case.case_id == case_id)
    if not matching:
        raise ValueError(f"unknown case ID: {case_id}")
    if phase != "final" and matching[0].phase != phase:
        raise ValueError(f"case {case_id} does not belong to phase {phase}")
    return matching


async def run_case_batch(
    *,
    cases: Sequence[CaseDefinition],
    release_root: Path,
    runners: Mapping[str, CaseRunner] | None = None,
    concurrency: int,
    require_cleanup: bool = False,
    require_all_selected: bool = False,
    run_id: str,
    target: str,
    delegated_lock_holder_pid: int | None = None,
    delegated_lock_path: Path | None = None,
) -> BatchResult:
    if type(concurrency) is not int or concurrency <= 0:
        raise ValueError("concurrency must be a positive integer")
    with _open_release_for_case_execution(release_root):
        return await _run_case_batch_in_open_release(
            cases=cases,
            release_root=release_root,
            runners=runners,
            concurrency=concurrency,
            require_cleanup=require_cleanup,
            require_all_selected=require_all_selected,
            run_id=run_id,
            target=target,
            delegated_lock_holder_pid=delegated_lock_holder_pid,
            delegated_lock_path=delegated_lock_path,
        )


async def _run_case_batch_in_open_release(
    *,
    cases: Sequence[CaseDefinition],
    release_root: Path,
    runners: Mapping[str, CaseRunner] | None,
    concurrency: int,
    require_cleanup: bool,
    require_all_selected: bool,
    run_id: str,
    target: str,
    delegated_lock_holder_pid: int | None,
    delegated_lock_path: Path | None,
) -> BatchResult:
    release_tag, git_sha = release_identity(release_root)
    selected = _snapshot_selected_cases(cases)
    resolved_runners = _resolve_selected_runners(selected, runners)

    existing_records = collect_case_executions(
        release_root=release_root,
        release_tag=release_tag,
        git_sha=git_sha,
    )
    existing_by_id = {record["case_id"]: record for record in existing_records}
    outcomes: dict[str, CaseOutcome] = {}
    skipped: list[str] = []
    pending: list[CaseDefinition] = []
    partial: list[str] = []
    for case in selected:
        existing = existing_by_id.get(case.case_id)
        if existing is not None:
            outcomes[case.case_id] = CaseOutcome(
                status=cast(CaseStatus, existing["status"]),
                reason=existing["reason"],
                evidence=tuple(Path(path) for path in existing["evidence"]),
            )
            skipped.append(case.case_id)
            continue
        if _case_evidence_path_exists(release_root, case):
            partial.append(case.case_id)
            continue
        pending.append(case)

    semaphore = asyncio.Semaphore(concurrency)
    requires_lock = any(case.safety == "canonical_runtime" for case in pending)
    if (delegated_lock_holder_pid is None) != (delegated_lock_path is None):
        raise ValueError("delegated maintenance lock requires both holder PID and path")
    lock_context: AbstractContextManager[MaintenanceLock | None]
    if requires_lock and delegated_lock_holder_pid is not None:
        assert delegated_lock_path is not None
        lock_context = DelegatedMaintenanceLockGuard(
            release_root,
            delegated_lock_holder_pid,
            delegated_lock_path,
        )
    else:
        lock_context = (
            MaintenanceLockGuard(release_root) if requires_lock else nullcontext(None)
        )
    completed: list[str] = []
    missing = list(partial)
    with lock_context as maintenance_lock:
        tasks = [
            asyncio.create_task(
                _run_selected_case(
                    case=case,
                    runner=resolved_runners[case.runner],
                    release_root=release_root,
                    release_tag=release_tag,
                    git_sha=git_sha,
                    run_id=run_id,
                    target=target,
                    semaphore=semaphore,
                    require_cleanup=require_cleanup,
                    maintenance_lock=(
                        maintenance_lock
                        if case.safety == "canonical_runtime"
                        else None
                    ),
                )
            )
            for case in pending
        ]
        executions = await asyncio.gather(*tasks)
    for execution in executions:
        if execution.terminal and execution.outcome is not None:
            completed.append(execution.case_id)
            outcomes[execution.case_id] = execution.outcome
        else:
            missing.append(execution.case_id)

    ordered_completed = tuple(
        case.case_id for case in selected if case.case_id in set(completed)
    )
    ordered_skipped = tuple(
        case.case_id for case in selected if case.case_id in set(skipped)
    )
    ordered_missing = tuple(
        case.case_id for case in selected if case.case_id in set(missing)
    )
    has_failure = any(outcome.status == "失败" for outcome in outcomes.values())
    incomplete = bool(ordered_missing) and require_all_selected
    exit_code = 1 if has_failure or incomplete else 0
    return BatchResult(
        outcomes=outcomes,
        completed=ordered_completed,
        skipped=ordered_skipped,
        missing=ordered_missing,
        exit_code=exit_code,
    )


@contextmanager
def _open_release_for_case_execution(release_root: Path) -> Iterator[None]:
    with _release_root_lock(release_root, exclusive=False):
        root_entries = _real_directory_entries(release_root, Path())
        entries_by_parent: dict[Path, dict[str, os.stat_result]] = {}
        sealed: list[str] = []
        for relative_path in SEALED_RELEASE_PATHS:
            parent_path = relative_path.parent
            parent_entries = entries_by_parent.get(parent_path)
            if parent_entries is None:
                parent_metadata = root_entries.get(parent_path.name)
                if parent_metadata is None:
                    entries_by_parent[parent_path] = {}
                    continue
                _require_real_subdirectory(parent_metadata, parent_path)
                parent_entries = _real_directory_entries(release_root, parent_path)
                entries_by_parent[parent_path] = parent_entries
            if relative_path.name in parent_entries:
                sealed.append(relative_path.as_posix())
        if sealed:
            raise ValueError(
                f"release is sealed and read-only by report artifacts: {sealed}"
            )
        yield


def _snapshot_selected_cases(
    cases: Sequence[CaseDefinition],
) -> tuple[CaseDefinition, ...]:
    seen_ids: set[str] = set()
    snapshots: list[CaseDefinition] = []
    for index, case in enumerate(cases):
        if type(case) is not CaseDefinition:
            raise ValueError(f"selected case[{index}] must be CaseDefinition")
        case_id = case.case_id
        category = case.category
        phase = case.phase
        title = case.title
        expected = case.expected
        runner = case.runner
        safety = case.safety
        timeout_seconds = case.timeout_seconds
        string_fields = {
            "case_id": case_id,
            "category": category,
            "phase": phase,
            "title": title,
            "expected": expected,
            "runner": runner,
            "safety": safety,
        }
        for field_name, value in string_fields.items():
            if type(value) is not str or not value.strip():
                raise ValueError(
                    f"case {field_name} must be a non-empty plain string"
                )
        if case_id in seen_ids:
            raise ValueError(f"duplicate selected case ID: {case_id}")
        seen_ids.add(case_id)
        expected_category = DECLARATION_CATEGORY_BY_CASE_ID.get(case_id)
        if expected_category != category:
            raise ValueError(
                f"case {case_id} category does not match report authority"
            )
        if phase not in CASE_PHASES:
            raise ValueError(f"unknown case phase: {phase}")
        if safety not in CASE_SAFETY_LEVELS:
            raise ValueError(f"unknown case safety: {safety}")
        if RUNNER_PATTERN.fullmatch(runner) is None:
            raise ValueError(f"unsafe runner name: {runner}")
        if type(timeout_seconds) is not int or timeout_seconds <= 0:
            raise ValueError(f"case timeout must be positive: {case_id}")
        snapshots.append(
            CaseDefinition(
                case_id=case_id,
                category=category,
                phase=phase,
                title=title,
                expected=expected,
                runner=runner,
                timeout_seconds=timeout_seconds,
                safety=safety,
            )
        )
    return tuple(snapshots)


def _copy_case_definition(case: CaseDefinition) -> CaseDefinition:
    return CaseDefinition(
        case_id=case.case_id,
        category=case.category,
        phase=case.phase,
        title=case.title,
        expected=case.expected,
        runner=case.runner,
        timeout_seconds=case.timeout_seconds,
        safety=case.safety,
    )


def _snapshot_outcome(raw_outcome: object) -> CaseOutcome:
    if type(raw_outcome) is not CaseOutcome:
        raise ValueError("runner did not return an exact CaseOutcome")
    status = raw_outcome.status
    reason = raw_outcome.reason
    evidence = raw_outcome.evidence
    if type(status) is not str or type(reason) is not str:
        raise ValueError("runner outcome status and reason must be plain strings")
    try:
        evidence_snapshot = tuple(evidence)
    except TypeError as exc:
        raise ValueError("runner outcome evidence must be a sequence") from exc
    return CaseOutcome(
        status=status,
        reason=reason,
        evidence=evidence_snapshot,
    )


def _resolve_selected_runners(
    cases: Sequence[CaseDefinition],
    runners: Mapping[str, CaseRunner] | None,
) -> dict[str, CaseRunner]:
    resolved: dict[str, CaseRunner] = {}
    for runner_name in dict.fromkeys(case.runner for case in cases):
        runner: CaseRunner | None
        if runners is None:
            runner = resolve_runner(runner_name)
        else:
            runner = runners.get(runner_name)
            if runner is None:
                raise ValueError(f"unknown runner: {runner_name}")
        if not callable(getattr(runner, "run", None)):
            raise ValueError(f"runner does not implement async run: {runner_name}")
        resolved[runner_name] = runner
    return resolved


def _case_evidence_path_exists(release_root: Path, case: CaseDefinition) -> bool:
    path = release_root / case.category / "evidence" / case.case_id
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        return True
    try:
        entries = list(os.scandir(path))
    except OSError:
        return True
    if len(entries) != 1 or entries[0].name != "campaign.json":
        return True
    try:
        campaign = entries[0].stat(follow_symlinks=False)
    except OSError:
        return True
    return not (
        stat.S_ISREG(campaign.st_mode)
        and campaign.st_uid == os.getuid()
        and campaign.st_nlink == 1
        and stat.S_IMODE(campaign.st_mode) == 0o600
    )


async def _run_selected_case(
    *,
    case: CaseDefinition,
    runner: CaseRunner,
    release_root: Path,
    release_tag: str,
    git_sha: str,
    run_id: str,
    target: str,
    semaphore: asyncio.Semaphore,
    require_cleanup: bool,
    maintenance_lock: MaintenanceLock | None,
) -> _CaseExecution:
    async with semaphore:
        authority_case = case
        authority_case_id = authority_case.case_id
        authority_category = authority_case.category
        started_at = datetime.now(UTC)
        authority_context = CaseContext(
            release_root=release_root,
            run_id=run_id,
            target=target,
        )
        try:
            claimed = claim_case_once(
                context=authority_context,
                case=authority_case,
            )
        except ValueError:
            return _CaseExecution(authority_case_id, None, False)
        if not claimed:
            return _CaseExecution(authority_case_id, None, False)
        outcome: CaseOutcome | None = None
        failure_reasons: list[str] = []
        runner_context = CaseContext(release_root, run_id, target)
        runner_case = _copy_case_definition(authority_case)
        command_termination_confirmed = True
        command_termination_errors: list[CommandTaskTerminationError] = []
        async with _case_execution_scope(
            runner_context,
            authority_case.safety,
            maintenance_lock,
            authority_case,
            command_termination_errors,
        ):
            try:
                raw_outcome = await _await_with_hard_timeout(
                    runner.run(runner_context, runner_case),
                    timeout_seconds=authority_case.timeout_seconds,
                )
                outcome = _snapshot_outcome(raw_outcome)
            except TimeoutError:
                failure_reasons.append(
                    "runner timeout after "
                    f"{authority_case.timeout_seconds} seconds"
                )
            except Exception as exc:
                failure_reasons.append(f"runner failed: {_error_text(exc)}")
        if command_termination_errors:
            command_termination_confirmed = False
            failure_reasons.append(
                "runner command termination unconfirmed: "
                + "; ".join(
                    _error_text(error) for error in command_termination_errors
                )
            )

        cleanup = getattr(runner, "cleanup", None)
        if require_cleanup:
            if not command_termination_confirmed:
                failure_reasons.append(
                    "runner cleanup skipped because command termination is unconfirmed"
                )
            elif not callable(cleanup):
                failure_reasons.append("required runner cleanup is not implemented")
            else:
                cleanup_context = CaseContext(release_root, run_id, target)
                cleanup_case = _copy_case_definition(authority_case)
                async with _case_execution_scope(
                    cleanup_context,
                    authority_case.safety,
                    maintenance_lock,
                    authority_case,
                ):
                    cleanup_call = cast(CleanupFunction, cleanup)
                    try:
                        await _await_with_hard_timeout(
                            cleanup_call(cleanup_context, cleanup_case),
                            timeout_seconds=_case_cleanup_timeout_seconds(
                                authority_case
                            ),
                        )
                    except TimeoutError:
                        failure_reasons.append("runner cleanup timeout")
                    except Exception as exc:
                        failure_reasons.append(
                            f"runner cleanup failed: {_error_text(exc)}"
                        )

        evidence: tuple[Path, ...] = ()
        if outcome is not None:
            try:
                evidence = _validate_outcome_evidence(
                    release_root=release_root,
                    release_tag=release_tag,
                    git_sha=git_sha,
                    case=authority_case,
                    outcome=outcome,
                )
            except ValueError as exc:
                failure_reasons.append(
                    f"case evidence validation failed: {_error_text(exc)}"
                )

        if failure_reasons:
            failure_context = CaseContext(release_root, run_id, target)
            try:
                async with _case_execution_scope(
                    failure_context,
                    authority_case.safety,
                    maintenance_lock,
                    authority_case,
                ):
                    failure_evidence = publish_framework_failure_evidence(
                        context=failure_context,
                        case=authority_case,
                        reason="; ".join(failure_reasons),
                    )
            except ValueError:
                return _CaseExecution(authority_case_id, None, False)
            evidence = (*evidence, failure_evidence)
            outcome = CaseOutcome(
                status="失败",
                reason="; ".join(failure_reasons),
                evidence=evidence,
            )
        elif outcome is None:
            return _CaseExecution(authority_case_id, None, False)
        else:
            outcome = CaseOutcome(
                status=outcome.status,
                reason=outcome.reason,
                evidence=evidence,
            )

        finished_at = datetime.now(UTC)
        execution_path = (
            Path(authority_category)
            / "executions"
            / f"{authority_case_id}.json"
        )
        document: dict[str, Any] = {
            "schema_version": 3,
            "evidence_type": f"{authority_category}_case",
            "case_id": authority_case_id,
            "status": outcome.status,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "target": authority_context.target,
            "command": (
                "run_milestone_2b_case_batch.py "
                f"--case {authority_case_id}"
            ),
            "reason": outcome.reason,
            "mock": False,
            "release_tag": release_tag,
            "git_sha": git_sha,
            "evidence": [path.as_posix() for path in outcome.evidence],
        }
        if set(document) != EXECUTION_RECORD_FIELDS:
            return _CaseExecution(authority_case_id, None, False)
        try:
            publish_json_once(
                release_root=release_root,
                relative_path=execution_path,
                document=document,
            )
        except ValueError:
            return _CaseExecution(authority_case_id, None, False)
        return _CaseExecution(authority_case_id, outcome, True)


def _validate_outcome_evidence(
    *,
    release_root: Path,
    release_tag: str,
    git_sha: str,
    case: CaseDefinition,
    outcome: CaseOutcome,
) -> tuple[Path, ...]:
    if not outcome.evidence:
        raise ValueError("case evidence must not be empty")
    expected_prefix = (case.category, "evidence", case.case_id)
    paths: list[Path] = []
    for index, raw_path in enumerate(outcome.evidence):
        path = Path(raw_path)
        if (
            path.is_absolute()
            or ".." in path.parts
            or len(path.parts) != 4
            or path.parts[:3] != expected_prefix
            or path.suffix != ".json"
        ):
            raise ValueError(
                f"case evidence[{index}] must be below "
                f"{case.category}/evidence/{case.case_id}/"
            )
        document, metadata = _load_release_json_with_metadata(release_root, path)
        if (
            metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise ValueError(f"case evidence has unsafe ownership or mode: {path}")
        if path.name == CAMPAIGN_EVIDENCE_NAME:
            validate_campaign_source_evidence(
                release_root=release_root,
                case=case,
                source_path=path,
            )
        else:
            validate_raw_execution_evidence(
                document,
                path.as_posix(),
                expected_case_id=case.case_id,
            )
        if (
            document.get("release_tag") != release_tag
            or document.get("git_sha") != git_sha
        ):
            raise ValueError(f"case evidence release identity mismatch: {path}")
        paths.append(path)
    if len(paths) != len(set(paths)):
        raise ValueError("case evidence paths must be unique")
    return tuple(paths)


def _error_text(exc: Exception) -> str:
    text = str(exc).strip() or type(exc).__name__
    return " ".join(text.split())[:2000]


async def _await_with_hard_timeout(
    awaitable: Awaitable[object],
    *,
    timeout_seconds: float,
) -> object:
    task = asyncio.ensure_future(awaitable)
    try:
        done, _ = await asyncio.wait((task,), timeout=timeout_seconds)
    except asyncio.CancelledError:
        task.cancel()
        await _wait_for_task_cancellation(task)
        raise
    if task in done:
        return task.result()

    task.cancel()
    await _wait_for_task_cancellation(task)
    raise TimeoutError


async def _wait_for_task_cancellation(task: asyncio.Future[object]) -> None:
    done, _ = await asyncio.wait(
        (task,),
        timeout=CASE_TASK_CANCEL_GRACE_SECONDS,
    )
    if task in done:
        _consume_late_task_result(task)
    else:
        task.add_done_callback(_consume_late_task_result)


def _consume_late_task_result(task: asyncio.Future[object]) -> None:
    try:
        task.result()
    except BaseException:
        pass


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="run milestone 2B negative/load case batches",
        allow_abbrev=False,
    )
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--release-root", required=True, type=Path)
    parser.add_argument("--phase", required=True, choices=sorted(CASE_PHASES))
    parser.add_argument("--case")
    parser.add_argument("--concurrency", type=_positive_int, default=1)
    parser.add_argument("--require-cleanup", action="store_true")
    parser.add_argument("--require-all-selected", action="store_true")
    parser.add_argument("--delegated-lock-holder-pid", type=_positive_int)
    parser.add_argument("--delegated-lock-path", type=Path)
    return parser.parse_args(argv)


def _case_cleanup_timeout_seconds(case: CaseDefinition) -> float:
    if case.category == "load" and case.safety == "canonical_runtime":
        from scripts.milestone_2b_case_runners.load import (
            _runtime_recovery_cleanup_timeout_seconds,
        )

        return _runtime_recovery_cleanup_timeout_seconds(case.case_id)
    return min(case.timeout_seconds, 30)


def _supervisor_deadline_seconds(
    cases: Sequence[CaseDefinition],
    *,
    require_cleanup: bool,
) -> float:
    command_termination_budget = maximum_command_termination_budget_seconds()
    case_seconds = sum(
        case.timeout_seconds
        + CASE_TASK_CANCEL_GRACE_SECONDS
        + command_termination_budget
        + (
            _case_cleanup_timeout_seconds(case)
            + CASE_TASK_CANCEL_GRACE_SECONDS
            + command_termination_budget
            if require_cleanup
            else 0
        )
        for case in cases
    )
    return case_seconds + SUPERVISOR_FIXED_GRACE_SECONDS


async def _run_parent_runtime_recovery(
    *,
    cases: Sequence[CaseDefinition],
    release_root: Path,
    run_id: str,
    target: str,
    maintenance_lock: MaintenanceLock,
) -> None:
    resolved_runners = _resolve_selected_runners(cases, None)
    for authority_case in cases:
        runner = resolved_runners[authority_case.runner]
        cleanup = getattr(runner, "cleanup", None)
        if not callable(cleanup):
            raise ValueError(
                f"parent recovery cleanup is not implemented: {authority_case.case_id}"
            )
        cleanup_context = CaseContext(release_root, run_id, target)
        cleanup_case = _copy_case_definition(authority_case)
        async with _case_execution_scope(
            cleanup_context,
            authority_case.safety,
            maintenance_lock,
            authority_case,
        ):
            await _await_with_hard_timeout(
                cast(CleanupFunction, cleanup)(cleanup_context, cleanup_case),
                timeout_seconds=_case_cleanup_timeout_seconds(authority_case),
            )


def _recover_parent_runtime_state(
    *,
    cases: Sequence[CaseDefinition],
    release_root: Path,
    run_id: str,
    target: str,
    delegated_lock_holder_pid: int | None,
    delegated_lock_path: Path | None,
) -> None:
    canonical_cases = tuple(
        case for case in cases if case.safety == "canonical_runtime"
    )
    if not canonical_cases:
        return
    if (delegated_lock_holder_pid is None) != (delegated_lock_path is None):
        raise ValueError("parent recovery delegated lock authority is incomplete")
    lock_context: AbstractContextManager[MaintenanceLock]
    if delegated_lock_holder_pid is not None:
        assert delegated_lock_path is not None
        lock_context = DelegatedMaintenanceLockGuard(
            release_root,
            delegated_lock_holder_pid,
            delegated_lock_path,
        )
    else:
        lock_context = MaintenanceLockGuard(release_root)
    with lock_context as maintenance_lock:
        if not maintenance_lock.held_for(release_root):
            raise ValueError("parent recovery maintenance lock is not held")
        asyncio.run(
            _run_parent_runtime_recovery(
                cases=canonical_cases,
                release_root=release_root,
                run_id=run_id,
                target=target,
                maintenance_lock=maintenance_lock,
            )
        )
        if not maintenance_lock.held_for(release_root):
            raise ValueError("parent recovery maintenance lock was lost")


def _write_supervisor_result(
    descriptor: int,
    result: _SupervisorResult,
) -> None:
    payload = (
        json.dumps(
            {
                "exit_code": result.exit_code,
                "error": result.error,
            },
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
        + b"\n"
    )
    if len(payload) > SUPERVISOR_MESSAGE_LIMIT:
        raise ValueError("supervisor result exceeds the message limit")
    while True:
        try:
            written = os.write(descriptor, payload)
            break
        except InterruptedError:
            continue
    if written != len(payload):
        raise OSError("partial supervisor result write")


async def _run_supervised_batch_child(
    *,
    cases: Sequence[CaseDefinition],
    release_root: Path,
    concurrency: int,
    require_cleanup: bool,
    require_all_selected: bool,
    run_id: str,
    target: str,
    result_descriptor: int,
    delegated_lock_holder_pid: int | None,
    delegated_lock_path: Path | None,
) -> int:
    try:
        batch = await run_case_batch(
            cases=cases,
            release_root=release_root,
            concurrency=concurrency,
            require_cleanup=require_cleanup,
            require_all_selected=require_all_selected,
            run_id=run_id,
            target=target,
            delegated_lock_holder_pid=delegated_lock_holder_pid,
            delegated_lock_path=delegated_lock_path,
        )
    except ValueError as exc:
        result = _SupervisorResult(exit_code=2, error=_error_text(exc))
    except BaseException as exc:
        detail = " ".join((str(exc).strip() or type(exc).__name__).split())[:2000]
        result = _SupervisorResult(
            exit_code=2,
            error=f"batch child failed: {detail}",
        )
    else:
        result = _SupervisorResult(exit_code=batch.exit_code, error=None)
    _write_supervisor_result(result_descriptor, result)
    return result.exit_code


def _batch_child_entry(
    *,
    cases: Sequence[CaseDefinition],
    release_root: Path,
    concurrency: int,
    require_cleanup: bool,
    require_all_selected: bool,
    run_id: str,
    target: str,
    delegated_lock_holder_pid: int | None,
    delegated_lock_path: Path | None,
    result_read_descriptor: int,
    result_write_descriptor: int,
    process_read_descriptor: int,
    process_write_descriptor: int,
    inherited_signal_mask: set[int | signal.Signals],
) -> None:
    from scripts.milestone_2b_case_runners.process import (
        _configure_process_supervisor,
    )

    exit_code = 2
    try:
        for signal_number in SUPERVISOR_BLOCKED_SIGNALS:
            signal.signal(signal_number, signal.SIG_DFL)
        os.setsid()
        os.close(result_read_descriptor)
        os.close(process_read_descriptor)
        _configure_process_supervisor(process_write_descriptor)
        signal.pthread_sigmask(signal.SIG_SETMASK, inherited_signal_mask)
        exit_code = asyncio.run(
            _run_supervised_batch_child(
                cases=cases,
                release_root=release_root,
                concurrency=concurrency,
                require_cleanup=require_cleanup,
                require_all_selected=require_all_selected,
                run_id=run_id,
                target=target,
                result_descriptor=result_write_descriptor,
                delegated_lock_holder_pid=delegated_lock_holder_pid,
                delegated_lock_path=delegated_lock_path,
            )
        )
    except BaseException as exc:
        try:
            detail = " ".join(
                (str(exc).strip() or type(exc).__name__).split()
            )[:2000]
            _write_supervisor_result(
                result_write_descriptor,
                _SupervisorResult(
                    exit_code=2,
                    error=f"batch child startup failed: {detail}",
                ),
            )
        except (OSError, ValueError):
            pass
    finally:
        _configure_process_supervisor(None)
        for descriptor in (result_write_descriptor, process_write_descriptor):
            try:
                os.close(descriptor)
            except OSError:
                pass
    os._exit(exit_code)


def _poll_batch_child(
    child_pid: int,
    child_status: int | None,
) -> int | None:
    if child_status is not None:
        return child_status
    try:
        waited_pid, status = os.waitpid(child_pid, os.WNOHANG)
    except ChildProcessError:
        return 0
    if waited_pid == 0:
        return None
    return status


def _read_pipe_bytes(
    descriptor: int,
    buffer: bytearray,
) -> bool:
    while True:
        try:
            chunk = os.read(descriptor, 65_536)
        except BlockingIOError:
            return True
        except InterruptedError:
            continue
        if not chunk:
            return False
        buffer.extend(chunk)
        if len(buffer) > SUPERVISOR_MESSAGE_LIMIT:
            raise ValueError("supervisor pipe exceeded the message limit")


def _consume_process_events(
    buffer: bytearray,
    active_process_groups: set[int],
    *,
    child_pid: int,
) -> None:
    parent_process_group = os.getpgrp()
    while b"\n" in buffer:
        mutable_line, _, remainder = buffer.partition(b"\n")
        buffer[:] = remainder
        raw_line = bytes(mutable_line)
        if len(raw_line) < 2 or raw_line[:1] not in {b"+", b"-"}:
            raise ValueError("invalid process supervisor event")
        try:
            process_group_id = int(raw_line[1:].decode("ascii"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("invalid process supervisor process group") from exc
        if (
            process_group_id <= 0
            or process_group_id in {parent_process_group, child_pid}
        ):
            raise ValueError("unsafe process supervisor process group")
        if raw_line[:1] == b"+":
            if process_group_id in active_process_groups:
                raise ValueError("duplicate process supervisor registration")
            active_process_groups.add(process_group_id)
        else:
            if process_group_id not in active_process_groups:
                raise ValueError("unknown process supervisor deregistration")
            active_process_groups.remove(process_group_id)


def _parse_supervisor_result(buffer: bytearray) -> _SupervisorResult | None:
    if b"\n" not in buffer:
        return None
    raw_line, _, remainder = buffer.partition(b"\n")
    if remainder:
        raise ValueError("supervisor result contains trailing data")
    try:
        document = json.loads(raw_line)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid supervisor result JSON") from exc
    if type(document) is not dict or set(document) != {"exit_code", "error"}:
        raise ValueError("invalid supervisor result fields")
    exit_code = document["exit_code"]
    error = document["error"]
    if type(exit_code) is not int or exit_code not in {0, 1, 2}:
        raise ValueError("invalid supervisor result exit code")
    if error is not None and (type(error) is not str or not error or len(error) > 2000):
        raise ValueError("invalid supervisor result error")
    if (exit_code == 2) != (error is not None):
        raise ValueError("supervisor rejection must contain exactly one error")
    return _SupervisorResult(exit_code=exit_code, error=error)


def _supervisor_terminal_state_error(
    *,
    result: _SupervisorResult,
    child_status: int | None,
    forced_cleanup_required: bool,
    descriptors: set[int],
    result_descriptor: int,
    process_descriptor: int,
    process_buffer: bytearray,
    active_process_groups: set[int],
    batch_process_group_alive: bool,
) -> str | None:
    if active_process_groups:
        return "supervisor terminal result left active process groups"
    if batch_process_group_alive:
        return "supervisor terminal result left the batch process group alive"
    if process_buffer:
        return "supervisor process protocol ended with a partial event"
    if result_descriptor in descriptors or process_descriptor in descriptors:
        return "supervisor result and process protocols did not close"
    if result.exit_code != 0:
        return None
    if child_status is None:
        return "batch child published success without a wait status"
    if not os.WIFEXITED(child_status):
        if os.WIFSIGNALED(child_status):
            return (
                "batch child published success but terminated by signal "
                f"{os.WTERMSIG(child_status)}"
            )
        return (
            "batch child published success with abnormal wait status "
            f"{child_status}"
        )
    child_exit_code = os.WEXITSTATUS(child_status)
    if child_exit_code != 0:
        return (
            "batch child published success but exited with status "
            f"{child_exit_code}"
        )
    if forced_cleanup_required:
        return "batch child success required forced process cleanup"
    return None


def _signal_process_group(process_group_id: int, signal_number: int) -> None:
    try:
        os.killpg(process_group_id, signal_number)
    except (PermissionError, ProcessLookupError):
        pass


def _signal_batch_child(child_pid: int, signal_number: int) -> None:
    try:
        os.killpg(child_pid, signal_number)
    except ProcessLookupError:
        try:
            os.kill(child_pid, signal_number)
        except ProcessLookupError:
            pass


def _process_group_is_alive(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _discard_gone_process_groups(active_process_groups: set[int]) -> None:
    for process_group_id in tuple(active_process_groups):
        try:
            os.killpg(process_group_id, 0)
        except ProcessLookupError:
            active_process_groups.remove(process_group_id)
        except PermissionError:
            pass


def _drain_supervisor_pipes(
    descriptors: set[int],
    *,
    result_descriptor: int,
    process_descriptor: int,
    result_buffer: bytearray,
    process_buffer: bytearray,
    active_process_groups: set[int],
    child_pid: int,
    timeout: float,
) -> None:
    if not descriptors:
        if timeout > 0:
            time.sleep(timeout)
        return
    readable, _, _ = select.select(tuple(descriptors), (), (), max(timeout, 0))
    for descriptor in readable:
        buffer = result_buffer if descriptor == result_descriptor else process_buffer
        if not _read_pipe_bytes(descriptor, buffer):
            descriptors.remove(descriptor)
    _consume_process_events(
        process_buffer,
        active_process_groups,
        child_pid=child_pid,
    )


def _terminate_supervised_batch(
    *,
    child_pid: int,
    child_status: int | None,
    active_process_groups: set[int],
    descriptors: set[int],
    result_descriptor: int,
    process_descriptor: int,
    result_buffer: bytearray,
    process_buffer: bytearray,
) -> int | None:
    for process_group_id in active_process_groups:
        _signal_process_group(process_group_id, signal.SIGTERM)
    batch_process_group_alive = _process_group_is_alive(child_pid)
    if child_status is None:
        _signal_batch_child(child_pid, signal.SIGTERM)
    elif batch_process_group_alive:
        _signal_process_group(child_pid, signal.SIGTERM)
    terminate_deadline = time.monotonic() + SUPERVISOR_SIGNAL_GRACE_SECONDS
    while time.monotonic() < terminate_deadline:
        _drain_supervisor_pipes(
            descriptors,
            result_descriptor=result_descriptor,
            process_descriptor=process_descriptor,
            result_buffer=result_buffer,
            process_buffer=process_buffer,
            active_process_groups=active_process_groups,
            child_pid=child_pid,
            timeout=min(0.02, terminate_deadline - time.monotonic()),
        )
        child_status = _poll_batch_child(child_pid, child_status)
        _discard_gone_process_groups(active_process_groups)
        batch_process_group_alive = _process_group_is_alive(child_pid)
        if (
            child_status is not None
            and not active_process_groups
            and not batch_process_group_alive
        ):
            return child_status

    for process_group_id in active_process_groups:
        _signal_process_group(process_group_id, signal.SIGKILL)
    if child_status is None:
        _signal_batch_child(child_pid, signal.SIGKILL)
    elif batch_process_group_alive:
        _signal_process_group(child_pid, signal.SIGKILL)
    reap_deadline = time.monotonic() + SUPERVISOR_FIXED_GRACE_SECONDS
    while time.monotonic() < reap_deadline:
        _drain_supervisor_pipes(
            descriptors,
            result_descriptor=result_descriptor,
            process_descriptor=process_descriptor,
            result_buffer=result_buffer,
            process_buffer=process_buffer,
            active_process_groups=active_process_groups,
            child_pid=child_pid,
            timeout=min(0.02, reap_deadline - time.monotonic()),
        )
        child_status = _poll_batch_child(child_pid, child_status)
        _discard_gone_process_groups(active_process_groups)
        batch_process_group_alive = _process_group_is_alive(child_pid)
        if (
            child_status is not None
            and not active_process_groups
            and not batch_process_group_alive
        ):
            return child_status
    raise RuntimeError("supervisor could not reap the batch process tree")


def _require_supervised_process_tree_reaped(
    *,
    child_pid: int,
    child_status: int | None,
    active_process_groups: set[int],
) -> None:
    _discard_gone_process_groups(active_process_groups)
    if (
        child_status is None
        or active_process_groups
        or _process_group_is_alive(child_pid)
    ):
        raise RuntimeError("supervisor termination did not reap the batch process tree")


@contextmanager
def _supervisor_signal_scope() -> Iterator[_SupervisorSignalState]:
    handled_signals = SUPERVISOR_BLOCKED_SIGNALS
    previous_handlers = {
        signal_number: signal.getsignal(signal_number)
        for signal_number in handled_signals
    }
    signal_state = _SupervisorSignalState()

    def raise_supervisor_signal(
        signal_number: int,
        _frame: object,
    ) -> None:
        signal_state.handle(signal_number)

    try:
        for signal_number in handled_signals:
            signal.signal(signal_number, raise_supervisor_signal)
        yield signal_state
    finally:
        for signal_number, previous_handler in previous_handlers.items():
            signal.signal(signal_number, previous_handler)


def _supervise_case_batch(
    *,
    cases: Sequence[CaseDefinition],
    release_root: Path,
    concurrency: int,
    require_cleanup: bool,
    require_all_selected: bool,
    run_id: str,
    target: str,
    delegated_lock_holder_pid: int | None = None,
    delegated_lock_path: Path | None = None,
) -> _SupervisorResult:
    with _supervisor_signal_scope() as signal_state:
        return _supervise_case_batch_processes(
            cases=cases,
            release_root=release_root,
            concurrency=concurrency,
            require_cleanup=require_cleanup,
            require_all_selected=require_all_selected,
            run_id=run_id,
            target=target,
            delegated_lock_holder_pid=delegated_lock_holder_pid,
            delegated_lock_path=delegated_lock_path,
            signal_state=signal_state,
        )


def _supervise_case_batch_processes(
    *,
    cases: Sequence[CaseDefinition],
    release_root: Path,
    concurrency: int,
    require_cleanup: bool,
    require_all_selected: bool,
    run_id: str,
    target: str,
    delegated_lock_holder_pid: int | None,
    delegated_lock_path: Path | None,
    signal_state: _SupervisorSignalState,
) -> _SupervisorResult:
    open_descriptors: set[int] = set()
    inherited_signal_mask: set[int | signal.Signals] | None = None
    signal_mask_blocked = False
    try:
        result_read, result_write = os.pipe()
        open_descriptors.update((result_read, result_write))
        process_read, process_write = os.pipe()
        open_descriptors.update((process_read, process_write))
        os.set_blocking(result_read, False)
        os.set_blocking(process_read, False)
        inherited_signal_mask = signal.pthread_sigmask(
            signal.SIG_BLOCK,
            SUPERVISOR_BLOCKED_SIGNALS,
        )
        signal_mask_blocked = True
        child_pid = os.fork()
    except BaseException:
        if signal_mask_blocked and inherited_signal_mask is not None:
            signal.pthread_sigmask(signal.SIG_SETMASK, inherited_signal_mask)
        for descriptor in tuple(open_descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise

    assert inherited_signal_mask is not None
    if child_pid == 0:
        _batch_child_entry(
            cases=cases,
            release_root=release_root,
            concurrency=concurrency,
            require_cleanup=require_cleanup,
            require_all_selected=require_all_selected,
            run_id=run_id,
            target=target,
            delegated_lock_holder_pid=delegated_lock_holder_pid,
            delegated_lock_path=delegated_lock_path,
            result_read_descriptor=result_read,
            result_write_descriptor=result_write,
            process_read_descriptor=process_read,
            process_write_descriptor=process_write,
            inherited_signal_mask=inherited_signal_mask,
        )
        os._exit(2)

    descriptors = {result_read, process_read}
    result_buffer = bytearray()
    process_buffer = bytearray()
    active_process_groups: set[int] = set()
    child_status: int | None = None
    result: _SupervisorResult | None = None
    supervisor_error: str | None = None
    process_tree_reaped = False
    forced_cleanup_required = False
    parent_recovery_attempted = False
    deadline = time.monotonic() + _supervisor_deadline_seconds(
        cases,
        require_cleanup=require_cleanup,
    )
    try:
        os.close(result_write)
        open_descriptors.remove(result_write)
        os.close(process_write)
        open_descriptors.remove(process_write)
        signal.pthread_sigmask(signal.SIG_SETMASK, inherited_signal_mask)
        signal_mask_blocked = False
        while result is None and child_status is None and time.monotonic() < deadline:
            _drain_supervisor_pipes(
                descriptors,
                result_descriptor=result_read,
                process_descriptor=process_read,
                result_buffer=result_buffer,
                process_buffer=process_buffer,
                active_process_groups=active_process_groups,
                child_pid=child_pid,
                timeout=min(0.05, deadline - time.monotonic()),
            )
            result = _parse_supervisor_result(result_buffer)
            child_status = _poll_batch_child(child_pid, child_status)

        _drain_supervisor_pipes(
            descriptors,
            result_descriptor=result_read,
            process_descriptor=process_read,
            result_buffer=result_buffer,
            process_buffer=process_buffer,
            active_process_groups=active_process_groups,
            child_pid=child_pid,
            timeout=0,
        )
        if result is None:
            result = _parse_supervisor_result(result_buffer)

        if result is not None and child_status is None:
            exit_deadline = time.monotonic() + SUPERVISOR_RESULT_GRACE_SECONDS
            while child_status is None and time.monotonic() < exit_deadline:
                _drain_supervisor_pipes(
                    descriptors,
                    result_descriptor=result_read,
                    process_descriptor=process_read,
                    result_buffer=result_buffer,
                    process_buffer=process_buffer,
                    active_process_groups=active_process_groups,
                    child_pid=child_pid,
                    timeout=min(0.02, exit_deadline - time.monotonic()),
                )
                child_status = _poll_batch_child(child_pid, child_status)

        _drain_supervisor_pipes(
            descriptors,
            result_descriptor=result_read,
            process_descriptor=process_read,
            result_buffer=result_buffer,
            process_buffer=process_buffer,
            active_process_groups=active_process_groups,
            child_pid=child_pid,
            timeout=0,
        )
        parsed_result = _parse_supervisor_result(result_buffer)
        if parsed_result is not None:
            result = parsed_result

        if result is None:
            supervisor_error = "batch child did not publish a valid terminal result"
        batch_process_group_alive = _process_group_is_alive(child_pid)
        if child_status is None or active_process_groups or batch_process_group_alive:
            forced_cleanup_required = True
            recovery_error: Exception | None = None
            with signal_state.defer():
                child_status = _terminate_supervised_batch(
                    child_pid=child_pid,
                    child_status=child_status,
                    active_process_groups=active_process_groups,
                    descriptors=descriptors,
                    result_descriptor=result_read,
                    process_descriptor=process_read,
                    result_buffer=result_buffer,
                    process_buffer=process_buffer,
                )
                _require_supervised_process_tree_reaped(
                    child_pid=child_pid,
                    child_status=child_status,
                    active_process_groups=active_process_groups,
                )
                process_tree_reaped = True
                parent_recovery_attempted = True
                try:
                    _recover_parent_runtime_state(
                        cases=cases,
                        release_root=release_root,
                        run_id=run_id,
                        target=target,
                        delegated_lock_holder_pid=delegated_lock_holder_pid,
                        delegated_lock_path=delegated_lock_path,
                    )
                except Exception as recovery_exc:
                    recovery_error = recovery_exc
            pending_signal = signal_state.pop_pending()
            if pending_signal is not None:
                if recovery_error is not None:
                    raise pending_signal from recovery_error
                raise pending_signal
            if recovery_error is not None:
                return _SupervisorResult(
                    exit_code=2,
                    error=f"parent runtime recovery failed: {_error_text(recovery_error)}",
                )
        _drain_supervisor_pipes(
            descriptors,
            result_descriptor=result_read,
            process_descriptor=process_read,
            result_buffer=result_buffer,
            process_buffer=process_buffer,
            active_process_groups=active_process_groups,
            child_pid=child_pid,
            timeout=0,
        )
        parsed_result = _parse_supervisor_result(result_buffer)
        if parsed_result is not None:
            result = parsed_result
        if result is None:
            return _SupervisorResult(exit_code=2, error=supervisor_error)
        terminal_state_error = _supervisor_terminal_state_error(
            result=result,
            child_status=child_status,
            forced_cleanup_required=forced_cleanup_required,
            descriptors=descriptors,
            result_descriptor=result_read,
            process_descriptor=process_read,
            process_buffer=process_buffer,
            active_process_groups=active_process_groups,
            batch_process_group_alive=_process_group_is_alive(child_pid),
        )
        if terminal_state_error is not None:
            return _SupervisorResult(exit_code=2, error=terminal_state_error)
        return result
    except BaseException as exc:
        cleanup_error: Exception | None = None
        recovery_error = None
        with signal_state.defer():
            if not process_tree_reaped:
                try:
                    child_status = _terminate_supervised_batch(
                        child_pid=child_pid,
                        child_status=child_status,
                        active_process_groups=active_process_groups,
                        descriptors=descriptors,
                        result_descriptor=result_read,
                        process_descriptor=process_read,
                        result_buffer=result_buffer,
                        process_buffer=process_buffer,
                    )
                    _require_supervised_process_tree_reaped(
                        child_pid=child_pid,
                        child_status=child_status,
                        active_process_groups=active_process_groups,
                    )
                    process_tree_reaped = True
                except Exception as cleanup_exc:
                    cleanup_error = cleanup_exc
            if process_tree_reaped and not parent_recovery_attempted:
                parent_recovery_attempted = True
                try:
                    _recover_parent_runtime_state(
                        cases=cases,
                        release_root=release_root,
                        run_id=run_id,
                        target=target,
                        delegated_lock_holder_pid=delegated_lock_holder_pid,
                        delegated_lock_path=delegated_lock_path,
                    )
                except Exception as recovery_exc:
                    recovery_error = recovery_exc
        pending_signal = signal_state.pop_pending()
        if not isinstance(exc, Exception):
            failure = recovery_error or cleanup_error
            if failure is not None:
                raise exc from failure
            raise
        if pending_signal is not None:
            failure = recovery_error or cleanup_error or exc
            raise pending_signal from failure
        if cleanup_error is not None:
            return _SupervisorResult(
                exit_code=2,
                error=f"supervisor cleanup failed: {_error_text(cleanup_error)}",
            )
        if recovery_error is not None:
            return _SupervisorResult(
                exit_code=2,
                error=f"parent runtime recovery failed: {_error_text(recovery_error)}",
            )
        return _SupervisorResult(
            exit_code=2,
            error=f"supervisor failed: {_error_text(exc)}",
        )
    finally:
        if signal_mask_blocked:
            signal.pthread_sigmask(signal.SIG_SETMASK, inherited_signal_mask)
        for descriptor in tuple(open_descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(argv)
    try:
        catalog = load_case_catalog(arguments.catalog)
        selected = select_cases(catalog.cases, arguments.phase, arguments.case)
        result = _supervise_case_batch(
            cases=selected,
            release_root=arguments.release_root,
            concurrency=arguments.concurrency,
            require_cleanup=arguments.require_cleanup,
            require_all_selected=arguments.require_all_selected,
            run_id=uuid.uuid4().hex,
            target=socket.gethostname(),
            delegated_lock_holder_pid=arguments.delegated_lock_holder_pid,
            delegated_lock_path=arguments.delegated_lock_path,
        )
    except ValueError as exc:
        print(f"milestone 2B case batch rejected: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    except _SupervisorSignal as exc:
        return 128 + exc.signal_number
    if result.error is not None:
        print(f"milestone 2B case batch rejected: {result.error}", file=sys.stderr)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
