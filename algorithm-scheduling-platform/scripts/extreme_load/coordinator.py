from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, cast

from .catalog import CampaignPhase, CaseExecution, CaseSpec
from .execution import CampaignCaseExecutor
from .guardrails import GuardrailAssessment, GuardrailLevel
from .plan import (
    CampaignPlan,
    execution_path,
    read_case_evidence,
    read_case_executions,
    validate_release_executions,
)
from .report import atomic_write_report, validate_public_payload
from .stage_runtime import (
    STAGE_ADAPTER_NAMES,
    StageAdapterFactory,
    StageAdapterName,
    StageCaseAdapter,
    StageCaseOutcome,
    StageMetricsAdapter,
    guardrail_evidence,
    publish_stage_case_evidence,
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
_STAGE_ADAPTER_BY_KIND: Mapping[str, StageAdapterName] = {
    "media_download": "media_download",
    "face_photo_residue": "face_photo_residue",
    "mixed": "mixed",
    "single_operator_fault": "fault",
    "gpu_group_fault": "fault",
    "platform_fault": "fault",
    "middleware_fault": "fault",
    "soak": "soak",
}
_SERIAL_STAGE_ADAPTERS = frozenset({"media_download", "fault", "mixed", "soak"})
_ADAPTER_BLOCKERS: Mapping[StageAdapterName, str] = {
    "media_download": "需要显式 SSH/媒体下载探针适配器；本地协调器不会访问远端主机",
    "face_photo_residue": "需要显式 FaceRec 原图残留检查 SSH 探针适配器",
    "metrics": "需要显式实时主机指标适配器",
    "fault": "需要显式故障注入与恢复语义适配器",
    "mixed": "需要显式混合负载、实时指标和停止语义适配器",
    "soak": "需要显式长稳负载、实时指标和停止语义适配器",
}
_RUNTIME_SUMMARY_REQUIRED_KEYS = frozenset(
    {
        "sample_count",
        "gateway_delta",
        "gateway_instance_request_delta",
        "peak_inflight",
        "peak_active_leases",
        "peak_declared_capacity",
        "peak_gpu_utilization",
        "peak_gpu_memory_bytes",
        "minimum_target_filesystem_available_bytes",
        "target_directory_bytes_before",
        "target_directory_bytes_after",
        "target_directory_bytes_delta",
    }
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
        adapter_factories: Mapping[str, StageAdapterFactory] | None = None,
    ) -> None:
        _validate_catalog_order(plan)
        registered = dict(adapter_factories or {})
        unknown_adapters = sorted(set(registered) - STAGE_ADAPTER_NAMES)
        if unknown_adapters:
            raise ValueError("未知阶段适配器: " + ",".join(unknown_adapters))
        if any(not callable(factory) for factory in registered.values()):
            raise ValueError("阶段适配器 factory 必须可调用")
        self.plan = plan
        self.release_root = release_root
        self._executor_factory = executor_factory or CampaignCaseExecutor
        self._adapter_factories = registered
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

    @staticmethod
    def _stage_adapter_name(case: CaseSpec) -> StageAdapterName | None:
        return _STAGE_ADAPTER_BY_KIND.get(str(case.load.get("kind", "")))

    def _stage_sequence_blocker(
        self,
        case: CaseSpec,
        indexed: Mapping[str, CaseExecution],
        adapter_name: StageAdapterName,
    ) -> str | None:
        if adapter_name not in _SERIAL_STAGE_ADAPTERS:
            return None
        for candidate in self.plan.catalog.cases:
            if candidate.case_id == case.case_id:
                break
            if (
                candidate.phase is case.phase
                and self._stage_adapter_name(candidate) == adapter_name
            ):
                execution = indexed.get(candidate.case_id)
                if execution is None:
                    return f"阶段适配器必须逐案顺序执行，前案缺失: {candidate.case_id}"
                if execution.status != "passed":
                    return (
                        "阶段适配器前案未通过，禁止继续升级或注入故障: "
                        f"{candidate.case_id}={execution.status}"
                    )
        return None

    def _adapter_registration_blocker(self, adapter_name: StageAdapterName) -> str | None:
        required: tuple[StageAdapterName, ...] = (
            ("metrics",) if adapter_name == "metrics" else ("metrics", adapter_name)
        )
        missing = tuple(name for name in required if name not in self._adapter_factories)
        if not missing:
            return None
        return "；".join(_ADAPTER_BLOCKERS[name] for name in missing)

    @staticmethod
    def _requires_live_execution(kind: str) -> bool:
        return kind in _LIVE_EXECUTOR_KINDS or kind in _STAGE_ADAPTER_BY_KIND

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
                self._requires_live_execution(kind),
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
                self._requires_live_execution(kind),
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
                self._requires_live_execution(kind),
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
                self._requires_live_execution(kind),
            )

        adapter_name = self._stage_adapter_name(case)
        if adapter_name is not None:
            registration_blocker = self._adapter_registration_blocker(adapter_name)
            if registration_blocker is not None:
                return CaseReadiness(
                    case.case_id,
                    case.phase,
                    case.required,
                    kind,
                    "blocked",
                    registration_blocker,
                    case.prerequisites,
                    None,
                    True,
                )
            sequence_blocker = self._stage_sequence_blocker(case, indexed, adapter_name)
            if sequence_blocker is not None:
                return CaseReadiness(
                    case.case_id,
                    case.phase,
                    case.required,
                    kind,
                    "blocked",
                    sequence_blocker,
                    case.prerequisites,
                    None,
                    True,
                )
            return CaseReadiness(
                case.case_id,
                case.phase,
                case.required,
                kind,
                "ready",
                f"显式 {adapter_name} 阶段适配器与 metrics 护栏适配器已注册",
                case.prerequisites,
                None,
                True,
            )
        if kind in _LIVE_EXECUTOR_KINDS and "metrics" not in self._adapter_factories:
            return CaseReadiness(
                case.case_id,
                case.phase,
                case.required,
                kind,
                "blocked",
                _ADAPTER_BLOCKERS["metrics"],
                case.prerequisites,
                None,
                True,
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
            self._requires_live_execution(kind),
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
            integration_blockers=tuple(
                reason
                for adapter_name, reason in _ADAPTER_BLOCKERS.items()
                if adapter_name not in self._adapter_factories
            ),
        )

    def readiness(self, case_id: str) -> CaseReadiness:
        case = self._case_by_id.get(case_id)
        if case is None:
            raise ValueError(f"未知 Campaign 用例: {case_id}")
        _, indexed = self._executions()
        return self._readiness(case, indexed, self._active_phase(indexed))

    def _build_stage_adapter(self, adapter_name: StageAdapterName) -> StageCaseAdapter:
        instance = self._adapter_factories[adapter_name](self.plan, self.release_root)
        if not callable(getattr(instance, "execute", None)):
            raise TypeError(f"{adapter_name} factory 未返回阶段用例适配器")
        return cast(StageCaseAdapter, instance)

    def _build_metrics_adapter(self) -> StageMetricsAdapter:
        instance = self._build_stage_adapter("metrics")
        if not callable(getattr(instance, "assess", None)):
            raise TypeError("metrics factory 未返回护栏评估适配器")
        return cast(StageMetricsAdapter, instance)

    @staticmethod
    def _guardrail_blocked_outcome(
        assessment: GuardrailAssessment,
        checkpoint: str,
        adapter_outcome: StageCaseOutcome | None = None,
    ) -> StageCaseOutcome:
        evidence: dict[str, object] = {"guardrail_checkpoint": checkpoint}
        recovery_succeeded = None
        if adapter_outcome is not None:
            evidence["adapter_status_before_guardrail"] = adapter_outcome.status
            evidence["adapter_evidence_before_guardrail"] = dict(adapter_outcome.evidence)
            recovery_succeeded = adapter_outcome.recovery_succeeded
        return StageCaseOutcome(
            "blocked",
            f"{checkpoint} 护栏为 {assessment.level.value}，禁止继续升级: "
            + "；".join(assessment.reasons),
            evidence,
            recovery_succeeded=recovery_succeeded,
        )

    @staticmethod
    def _enforce_fault_recovery(outcome: StageCaseOutcome) -> StageCaseOutcome:
        if outcome.recovery_succeeded is False:
            return StageCaseOutcome(
                "failed",
                f"{outcome.reason}；精确恢复失败，禁止后续故障",
                outcome.evidence,
                recovery_succeeded=False,
            )
        if outcome.status == "passed" and outcome.recovery_succeeded is not True:
            return StageCaseOutcome(
                "failed",
                "故障适配器未提供 recovery_succeeded=true 的精确恢复证据",
                outcome.evidence,
                recovery_succeeded=outcome.recovery_succeeded,
            )
        return outcome

    async def _execute_stage_case(
        self,
        case: CaseSpec,
        adapter_name: StageAdapterName,
    ) -> CaseExecutionResult:
        started_at = datetime.now(UTC).isoformat()
        started = time.perf_counter()
        before: GuardrailAssessment | None = None
        after: GuardrailAssessment | None = None
        metrics: StageMetricsAdapter | None = None
        try:
            metrics = self._build_metrics_adapter()
            before = await metrics.assess(case, "before")
            if not isinstance(before, GuardrailAssessment):
                raise TypeError("metrics adapter 返回了非法前置护栏评估")
            if before.level is not GuardrailLevel.CLEAR:
                outcome = self._guardrail_blocked_outcome(before, "前置")
            else:
                adapter = metrics if adapter_name == "metrics" else self._build_stage_adapter(
                    adapter_name
                )
                outcome = await adapter.execute(case)
                if not isinstance(outcome, StageCaseOutcome):
                    raise TypeError(f"{adapter_name} adapter 返回了非法用例结果")
                if adapter_name == "fault":
                    outcome = self._enforce_fault_recovery(outcome)
                after = await metrics.assess(case, "after")
                if not isinstance(after, GuardrailAssessment):
                    raise TypeError("metrics adapter 返回了非法后置护栏评估")
                if outcome.status == "passed" and after.level is not GuardrailLevel.CLEAR:
                    outcome = self._guardrail_blocked_outcome(after, "后置", outcome)
        except Exception as error:
            if metrics is not None and before is not None and after is None:
                try:
                    after_candidate = await metrics.assess(case, "after")
                    if isinstance(after_candidate, GuardrailAssessment):
                        after = after_candidate
                except Exception:
                    pass
            outcome = StageCaseOutcome(
                "failed",
                f"阶段适配器执行异常: {type(error).__name__}",
                {"error_type": type(error).__name__},
            )

        evidence_path = publish_stage_case_evidence(
            release_root=self.release_root,
            plan=self.plan,
            case=case,
            adapter_name=adapter_name,
            outcome=outcome,
            guardrail_before=before,
            guardrail_after=after,
            started_at=started_at,
            completed_at=datetime.now(UTC).isoformat(),
            elapsed_seconds=time.perf_counter() - started,
        )
        executions, indexed = self._executions()
        if len(indexed) != len(executions) or case.case_id not in indexed:
            raise RuntimeError(f"阶段适配器没有发布可验证证据: {case.case_id}")
        return CaseExecutionResult(
            case_id=case.case_id,
            status=indexed[case.case_id].status,
            evidence_path=evidence_path,
        )

    def _observed_executor_plan(self, case: CaseSpec) -> tuple[CampaignPlan, CaseSpec]:
        business_evidence_path = f"campaign/runtime-metrics/{case.case_id}/business-case.json"
        observed_case = case.model_copy(update={"evidence_path": business_evidence_path})
        observed_catalog = self.plan.catalog.model_copy(
            update={
                "cases": tuple(
                    observed_case if item.case_id == case.case_id else item
                    for item in self.plan.catalog.cases
                )
            }
        )
        return self.plan.model_copy(update={"catalog": observed_catalog}), observed_case

    def _publish_observed_live_evidence(
        self,
        *,
        case: CaseSpec,
        business_document: Mapping[str, object],
        business_evidence_path: str | None,
        before: GuardrailAssessment,
        after: GuardrailAssessment | None,
        metrics_outcome: StageCaseOutcome,
    ) -> Path:
        business_status = business_document.get("status")
        business_reason = business_document.get("reason")
        if business_status not in {"passed", "failed", "blocked", "not_run"}:
            raise ValueError("业务用例证据状态不合法")
        if not isinstance(business_reason, str) or not business_reason:
            raise ValueError("业务用例证据原因不合法")
        final_status = business_status
        final_reason = business_reason
        if business_status == "passed" and metrics_outcome.status != "passed":
            final_status = "failed" if metrics_outcome.status == "failed" else "blocked"
            final_reason = "运行时观测未通过，用例不得保持 passed: " + metrics_outcome.reason
        elif (
            business_status == "passed"
            and after is not None
            and after.level is not GuardrailLevel.CLEAR
        ):
            final_status = "blocked"
            final_reason = f"后置护栏为 {after.level.value}，用例不得保持 passed: " + "；".join(
                after.reasons
            )
        document = dict(business_document)
        document.update(
            {
                "status": final_status,
                "reason": final_reason,
                "business_status": business_status,
                "business_reason": business_reason,
                "business_evidence_path": business_evidence_path,
                "guardrail_before": guardrail_evidence(before),
                "guardrail_after": guardrail_evidence(after),
                "runtime_observability": {
                    **dict(metrics_outcome.evidence),
                    "status": metrics_outcome.status,
                    "reason": metrics_outcome.reason,
                },
            }
        )
        validate_public_payload(document)
        output = execution_path(self.release_root, self.plan, case.case_id)
        atomic_write_report(
            output,
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        read_case_evidence(self.release_root, self.plan, case)
        return output

    @staticmethod
    async def _finalize_runtime_observability(
        metrics: StageMetricsAdapter,
        case: CaseSpec,
    ) -> StageCaseOutcome:
        try:
            outcome = await metrics.execute(case)
            if not isinstance(outcome, StageCaseOutcome):
                raise TypeError("metrics adapter 返回了非法指标证据")
            if outcome.status == "passed":
                summary = outcome.evidence.get("runtime_metrics")
                sample_evidence = outcome.evidence.get("sample_evidence")
                if not isinstance(summary, Mapping):
                    raise ValueError("运行时指标通过结果缺少 summary")
                missing = sorted(_RUNTIME_SUMMARY_REQUIRED_KEYS - set(summary))
                if missing:
                    raise ValueError("运行时指标 summary 缺少字段: " + ",".join(missing))
                sample_count = summary.get("sample_count")
                if (
                    type(sample_count) is not int
                    or sample_count <= 0
                    or type(sample_evidence) is not list
                    or len(sample_evidence) != sample_count
                ):
                    raise ValueError("运行时指标样本数量与证据路径不一致")
                prefix = f"campaign/runtime-metrics/{case.case_id}/"
                if any(
                    not isinstance(path, str)
                    or not path.startswith(prefix)
                    or not path.endswith(".json")
                    or ".." in Path(path).parts
                    for path in sample_evidence
                ):
                    raise ValueError("运行时指标样本证据路径不属于当前用例")
            return outcome
        except Exception as error:
            return StageCaseOutcome(
                "failed",
                f"运行时指标汇总失败: {type(error).__name__}",
                {"error_type": type(error).__name__},
            )

    async def _execute_live_case(self, case: CaseSpec) -> CaseExecutionResult:
        if "metrics" not in self._adapter_factories:
            raise CoordinatorBlockedError(_ADAPTER_BLOCKERS["metrics"])
        started_at = datetime.now(UTC).isoformat()
        started = time.perf_counter()
        metrics = self._build_metrics_adapter()
        before = await metrics.assess(case, "before")
        if not isinstance(before, GuardrailAssessment):
            raise TypeError("metrics adapter 返回了非法前置护栏评估")
        if before.level is not GuardrailLevel.CLEAR:
            outcome = self._guardrail_blocked_outcome(before, "前置")
            metrics_outcome = await self._finalize_runtime_observability(metrics, case)
            evidence_path = self._publish_observed_live_evidence(
                case=case,
                business_document={
                    "schema_version": 1,
                    "evidence_type": "extreme_load_campaign_case",
                    "campaign_id": self.plan.campaign_id,
                    "release_tag": self.plan.release_tag,
                    "git_sha": self.plan.git_sha,
                    "case_id": case.case_id,
                    "phase": case.phase.value,
                    "status": outcome.status,
                    "reason": outcome.reason,
                    "started_at": started_at,
                    "completed_at": datetime.now(UTC).isoformat(),
                    "elapsed_seconds": time.perf_counter() - started,
                },
                business_evidence_path=None,
                before=before,
                after=None,
                metrics_outcome=metrics_outcome,
            )
            return CaseExecutionResult(case.case_id, "blocked", evidence_path)

        observed_plan, observed_case = self._observed_executor_plan(case)
        expected_business_path = execution_path(
            self.release_root,
            observed_plan,
            case.case_id,
        )
        executor = self._executor_factory(observed_plan, self.release_root)
        execution_error: Exception | None = None
        business_document: Mapping[str, object] | None = None
        try:
            actual_path = await executor.execute(case.case_id)
            if actual_path.resolve() != expected_business_path.resolve():
                raise RuntimeError("用例执行器返回了非观测业务证据路径")
            business_document = read_case_evidence(
                self.release_root,
                observed_plan,
                observed_case,
            )
        except Exception as error:
            execution_error = error

        after = await metrics.assess(case, "after")
        if not isinstance(after, GuardrailAssessment):
            raise TypeError("metrics adapter 返回了非法后置护栏评估")
        metrics_outcome = await self._finalize_runtime_observability(metrics, case)
        if execution_error is not None or business_document is None:
            error_type = type(execution_error).__name__
            evidence_path = self._publish_observed_live_evidence(
                case=case,
                business_document={
                    "schema_version": 1,
                    "evidence_type": "extreme_load_campaign_case",
                    "campaign_id": self.plan.campaign_id,
                    "release_tag": self.plan.release_tag,
                    "git_sha": self.plan.git_sha,
                    "case_id": case.case_id,
                    "phase": case.phase.value,
                    "status": "failed",
                    "reason": f"用例执行器异常: {error_type}",
                    "error_type": error_type,
                    "started_at": started_at,
                    "completed_at": datetime.now(UTC).isoformat(),
                    "elapsed_seconds": time.perf_counter() - started,
                },
                business_evidence_path=None,
                before=before,
                after=after,
                metrics_outcome=metrics_outcome,
            )
        else:
            evidence_path = self._publish_observed_live_evidence(
                case=case,
                business_document=business_document,
                business_evidence_path=observed_case.evidence_path,
                before=before,
                after=after,
                metrics_outcome=metrics_outcome,
            )
        _, indexed = self._executions()
        if case.case_id not in indexed:
            raise RuntimeError(f"观测用例没有发布可验证证据: {case.case_id}")
        return CaseExecutionResult(
            case_id=case.case_id,
            status=indexed[case.case_id].status,
            evidence_path=evidence_path,
        )

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
                f"{case_id} 需要显式 --allow-live-execution 才能发起北向或阶段适配器执行"
            )

        case = self._case_by_id[case_id]
        adapter_name = self._stage_adapter_name(case)
        if adapter_name is not None:
            return await self._execute_stage_case(case, adapter_name)

        kind = str(case.load.get("kind", ""))
        if kind in _LIVE_EXECUTOR_KINDS:
            return await self._execute_live_case(case)

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
