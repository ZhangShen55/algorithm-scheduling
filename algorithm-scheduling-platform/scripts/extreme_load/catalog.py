from __future__ import annotations

from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CampaignPhase(StrEnum):
    BASELINE = "baseline"
    OFFLINE = "offline"
    CONTROL_QUERY = "control-query"
    ONLINE = "online"
    MIXED = "mixed"
    RECOVERY = "recovery"
    SOAK = "soak"

    @property
    def sequence(self) -> int:
        return list(CampaignPhase).index(self)


class FixtureKind(StrEnum):
    SHORT_TEACHER_VIDEO = "short_teacher_video"
    SHORT_STUDENT_VIDEO = "short_student_video"
    SHORT_SLIDES_VIDEO = "short_slides_video"
    LONG_COURSE = "long_course"
    ONLINE_IMAGE = "online_image"
    REALTIME_AUDIO = "realtime_audio"
    PERSON_PHOTO = "person_photo"


class FixtureDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    fixture_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_-]+$")
    kind: FixtureKind
    path: str = Field(min_length=1)
    size_bytes: int = Field(gt=0)
    duration_seconds: float | None = Field(default=None, gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("kind", mode="before")
    @classmethod
    def parse_kind(cls, value: object) -> object:
        return FixtureKind(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def metadata_path_only(self) -> Self:
        parsed = urlsplit(self.path)
        remote = parsed.scheme in {"http", "https"} and bool(parsed.netloc)
        local = Path(self.path).is_absolute()
        if not (remote or local) or self.path.startswith("data:"):
            raise ValueError("fixture path 必须是外部绝对路径或 HTTP/HTTPS URL")
        duration_required = self.kind in {
            FixtureKind.SHORT_TEACHER_VIDEO,
            FixtureKind.SHORT_STUDENT_VIDEO,
            FixtureKind.SHORT_SLIDES_VIDEO,
            FixtureKind.LONG_COURSE,
            FixtureKind.REALTIME_AUDIO,
        }
        if duration_required and self.duration_seconds is None:
            raise ValueError("视频和实时音频 fixture 必须记录时长")
        return self


class FixtureManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: int = Field(gt=0)
    fixtures: tuple[FixtureDescriptor, ...] = Field(min_length=1)

    @field_validator("fixtures", mode="before")
    @classmethod
    def freeze_fixtures(cls, value: object) -> object:
        # Catalog files naturally decode arrays as lists; freeze them after parsing.
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def fixture_ids_unique(self) -> Self:
        ids = [item.fixture_id for item in self.fixtures]
        if len(ids) != len(set(ids)):
            raise ValueError("fixture ID 重复")
        return self


def external_fixture_manifest_template() -> dict[str, object]:
    canonical = (
        ("short-teacher", FixtureKind.SHORT_TEACHER_VIDEO, "<seconds>"),
        ("short-student", FixtureKind.SHORT_STUDENT_VIDEO, "<seconds>"),
        ("short-slides", FixtureKind.SHORT_SLIDES_VIDEO, "<seconds>"),
        ("long-teacher", FixtureKind.LONG_COURSE, "<2700-3600 seconds>"),
        ("long-student", FixtureKind.LONG_COURSE, "<2700-3600 seconds>"),
        ("long-slides", FixtureKind.LONG_COURSE, "<2700-3600 seconds>"),
        ("online-image", FixtureKind.ONLINE_IMAGE, None),
        ("realtime-audio", FixtureKind.REALTIME_AUDIO, "<seconds>"),
        ("person-photo", FixtureKind.PERSON_PHOTO, None),
    )
    entries: list[dict[str, object]] = []
    for fixture_id, kind, duration in canonical:
        entries.append(
            {
                "fixture_id": fixture_id,
                "kind": kind.value,
                "path": "<absolute external path or HTTP/HTTPS URL>",
                "size_bytes": "<integer>",
                "duration_seconds": duration,
                "sha256": "<64 lowercase hex characters>",
            }
        )
    return {"schema_version": 1, "fixtures": entries}


class LoadProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    unique_submission_bursts: tuple[int, ...]
    idempotent_submission_bursts: tuple[int, ...]
    long_course_concurrency: tuple[int, ...]
    image_concurrency: tuple[int, ...]
    realtime_asr_sessions: tuple[int, ...]
    query_qps: tuple[int, ...]
    soak_hours: tuple[int, ...]

    @field_validator(
        "unique_submission_bursts",
        "idempotent_submission_bursts",
        "long_course_concurrency",
        "image_concurrency",
        "realtime_asr_sessions",
        "query_qps",
        "soak_hours",
        mode="before",
    )
    @classmethod
    def freeze_levels(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def levels_are_positive_and_increasing(self) -> Self:
        for field_name in (
            "unique_submission_bursts",
            "idempotent_submission_bursts",
            "long_course_concurrency",
            "image_concurrency",
            "realtime_asr_sessions",
            "query_qps",
            "soak_hours",
        ):
            values = getattr(self, field_name)
            if not values or any(value <= 0 for value in values):
                raise ValueError(f"{field_name} 必须包含正数档位")
            if tuple(sorted(set(values))) != values:
                raise ValueError(f"{field_name} 必须严格递增且不重复")
        return self


def default_load_profile() -> LoadProfile:
    return LoadProfile(
        unique_submission_bursts=(100, 300, 1000),
        idempotent_submission_bursts=(30, 100, 300, 1000),
        long_course_concurrency=(3, 6, 12, 24, 36),
        image_concurrency=(1, 3, 10, 30, 60, 100, 256, 512, 1000),
        realtime_asr_sessions=(1, 10, 24, 30, 60, 90, 150),
        query_qps=(50, 100, 300, 1000),
        soak_hours=(4, 8),
    )


class CaseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    case_id: str = Field(pattern=r"^[A-Z0-9_-]+$")
    phase: CampaignPhase
    required: bool = True
    prerequisites: tuple[str, ...] = ()
    load: dict[str, object] = Field(min_length=1)
    fixture_ids: tuple[str, ...] = Field(min_length=1)
    expected: str = Field(min_length=1)
    timeout_seconds: float = Field(gt=0)
    guardrails: tuple[str, ...] = Field(min_length=1)
    cleanup: tuple[str, ...] = Field(min_length=1)
    evidence_path: str = Field(min_length=1)

    @field_validator("phase", mode="before")
    @classmethod
    def parse_phase(cls, value: object) -> object:
        return CampaignPhase(value) if isinstance(value, str) else value

    @field_validator(
        "prerequisites",
        "fixture_ids",
        "guardrails",
        "cleanup",
        mode="before",
    )
    @classmethod
    def freeze_string_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def safe_evidence_path(self) -> Self:
        path = PurePosixPath(self.evidence_path)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("证据路径必须位于 release 相对目录")
        return self


class CampaignCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: int = Field(gt=0)
    cases: tuple[CaseSpec, ...] = Field(min_length=1)

    @field_validator("cases", mode="before")
    @classmethod
    def freeze_cases(cls, value: object) -> object:
        # Keep the public schema JSON/YAML friendly while exposing immutable cases.
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_graph(self) -> Self:
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("用例 ID 重复")
        known = set(ids)
        graph = {case.case_id: set(case.prerequisites) for case in self.cases}
        unknown = sorted({item for values in graph.values() for item in values} - known)
        if unknown:
            raise ValueError(f"存在未知前置: {', '.join(unknown)}")
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(case_id: str) -> None:
            if case_id in visiting:
                raise ValueError("用例存在循环依赖")
            if case_id in visited:
                return
            visiting.add(case_id)
            for prerequisite in graph[case_id]:
                visit(prerequisite)
            visiting.remove(case_id)
            visited.add(case_id)

        for case_id in ids:
            visit(case_id)
        return self


class CaseExecution(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    case_id: str
    status: Literal["passed", "failed", "blocked", "not_run"]
    evidence_path: str

    @model_validator(mode="after")
    def safe_evidence_path(self) -> Self:
        if not self.evidence_path:
            return self
        path = PurePosixPath(self.evidence_path)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("执行证据路径必须位于 release 相对目录")
        return self


class CatalogVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: bool
    missing: tuple[str, ...] = ()
    duplicate: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    unexpected: tuple[str, ...] = ()
    evidence_mismatch: tuple[str, ...] = ()


def validate_case_executions(
    catalog: CampaignCatalog,
    executions: tuple[CaseExecution, ...] | list[CaseExecution],
) -> CatalogVerdict:
    known = {case.case_id for case in catalog.cases}
    required = {case.case_id for case in catalog.cases if case.required}
    expected_evidence = {case.case_id: case.evidence_path for case in catalog.cases}
    counts: dict[str, int] = {}
    for execution in executions:
        counts[execution.case_id] = counts.get(execution.case_id, 0) + 1
    missing = tuple(sorted(required - set(counts)))
    duplicate = tuple(
        sorted(
            case_id
            for case_id, count in counts.items()
            if case_id in known and count > 1
        )
    )
    failed = tuple(
        sorted(
            {
                execution.case_id
                for execution in executions
                if execution.case_id in known and execution.status != "passed"
            }
        )
    )
    missing_evidence = tuple(
        sorted(
            {
                execution.case_id
                for execution in executions
                if execution.case_id in known and not execution.evidence_path.strip()
            }
        )
    )
    unexpected = tuple(sorted(set(counts) - known))
    evidence_mismatch = tuple(
        sorted(
            {
                execution.case_id
                for execution in executions
                if execution.case_id in known
                and execution.evidence_path
                and execution.evidence_path != expected_evidence[execution.case_id]
            }
        )
    )
    return CatalogVerdict(
        passed=not (
            missing
            or duplicate
            or failed
            or missing_evidence
            or unexpected
            or evidence_mismatch
        ),
        missing=missing,
        duplicate=duplicate,
        failed=failed,
        missing_evidence=missing_evidence,
        unexpected=unexpected,
        evidence_mismatch=evidence_mismatch,
    )


_SHORT_FIXTURES = ("short-teacher", "short-student", "short-slides")
_LONG_FIXTURES = ("long-teacher", "long-student", "long-slides")


def _catalog_case(
    phase: CampaignPhase,
    case_id: str,
    *,
    load: dict[str, object],
    fixture_ids: tuple[str, ...],
    prerequisites: tuple[str, ...] = (),
    timeout_seconds: float = 3_600,
    required: bool = True,
) -> CaseSpec:
    return CaseSpec(
        case_id=case_id,
        phase=phase,
        required=required,
        prerequisites=prerequisites,
        load=load,
        fixture_ids=fixture_ids,
        expected=f"{case_id} 满足业务、容量、恢复和证据合同",
        timeout_seconds=timeout_seconds,
        guardrails=("disk", "gpu", "container", "database", "evidence"),
        cleanup=("stop_new_load", "drain_accepted_work", "release_leases"),
        evidence_path=f"campaign/phase-{phase.sequence}-{phase.value}/{case_id.lower()}.json",
    )


def _phase_gate(
    phase: CampaignPhase,
    case_ids: tuple[str, ...],
    *,
    prerequisite_gate: str | None,
) -> CaseSpec:
    gate_id = f"PHASE-{phase.sequence}-COMPLETE"
    prerequisites = (*(() if prerequisite_gate is None else (prerequisite_gate,)), *case_ids)
    return _catalog_case(
        phase,
        gate_id,
        load={"kind": "phase_gate", "phase": phase.value},
        fixture_ids=("external-fixture-manifest",),
        prerequisites=prerequisites,
    )


def _append_phase(
    catalog: list[CaseSpec],
    phase_cases: list[CaseSpec],
    *,
    prerequisite_gate: str | None,
) -> str:
    if not phase_cases:
        raise ValueError("阶段至少需要一个真实用例")
    for item in phase_cases:
        if prerequisite_gate is not None and prerequisite_gate not in item.prerequisites:
            item = item.model_copy(
                update={"prerequisites": (prerequisite_gate, *item.prerequisites)}
            )
        catalog.append(item)
    required_ids = tuple(item.case_id for item in phase_cases if item.required)
    gate = _phase_gate(
        phase_cases[0].phase,
        required_ids,
        prerequisite_gate=prerequisite_gate,
    )
    catalog.append(gate)
    return gate.case_id


def default_catalog() -> CampaignCatalog:
    """Return every authoritative Campaign step, not one umbrella case per phase."""

    cases: list[CaseSpec] = []

    baseline: list[CaseSpec] = []
    for concurrency in (1, 3, 10, 30):
        baseline.append(
            _catalog_case(
                CampaignPhase.BASELINE,
                f"BASE-MEDIA-DOWNLOAD-{concurrency}",
                load={"kind": "media_download", "concurrency": concurrency},
                fixture_ids=_LONG_FIXTURES,
            )
        )
    for task_type in ("PPT", "ASR", "TEACHER_BEHAVIOR", "STUDENT_BEHAVIOR"):
        baseline.append(
            _catalog_case(
                CampaignPhase.BASELINE,
                f"BASE-OFFLINE-{task_type.replace('_BEHAVIOR', '')}",
                load={"kind": "offline_baseline", "task_types": [task_type]},
                fixture_ids=_SHORT_FIXTURES,
            )
        )
    for operator in ("VBAS", "FACE", "SCREEN-DET", "OCR"):
        baseline.append(
            _catalog_case(
                CampaignPhase.BASELINE,
                f"BASE-ONLINE-{operator}",
                load={"kind": "online_image", "operator": operator.lower(), "concurrency": 1},
                fixture_ids=("online-image",),
            )
        )
    baseline.append(
        _catalog_case(
            CampaignPhase.BASELINE,
            "BASE-ASR-WS",
            load={"kind": "realtime_asr", "sessions": 1},
            fixture_ids=("realtime-audio",),
        )
    )
    previous_gate = _append_phase(cases, baseline, prerequisite_gate=None)

    offline: list[CaseSpec] = []
    combinations = {
        "PPT": "ppt_only",
        "ASR": "asr_only",
        "TEACHER": "teacher_only",
        "STUDENT": "student_only",
        "PPT-ASR": "ppt_asr",
        "TEACHER-STUDENT": "teacher_student",
        "ALL": "all",
    }
    for label, combination in combinations.items():
        for count in (100, 300, 1000):
            offline.append(
                _catalog_case(
                    CampaignPhase.OFFLINE,
                    f"OFF-UNIQUE-{label}-{count}",
                    load={"kind": "unique_submission", "combination": combination, "count": count},
                    fixture_ids=_SHORT_FIXTURES,
                )
            )
    for count in (30, 100, 300, 1000):
        offline.extend(
            (
                _catalog_case(
                    CampaignPhase.OFFLINE,
                    f"OFF-IDEMPOTENT-{count}",
                    load={"kind": "idempotent_submission", "count": count},
                    fixture_ids=_SHORT_FIXTURES,
                ),
                _catalog_case(
                    CampaignPhase.OFFLINE,
                    f"OFF-CONFLICT-{count}",
                    load={"kind": "conflicting_submission", "count": count},
                    fixture_ids=_SHORT_FIXTURES,
                ),
            )
        )
    offline.extend(
        (
            _catalog_case(
                CampaignPhase.OFFLINE,
                "OFF-APPEND-TASK-TYPES",
                load={"kind": "append_task_types"},
                fixture_ids=_SHORT_FIXTURES,
            ),
            _catalog_case(
                CampaignPhase.OFFLINE,
                "OFF-COMPLETED-REUSE",
                load={"kind": "completed_result_reuse"},
                fixture_ids=_SHORT_FIXTURES,
            ),
        )
    )
    for normal, urgent in ((100, 10), (300, 30)):
        offline.append(
            _catalog_case(
                CampaignPhase.OFFLINE,
                f"OFF-PRIORITY-{normal}-{urgent}",
                load={"kind": "priority", "normal": normal, "urgent": urgent},
                fixture_ids=_SHORT_FIXTURES,
            )
        )
    for count in (3, 6, 12, 24, 36):
        offline.append(
            _catalog_case(
                CampaignPhase.OFFLINE,
                f"OFF-LONG-COURSE-{count}",
                load={"kind": "long_course", "count": count},
                fixture_ids=_LONG_FIXTURES,
                timeout_seconds=28_800,
            )
        )
    for percent in (1, 5, 20):
        offline.append(
            _catalog_case(
                CampaignPhase.OFFLINE,
                f"OFF-NEGATIVE-{percent}PCT",
                load={
                    "kind": "negative_submission",
                    "ratio": percent / 100,
                    "not_found_url": (
                        "http://192.168.29.12:5555/missing-404.mp4"
                    ),
                    "timeout_url": "http://192.168.29.12:5556/timeout.mp4",
                },
                fixture_ids=_SHORT_FIXTURES,
            )
        )
    previous_gate = _append_phase(cases, offline, prerequisite_gate=previous_gate)

    query: list[CaseSpec] = []
    for qps in (50, 100, 300, 1000):
        for interval in (2, 5):
            query.append(
                _catalog_case(
                    CampaignPhase.CONTROL_QUERY,
                    f"QUERY-JITTER-{qps}-{interval}S",
                    load={"kind": "query", "mode": "jittered", "qps": qps, "interval": interval},
                    fixture_ids=("external-fixture-manifest",),
                )
            )
        query.append(
            _catalog_case(
                CampaignPhase.CONTROL_QUERY,
                f"QUERY-HERD-{qps}",
                load={"kind": "query", "mode": "herd", "qps": qps},
                fixture_ids=("external-fixture-manifest",),
            )
        )
    for percent in (1, 5, 20):
        query.append(
            _catalog_case(
                CampaignPhase.CONTROL_QUERY,
                f"QUERY-NEGATIVE-{percent}PCT",
                load={"kind": "negative_query", "ratio": percent / 100},
                fixture_ids=("external-fixture-manifest",),
            )
        )
    previous_gate = _append_phase(cases, query, prerequisite_gate=previous_gate)

    online: list[CaseSpec] = []
    for operator in ("VBAS", "FACE", "SCREEN-DET", "OCR"):
        for concurrency in (1, 3, 10, 30, 60, 100, 256, 512, 1000):
            online.append(
                _catalog_case(
                    CampaignPhase.ONLINE,
                    f"IMG-{operator}-{concurrency}",
                    load={
                        "kind": "online_image",
                        "operator": operator.lower(),
                        "concurrency": concurrency,
                    },
                    fixture_ids=("online-image",),
                )
            )
    for concurrency in (10, 100, 1000):
        online.append(
            _catalog_case(
                CampaignPhase.ONLINE,
                f"IMG-MIXED-{concurrency}",
                load={"kind": "mixed_image", "concurrency": concurrency},
                fixture_ids=("online-image",),
            )
        )
    for streams in (100, 300, 1000):
        for interval in (5, 10, 30):
            online.append(
                _catalog_case(
                    CampaignPhase.ONLINE,
                    f"SSTREAM-{streams}-{interval}S",
                    load={"kind": "s_stream", "streams": streams, "interval": interval},
                    fixture_ids=("online-image",),
                )
            )
    for boundary in (
        "512K",
        "5M",
        "49M",
        "OVER-50M",
        "INVALID-B64",
        "BAD-FORMAT",
        "BAD-DATA-URI",
        "DECODE-FAIL",
    ):
        online.append(
            _catalog_case(
                CampaignPhase.ONLINE,
                f"IMG-BOUNDARY-{boundary}",
                load={"kind": "image_boundary", "boundary": boundary},
                fixture_ids=("online-image",),
            )
        )
    for sessions in (1, 10, 24, 30, 60, 90, 150):
        online.append(
            _catalog_case(
                CampaignPhase.ONLINE,
                f"ASR-WS-{sessions}",
                load={"kind": "realtime_asr", "sessions": sessions},
                fixture_ids=("realtime-audio",),
                timeout_seconds=14_400,
            )
        )
    online.append(
        _catalog_case(
            CampaignPhase.ONLINE,
            "ASR-WS-RECONNECT",
            load={"kind": "realtime_asr_reconnect"},
            fixture_ids=("realtime-audio",),
        )
    )
    for persons in (500, 1000, 5000):
        online.extend(
            (
                _catalog_case(
                    CampaignPhase.ONLINE,
                    f"FACE-MANAGE-{persons}",
                    load={"kind": "face_management", "persons": persons},
                    fixture_ids=("person-photo",),
                ),
                _catalog_case(
                    CampaignPhase.ONLINE,
                    f"FACE-RECOGNIZE-{persons}",
                    load={"kind": "face_recognition", "persons": persons},
                    fixture_ids=("person-photo",),
                    prerequisites=(f"FACE-MANAGE-{persons}",),
                ),
            )
        )
    online.append(
        _catalog_case(
            CampaignPhase.ONLINE,
            "FACE-PHOTO-RESIDUE",
            load={"kind": "face_photo_residue"},
            fixture_ids=("person-photo",),
            prerequisites=("FACE-MANAGE-5000", "FACE-RECOGNIZE-5000"),
        )
    )
    previous_gate = _append_phase(cases, online, prerequisite_gate=previous_gate)

    mixed = [
        _catalog_case(
            CampaignPhase.MIXED,
            f"MIXED-{level}",
            load={"kind": "mixed", "level": level.lower()},
            fixture_ids=(*_LONG_FIXTURES, "online-image", "realtime-audio"),
            timeout_seconds=28_800,
        )
        for level in ("DAILY", "PEAK", "EXTREME")
    ]
    previous_gate = _append_phase(cases, mixed, prerequisite_gate=previous_gate)

    recovery: list[CaseSpec] = []
    operator_faults = (
        "ASR-OFFLINE",
        "ASR-ONLINE",
        "OCR",
        "VBAS",
        "FACEREC",
        "SCREEN-DET",
        "PPT-SLICE",
    )
    for operator in operator_faults:
        recovery.append(
            _catalog_case(
                CampaignPhase.RECOVERY,
                f"RECOVERY-OPERATOR-{operator}",
                load={"kind": "single_operator_fault", "operator": operator.lower()},
                fixture_ids=("external-fixture-manifest",),
            )
        )
    for gpu in (0, 1, 2):
        recovery.append(
            _catalog_case(
                CampaignPhase.RECOVERY,
                f"RECOVERY-GPU-{gpu}",
                load={"kind": "gpu_group_fault", "gpu": gpu},
                fixture_ids=("external-fixture-manifest",),
            )
        )
    for service in ("CONTROL", "ORCHESTRATOR", "VISION", "ONLINE-GATEWAY"):
        recovery.append(
            _catalog_case(
                CampaignPhase.RECOVERY,
                f"RECOVERY-PLATFORM-{service}",
                load={"kind": "platform_fault", "service": service.lower()},
                fixture_ids=("external-fixture-manifest",),
            )
        )
    for service in ("KAFKA", "REDIS"):
        recovery.append(
            _catalog_case(
                CampaignPhase.RECOVERY,
                f"RECOVERY-{service}",
                load={"kind": "middleware_fault", "service": service.lower()},
                fixture_ids=("external-fixture-manifest",),
            )
        )
    previous_gate = _append_phase(cases, recovery, prerequisite_gate=previous_gate)

    soak = [
        _catalog_case(
            CampaignPhase.SOAK,
            "SOAK-4H",
            load={"kind": "soak", "hours": 4, "stable_capacity_ratio": 0.75},
            fixture_ids=(*_LONG_FIXTURES, "online-image", "realtime-audio"),
            timeout_seconds=18_000,
        ),
        _catalog_case(
            CampaignPhase.SOAK,
            "SOAK-8H-OPTIONAL",
            load={"kind": "soak", "hours": 8, "stable_capacity_ratio": 0.75},
            fixture_ids=(*_LONG_FIXTURES, "online-image", "realtime-audio"),
            timeout_seconds=32_400,
            required=False,
        ),
    ]
    _append_phase(cases, soak, prerequisite_gate=previous_gate)
    return CampaignCatalog(schema_version=2, cases=tuple(cases))
