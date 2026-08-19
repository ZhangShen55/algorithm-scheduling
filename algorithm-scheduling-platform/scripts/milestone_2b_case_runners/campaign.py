from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.aggregate_milestone_2b_cases import (
    _load_release_json_with_metadata,
    _release_source_metadata,
    publish_json_once,
)
from scripts.milestone_2b_case_catalog import CaseDefinition
from scripts.milestone_2b_report_contract import DECLARATION_CATEGORY_BY_CASE_ID

from .base import CaseContext, CaseOutcome
from .evidence import publish_case_evidence, release_identity

CAMPAIGN_EVIDENCE_NAME = "campaign.json"
CAMPAIGN_PHASES = frozenset({"offline", "vision", "online", "final"})


def publish_campaign_case(
    *,
    release_root: Path,
    case_id: str,
    phase: str,
    status: str,
    reason: str,
    observed: Mapping[str, Any],
    artifacts: Sequence[str] = (),
    cleanup: Mapping[str, Any] | None = None,
) -> Path:
    """Publish one immutable, real campaign result before case attestation."""
    category = DECLARATION_CATEGORY_BY_CASE_ID.get(case_id)
    if category not in {"negative", "load"}:
        raise ValueError(f"campaign case is not in the authoritative catalog: {case_id}")
    if phase not in CAMPAIGN_PHASES:
        raise ValueError(f"campaign phase is invalid: {phase}")
    if status not in {"通过", "失败"}:
        raise ValueError("campaign status must be 通过 or 失败")
    if type(reason) is not str or not reason.strip():
        raise ValueError("campaign reason must be a non-empty string")
    observed_copy = _plain_mapping(observed, "observed")
    if not observed_copy:
        raise ValueError("campaign observed facts must not be empty")
    artifact_paths = [_release_relative_path(path, "artifact") for path in artifacts]
    cleanup_copy = _plain_mapping(
        cleanup
        or {
            "status": "clean",
            "residual_resources": [],
        },
        "cleanup",
    )
    release_tag, git_sha = release_identity(release_root)
    relative_path = (
        Path(category) / "evidence" / case_id / CAMPAIGN_EVIDENCE_NAME
    )
    publish_json_once(
        release_root=release_root,
        relative_path=relative_path,
        document={
            "schema_version": 1,
            "evidence_type": "milestone_2b_campaign_case",
            "case_id": case_id,
            "category": category,
            "phase": phase,
            "status": status,
            "executed": True,
            "mock": False,
            "release_tag": release_tag,
            "git_sha": git_sha,
            "reason": reason,
            "observed": observed_copy,
            "artifacts": artifact_paths,
            "cleanup": cleanup_copy,
        },
    )
    return relative_path


@dataclass(frozen=True, slots=True)
class CampaignCaseRunner:
    phase: str
    case_id: str

    def __post_init__(self) -> None:
        if self.phase not in CAMPAIGN_PHASES:
            raise ValueError(f"campaign phase is invalid: {self.phase}")
        if self.case_id not in DECLARATION_CATEGORY_BY_CASE_ID:
            raise ValueError(f"campaign case is not authoritative: {self.case_id}")

    async def run(
        self,
        context: CaseContext,
        case: CaseDefinition,
    ) -> CaseOutcome:
        document, source_path = _load_and_validate(
            context=context,
            case=case,
            expected_phase=self.phase,
            expected_case_id=self.case_id,
        )
        source_digest = hashlib.sha256(
            json.dumps(
                document,
                allow_nan=False,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        attestation = publish_case_evidence(
            context=context,
            case=case,
            name="campaign-attestation.json",
            payload={
                "event": "campaign_case_attested",
                "phase": self.phase,
                "campaign_evidence": source_path.as_posix(),
                "campaign_sha256": source_digest,
                "observed": document["observed"],
                "artifacts": document["artifacts"],
                "cleanup": document["cleanup"],
            },
        )
        return CaseOutcome("通过", str(document["reason"]), (source_path, attestation))

    async def cleanup(
        self,
        context: CaseContext,
        case: CaseDefinition,
    ) -> None:
        document, _ = _load_and_validate(
            context=context,
            case=case,
            expected_phase=self.phase,
            expected_case_id=self.case_id,
        )
        cleanup = document["cleanup"]
        if cleanup.get("status") != "clean":
            raise ValueError(f"{self.case_id} campaign cleanup is not clean")
        residue = cleanup.get("residual_resources")
        if not isinstance(residue, list) or residue:
            raise ValueError(f"{self.case_id} campaign cleanup has residue")


def validate_campaign_source_evidence(
    *,
    release_root: Path,
    case: CaseDefinition,
    source_path: Path,
) -> None:
    """Validate a campaign source when the batch attests outcome evidence."""
    expected_path = (
        Path(case.category) / "evidence" / case.case_id / CAMPAIGN_EVIDENCE_NAME
    )
    if source_path != expected_path:
        raise ValueError(f"{case.case_id} campaign evidence path changed")
    context = CaseContext(
        release_root=release_root,
        run_id="campaign-validation",
        target="local-validation",
    )
    _load_and_validate(
        context=context,
        case=case,
        expected_phase=case.phase,
        expected_case_id=case.case_id,
    )


def _load_and_validate(
    *,
    context: CaseContext,
    case: CaseDefinition,
    expected_phase: str,
    expected_case_id: str,
) -> tuple[dict[str, Any], Path]:
    if case.case_id != expected_case_id or case.phase != expected_phase:
        raise ValueError(f"campaign runner contract changed: {expected_case_id}")
    category = DECLARATION_CATEGORY_BY_CASE_ID.get(expected_case_id)
    if category != case.category:
        raise ValueError(f"campaign category changed: {expected_case_id}")
    relative_path = (
        Path(case.category)
        / "evidence"
        / expected_case_id
        / CAMPAIGN_EVIDENCE_NAME
    )
    document, _metadata = _load_release_json_with_metadata(
        context.release_root,
        relative_path,
    )
    release_tag, git_sha = release_identity(context.release_root)
    expected_identity = {
        "schema_version": 1,
        "evidence_type": "milestone_2b_campaign_case",
        "case_id": expected_case_id,
        "category": case.category,
        "phase": expected_phase,
        "status": "通过",
        "executed": True,
        "mock": False,
        "release_tag": release_tag,
        "git_sha": git_sha,
    }
    for field, expected in expected_identity.items():
        if document.get(field) != expected:
            raise ValueError(
                f"{expected_case_id} campaign field {field} does not match"
            )
    reason = document.get("reason")
    if type(reason) is not str or not reason.strip():
        raise ValueError(f"{expected_case_id} campaign reason is missing")
    observed = document.get("observed")
    if not isinstance(observed, dict) or not observed:
        raise ValueError(f"{expected_case_id} campaign observed facts are missing")
    _validate_case_check(observed, case)
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError(f"{expected_case_id} campaign artifacts must be a list")
    for artifact in artifacts:
        artifact_path = Path(_release_relative_path(artifact, "artifact"))
        _release_source_metadata(context.release_root, artifact_path)
    cleanup = document.get("cleanup")
    if not isinstance(cleanup, dict):
        raise ValueError(f"{expected_case_id} campaign cleanup is missing")
    allowed_fields = {
        "schema_version",
        "evidence_type",
        "case_id",
        "category",
        "phase",
        "status",
        "executed",
        "mock",
        "release_tag",
        "git_sha",
        "reason",
        "observed",
        "artifacts",
        "cleanup",
    }
    extra = sorted(set(document) - allowed_fields)
    if extra:
        raise ValueError(f"{expected_case_id} campaign has extra fields: {extra}")
    return document, relative_path


def _validate_case_check(observed: dict[str, Any], case: CaseDefinition) -> None:
    expected_fields = {
        "check_id",
        "method",
        "case_title",
        "expected",
        "runtime_probe",
        "related_passed_testcases",
        "assertions",
        "manual_review",
    }
    if set(observed) != expected_fields:
        raise ValueError(f"{case.case_id} campaign check fields are incomplete")
    if (
        observed.get("check_id") != f"business-case-{case.case_id.lower()}"
        or observed.get("case_title") != case.title
        or observed.get("expected") != case.expected
        or observed.get("method")
        not in {
            "real-runtime-and-targeted-regression",
            "real-runtime-targeted-regression-and-manual-review",
        }
    ):
        raise ValueError(f"{case.case_id} campaign check identity changed")
    runtime_probe = observed.get("runtime_probe")
    related = observed.get("related_passed_testcases")
    assertions = observed.get("assertions")
    if (
        not isinstance(runtime_probe, dict)
        or not runtime_probe
        or not isinstance(related, list)
        or not related
        or not all(type(test) is str and test.strip() for test in related)
        or len(related) != len(set(related))
        or not isinstance(assertions, list)
        or not assertions
    ):
        raise ValueError(f"{case.case_id} campaign check is not attributable")
    for assertion in assertions:
        if not isinstance(assertion, dict) or assertion.get("passed") is not True:
            raise ValueError(f"{case.case_id} campaign assertion did not pass")
    manual_review = observed.get("manual_review")
    if observed["method"].endswith("manual-review"):
        if (
            not isinstance(manual_review, dict)
            or manual_review.get("status") != "通过"
            or not isinstance(manual_review.get("observed"), dict)
            or not manual_review["observed"]
        ):
            raise ValueError(f"{case.case_id} campaign manual review is missing")
    elif manual_review is not None:
        raise ValueError(f"{case.case_id} unexpected campaign manual review")


def _release_relative_path(value: object, label: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"campaign {label} path must be a non-empty string")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"campaign {label} path escapes the release root")
    return path.as_posix()


def _plain_mapping(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise ValueError(f"campaign {label} must be a plain string-keyed object")
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    loaded = json.loads(encoded)
    if type(loaded) is not dict:
        raise ValueError(f"campaign {label} must be an object")
    return loaded
