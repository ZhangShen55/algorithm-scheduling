from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from scripts.milestone_2b_case_catalog import CaseDefinition

CaseStatus = Literal["通过", "失败"]
RUN_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")


@dataclass(frozen=True, slots=True)
class CaseContext:
    release_root: Path
    run_id: str
    target: str

    def __post_init__(self) -> None:
        if not self.release_root.is_absolute():
            raise ValueError("release_root must be absolute")
        if (
            type(self.run_id) is not str
            or RUN_ID_PATTERN.fullmatch(self.run_id) is None
        ):
            raise ValueError("run_id must be a safe lowercase namespace")
        if type(self.target) is not str or not self.target.strip():
            raise ValueError("target must not be empty")


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    status: CaseStatus
    reason: str
    evidence: Sequence[Path]

    def __post_init__(self) -> None:
        if type(self.status) is not str or self.status not in {"通过", "失败"}:
            raise ValueError(
                "case outcome status must be the plain string 通过 or 失败"
            )
        if type(self.reason) is not str or not self.reason.strip():
            raise ValueError("case outcome reason must be a non-empty plain string")
        try:
            evidence_snapshot = tuple(self.evidence)
        except TypeError as exc:
            raise ValueError("case outcome evidence must be a sequence") from exc
        object.__setattr__(self, "evidence", evidence_snapshot)


class CaseRunner(Protocol):
    async def run(
        self, context: CaseContext, case: CaseDefinition
    ) -> CaseOutcome: ...
