from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from .catalog import CaseSpec
from .guardrails import GuardrailAssessment
from .plan import CampaignPlan, execution_path, read_case_evidence
from .report import atomic_write_report, validate_public_payload

StageAdapterName = Literal[
    "media_download",
    "face_photo_residue",
    "metrics",
    "fault",
    "mixed",
    "soak",
]
StageCheckpoint = Literal["before", "after"]
StageCaseStatus = Literal["passed", "failed", "blocked", "not_run"]

STAGE_ADAPTER_NAMES = frozenset(
    {"media_download", "face_photo_residue", "metrics", "fault", "mixed", "soak"}
)


@dataclass(frozen=True, slots=True)
class StageCaseOutcome:
    status: StageCaseStatus
    reason: str
    evidence: Mapping[str, object]
    recovery_succeeded: bool | None = None

    def __post_init__(self) -> None:
        if self.status not in {"passed", "failed", "blocked", "not_run"}:
            raise ValueError("阶段适配器结果状态不合法")
        if not self.reason:
            raise ValueError("阶段适配器结果原因不能为空")
        if self.recovery_succeeded is not None and type(self.recovery_succeeded) is not bool:
            raise ValueError("recovery_succeeded 必须是布尔值或 null")
        validate_public_payload(self.evidence)


class StageCaseAdapter(Protocol):
    async def execute(self, case: CaseSpec) -> StageCaseOutcome: ...


class StageMetricsAdapter(StageCaseAdapter, Protocol):
    async def assess(
        self,
        case: CaseSpec,
        checkpoint: StageCheckpoint,
    ) -> GuardrailAssessment: ...


StageAdapterFactory = Callable[[CampaignPlan, Path], object]


def guardrail_evidence(assessment: GuardrailAssessment | None) -> dict[str, object] | None:
    if assessment is None:
        return None
    return {
        "level": assessment.level.value,
        "reasons": list(assessment.reasons),
    }


def publish_stage_case_evidence(
    *,
    release_root: Path,
    plan: CampaignPlan,
    case: CaseSpec,
    adapter_name: StageAdapterName,
    outcome: StageCaseOutcome,
    guardrail_before: GuardrailAssessment | None,
    guardrail_after: GuardrailAssessment | None,
    started_at: str,
    completed_at: str,
    elapsed_seconds: float,
) -> Path:
    if elapsed_seconds < 0:
        raise ValueError("阶段执行耗时不能为负")
    output = execution_path(release_root, plan, case.case_id)
    document: dict[str, object] = {
        "schema_version": 1,
        "evidence_type": "extreme_load_campaign_case",
        "campaign_id": plan.campaign_id,
        "release_tag": plan.release_tag,
        "git_sha": plan.git_sha,
        "case_id": case.case_id,
        "phase": case.phase.value,
        "status": outcome.status,
        "reason": outcome.reason,
        "adapter": adapter_name,
        "started_at": started_at,
        "completed_at": completed_at,
        "elapsed_seconds": elapsed_seconds,
        "guardrail_before": guardrail_evidence(guardrail_before),
        "guardrail_after": guardrail_evidence(guardrail_after),
        "recovery_succeeded": outcome.recovery_succeeded,
        "adapter_evidence": dict(outcome.evidence),
    }
    validate_public_payload(document)
    atomic_write_report(
        output,
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    read_case_evidence(release_root, plan, case)
    return output
