from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from .catalog import CampaignPhase, CaseExecution, CaseSpec
from .execution import CampaignCaseExecutor
from .plan import (
    CampaignPlan,
    execution_path,
    read_case_executions,
    validate_release_executions,
)

ReadinessState = Literal["passed", "failed", "blocked", "not_run", "ready"]

_LOCAL_KINDS = frozenset({"phase_gate"})
_LIVE_EXECUTOR_KINDS = frozenset(
    {
        "append_task_types",
        "completed_result_reuse",
        "conflicting_submission",
        "face_management",
        "face_recognition",
        "idempotent_submission",
        "image_boundary",
        "long_course",
        "mixed_image",
        "negative_query",
        "negative_submission",
        "offline_baseline",
        "online_image",
        "priority",
        "query",
        "realtime_asr",
        "realtime_asr_reconnect",
        "s_stream",
        "unique_submission",
    }
)
_INTEGRATION_BLOCKERS: Mapping[str, str] = {
    "media_download": "需要显式 SSH/媒体下载探针适配器；本地协调器不会访问远端主机",
    "face_photo_residue": "需要显式远端人脸照片残留检查适配器",
    "mixed": "需要混合负载、实时主机指标和停止语义适配器",
    "single_operator_fault": "需要显式算子故障注入和恢复语义适配器",
    "gpu_group_fault": "需要显式 GPU 组故障注入和恢复语义适配器",
    "platform_fault": "需要显式平台服务故障注入和恢复语义适配器",
    "middleware_fault": "需要显式中间件故障注入和恢复语义适配器",
    "soak": "需要长稳负载、实时主机指标和停止语义适配器",
}
_GLOBAL_INTEGRATION_BLOCKERS = (
    "实时主机 CPU、内存、磁盘和 GPU 指标采集尚未接入本地协调器",
    "SSH/媒体下载探针尚未接入本地协调器",
    "故障注入与恢复语义探针尚未接入本地协调器",
    "混合负载与长稳负载阶段协调尚未接入本地协调器",
)


class CoordinatorBlockedError(RuntimeError):
    """The requested case is unsafe or not ready to execute."""


class CaseExecutor(Protocol):
    async def execute(self, case_id: str) -> Path: ...


ExecutorFactory = Callable[[CampaignPlan, Path], CaseExecutor]


@dataclass(frozen=True, slots=True)
class CaseReadiness:
    case_id: str
    phase: CampaignPhase
    required: bool
    kind: str
    state: ReadinessState
    reason: str
    prerequisites: tuple[str, ...]
    evidence_status: str | None
    requires_live_execution: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "phase": self.phase.value,
            "required": self.required,
            "kind": self.kind,
            "state": self.state,
            "reason": self.reason,
            "prerequisites": list(self.prerequisites),
            "evidence_status": self.evidence_status,
            "requires_live_execution": self.requires_live_execution,
        }


@dataclass(frozen=True, slots=True)
class CoordinatorValidation:
    passed: bool
    required_missing: tuple[str, ...]
    required_failed: tuple[str, ...]
    optional_pending: tuple[str, ...]
    catalog_verdict: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "required_missing": list(self.required_missing),
            "required_failed": list(self.required_failed),
            "optional_pending": list(self.optional_pending),
            "catalog_verdict": dict(self.catalog_verdict),
        }


@dataclass(frozen=True, slots=True)
class CoordinatorStatus:
    campaign_id: str
    git_sha: str
    active_phase: CampaignPhase | None
    required_complete: bool
    ready_case_ids: tuple[str, ...]
    blocked_case_ids: tuple[str, ...]
    cases: tuple[CaseReadiness, ...]
    validation: CoordinatorValidation
    integration_blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "campaign_id": self.campaign_id,
            "git_sha": self.git_sha,
            "active_phase": self.active_phase.value if self.active_phase else None,
            "required_complete": self.required_complete,
            "ready_case_ids": list(self.ready_case_ids),
            "blocked_case_ids": list(self.blocked_case_ids),
            "cases": [item.to_dict() for item in self.cases],
            "validation": self.validation.to_dict(),
            "integration_blockers": list(self.integration_blockers),
        }


@dataclass(frozen=True, slots=True)
class CaseExecutionResult:
    case_id: str
    status: str
    evidence_path: Path

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "status": self.status,
            "evidence_path": str(self.evidence_path),
        }


def _validate_catalog_order(plan: CampaignPlan) -> None:
    cases = plan.catalog.cases
    sequences = tuple(case.phase.sequence for case in cases)
    if sequences != tuple(sorted(sequences)):
        raise ValueError("Campaign catalog 必须保持阶段顺序")
    positions = {case.case_id: index for index, case in enumerate(cases)}
    for index, case in enumerate(cases):
        forward = tuple(
            prerequisite for prerequisite in case.prerequisites if positions[prerequisite] >= index
        )
        if forward:
            raise ValueError(
                f"Campaign catalog 前置用例必须先于当前用例: {case.case_id} <- " + ",".join(forward)
            )


def _execution_index(executions: list[CaseExecution]) -> dict[str, CaseExecution]:
    indexed: dict[str, CaseExecution] = {}
    for execution in executions:
        if execution.case_id in indexed:
            raise ValueError(f"Campaign 用例证据重复: {execution.case_id}")
        indexed[execution.case_id] = execution
    return indexed


class CampaignCoordinator:
    def __init__(
        self,
        plan: CampaignPlan,
        release_root: Path,
        *,
        executor_factory: ExecutorFactory | None = None,
    ) -> None:
        _validate_catalog_order(plan)
        self.plan = plan
        self.release_root = release_root
        self._executor_factory = executor_factory or CampaignCaseExecutor
        self._case_by_id = {case.case_id: case for case in plan.catalog.cases}

    def _executions(self) -> tuple[list[CaseExecution], dict[str, CaseExecution]]:
        executions = read_case_executions(self.release_root, self.plan)
        return executions, _execution_index(executions)

    def validate(self) -> CoordinatorValidation:
        executions, indexed = self._executions()
        verdict = validate_release_executions(self.release_root, self.plan)
        required_missing = tuple(
            case.case_id
            for case in self.plan.catalog.cases
            if case.required and case.case_id not in indexed
        )
        required_failed = tuple(
            case.case_id
            for case in self.plan.catalog.cases
            if case.required
            and case.case_id in indexed
            and indexed[case.case_id].status != "passed"
        )
        optional_pending = tuple(
            case.case_id
            for case in self.plan.catalog.cases
            if not case.required
            and (case.case_id not in indexed or indexed[case.case_id].status != "passed")
        )
        integrity_fields = (
            "duplicate",
            "missing_evidence",
            "unexpected",
            "evidence_mismatch",
        )
        integrity_failed = any(verdict.get(field) for field in integrity_fields)
        # The catalog verdict counts an explicitly blocked optional case as failed. Required-only
        # completion intentionally does not, while the optional case remains visible as pending.
        passed = not required_missing and not required_failed and not integrity_failed
        if len(indexed) != len(executions):
            passed = False
        return CoordinatorValidation(
            passed=passed,
            required_missing=required_missing,
            required_failed=required_failed,
            optional_pending=optional_pending,
            catalog_verdict=verdict,
        )

    def _active_phase(self, indexed: Mapping[str, CaseExecution]) -> CampaignPhase | None:
        for phase in CampaignPhase:
            required = tuple(
                case for case in self.plan.catalog.cases if case.phase is phase and case.required
            )
            if any(
                case.case_id not in indexed or indexed[case.case_id].status != "passed"
                for case in required
            ):
                return phase
        return None

    def _earlier_phase_gate_blocker(
        self,
        case: CaseSpec,
        indexed: Mapping[str, CaseExecution],
    ) -> str | None:
        known = self._case_by_id
        for phase in CampaignPhase:
            if phase.sequence >= case.phase.sequence:
                break
            gate_id = f"PHASE-{phase.sequence}-COMPLETE"
            if gate_id not in known:
                continue
            execution = indexed.get(gate_id)
            if execution is None:
                return f"较早必需阶段门禁缺失: {gate_id}"
            if execution.status != "passed":
                return f"较早必需阶段门禁未通过: {gate_id}={execution.status}"
        return None

    def _readiness(
        self,
        case: CaseSpec,
        indexed: Mapping[str, CaseExecution],
        active_phase: CampaignPhase | None,
    ) -> CaseReadiness:
        kind = str(case.load.get("kind", ""))
        existing = indexed.get(case.case_id)
        if existing is not None:
            reason = (
                "用例证据已通过且不可改写"
                if existing.status == "passed"
                else f"用例已有不可改写的 {existing.status} 证据"
            )
            return CaseReadiness(
                case.case_id,
                case.phase,
                case.required,
                kind,
                existing.status,
                reason,
                case.prerequisites,
                existing.status,
                kind in _LIVE_EXECUTOR_KINDS,
            )

        gate_blocker = self._earlier_phase_gate_blocker(case, indexed)
        if gate_blocker is not None:
            return CaseReadiness(
                case.case_id,
                case.phase,
                case.required,
                kind,
                "blocked",
                gate_blocker,
                case.prerequisites,
                None,
                kind in _LIVE_EXECUTOR_KINDS,
            )

        if active_phase is not None and case.phase.sequence > active_phase.sequence:
            return CaseReadiness(
                case.case_id,
                case.phase,
                case.required,
                kind,
                "blocked",
                f"较早必需阶段尚未完成: {active_phase.value}",
                case.prerequisites,
                None,
                kind in _LIVE_EXECUTOR_KINDS,
            )

        missing = tuple(
            prerequisite for prerequisite in case.prerequisites if prerequisite not in indexed
        )
        failed = tuple(
            f"{prerequisite}={indexed[prerequisite].status}"
            for prerequisite in case.prerequisites
            if prerequisite in indexed and indexed[prerequisite].status != "passed"
        )
        if missing or failed:
            reasons = []
            if missing:
                reasons.append("前置证据缺失: " + ",".join(missing))
            if failed:
                reasons.append("前置证据未通过: " + ",".join(failed))
            return CaseReadiness(
                case.case_id,
                case.phase,
                case.required,
                kind,
                "blocked",
                "；".join(reasons),
                case.prerequisites,
                None,
                kind in _LIVE_EXECUTOR_KINDS,
            )

        integration_reason = _INTEGRATION_BLOCKERS.get(kind)
        if integration_reason is not None:
            return CaseReadiness(
                case.case_id,
                case.phase,
                case.required,
                kind,
                "blocked",
                integration_reason,
                case.prerequisites,
                None,
                False,
            )
        if kind not in _LOCAL_KINDS and kind not in _LIVE_EXECUTOR_KINDS:
            return CaseReadiness(
                case.case_id,
                case.phase,
                case.required,
                kind,
                "blocked",
                f"没有已注册的本地或北向执行适配器: {kind or '<empty>'}",
                case.prerequisites,
                None,
                False,
            )
        return CaseReadiness(
            case.case_id,
            case.phase,
            case.required,
            kind,
            "ready",
            "本地阶段门禁可执行" if kind in _LOCAL_KINDS else "前置条件已满足",
            case.prerequisites,
            None,
            kind in _LIVE_EXECUTOR_KINDS,
        )

    def status(self) -> CoordinatorStatus:
        _, indexed = self._executions()
        active_phase = self._active_phase(indexed)
        cases = tuple(
            self._readiness(case, indexed, active_phase) for case in self.plan.catalog.cases
        )
        validation = self.validate()
        return CoordinatorStatus(
            campaign_id=self.plan.campaign_id,
            git_sha=self.plan.git_sha,
            active_phase=active_phase,
            required_complete=validation.passed,
            ready_case_ids=tuple(item.case_id for item in cases if item.state == "ready"),
            blocked_case_ids=tuple(item.case_id for item in cases if item.state == "blocked"),
            cases=cases,
            validation=validation,
            integration_blockers=_GLOBAL_INTEGRATION_BLOCKERS,
        )

    def readiness(self, case_id: str) -> CaseReadiness:
        case = self._case_by_id.get(case_id)
        if case is None:
            raise ValueError(f"未知 Campaign 用例: {case_id}")
        _, indexed = self._executions()
        return self._readiness(case, indexed, self._active_phase(indexed))

    async def execute_case(
        self,
        case_id: str,
        *,
        allow_live_execution: bool = False,
    ) -> CaseExecutionResult:
        readiness = self.readiness(case_id)
        if readiness.state != "ready":
            raise CoordinatorBlockedError(f"{case_id} 不可执行: {readiness.reason}")
        if readiness.requires_live_execution and not allow_live_execution:
            raise CoordinatorBlockedError(
                f"{case_id} 需要显式 --allow-live-execution 才能发起北向 HTTP/WebSocket 负载"
            )

        expected_path = execution_path(self.release_root, self.plan, case_id)
        executor = self._executor_factory(self.plan, self.release_root)
        actual_path = await executor.execute(case_id)
        if actual_path.resolve() != expected_path.resolve():
            raise RuntimeError(f"用例执行器返回了非规范证据路径: {actual_path}")

        executions, indexed = self._executions()
        if len(indexed) != len(executions) or case_id not in indexed:
            raise RuntimeError(f"用例执行器没有发布可验证证据: {case_id}")
        return CaseExecutionResult(
            case_id=case_id,
            status=indexed[case_id].status,
            evidence_path=expected_path,
        )
