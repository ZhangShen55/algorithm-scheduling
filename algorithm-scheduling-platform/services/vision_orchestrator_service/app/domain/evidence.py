from __future__ import annotations

import shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from packages.platform_common.workspace import task_workspace


class EvidenceCategory(StrEnum):
    STUDENT_HEAD_UP = "student_head_up"
    STUDENT_READING = "student_reading"
    STUDENT_SLEEPING = "student_sleeping"
    STUDENT_PHONE_USE = "student_phone_use"
    TEACHER_ALERT = "teacher_alert"
    TEACHER_WRITING = "teacher_writing"
    TEACHER_SITTING = "teacher_sitting"
    TEACHER_TEACHING = "teacher_teaching"


@dataclass(frozen=True, slots=True)
class EvidenceCandidate:
    category: EvidenceCategory
    capture_second: float
    confidence: float
    source_path: Path
    priority: int = 0

    def __post_init__(self) -> None:
        if self.capture_second < 0:
            raise ValueError("视觉证据时间不能小于 0")
        if not 0 <= self.confidence <= 1:
            raise ValueError("视觉证据置信度必须在 0 到 1 之间")
        if not self.source_path.is_absolute():
            raise ValueError("视觉证据源图片必须是绝对本地路径")


@dataclass(frozen=True, slots=True)
class EvidenceArtifact:
    category: EvidenceCategory
    capture_second: float
    confidence: float
    path: Path


@dataclass(frozen=True, slots=True)
class VisionEvidenceConfig:
    max_per_category: int = 3
    max_total: int = 20
    same_category_min_interval_seconds: float = 30

    def __post_init__(self) -> None:
        if self.max_per_category <= 0 or self.max_total <= 0:
            raise ValueError("视觉证据分类和总数上限必须大于 0")
        if self.same_category_min_interval_seconds < 0:
            raise ValueError("视觉证据同类最小间隔不能小于 0")


class VisionEvidencePublisher:
    _SUPPORTED_SUFFIXES = frozenset({".jpg", ".jpeg", ".png"})

    def __init__(
        self,
        *,
        result_root: Path,
        config: VisionEvidenceConfig | None = None,
    ) -> None:
        self._result_root = result_root
        self._config = config or VisionEvidenceConfig()

    def publish(
        self,
        task_id: str,
        candidates: list[EvidenceCandidate],
    ) -> list[EvidenceArtifact]:
        selected = self._select(candidates)
        if not selected:
            return []
        vision_root = task_workspace(self._result_root, task_id) / "vision"
        artifacts: list[EvidenceArtifact] = []
        for candidate in selected:
            if not candidate.source_path.is_file():
                raise FileNotFoundError(f"视觉证据源图片不存在: {candidate.source_path}")
            suffix = candidate.source_path.suffix.lower()
            if suffix not in self._SUPPORTED_SUFFIXES:
                raise ValueError(f"不支持的视觉证据图片格式: {suffix}")
            category_dir = vision_root / candidate.category.value
            category_dir.mkdir(parents=True, exist_ok=True)
            timestamp_ms = round(candidate.capture_second * 1000)
            target = category_dir / f"{candidate.category.value}-{timestamp_ms:012d}{suffix}"
            if not target.exists():
                partial = target.with_suffix(f"{suffix}.part")
                try:
                    shutil.copyfile(candidate.source_path, partial)
                    partial.replace(target)
                finally:
                    partial.unlink(missing_ok=True)
            artifacts.append(
                EvidenceArtifact(
                    category=candidate.category,
                    capture_second=candidate.capture_second,
                    confidence=candidate.confidence,
                    path=target,
                )
            )
        return artifacts

    def _select(self, candidates: list[EvidenceCandidate]) -> list[EvidenceCandidate]:
        selected: list[EvidenceCandidate] = []
        for category in EvidenceCategory:
            ordered = sorted(
                (item for item in candidates if item.category is category),
                key=lambda item: item.capture_second,
            )
            deduplicated: list[EvidenceCandidate] = []
            for candidate in ordered:
                conflict_index = next(
                    (
                        index
                        for index, existing in enumerate(deduplicated)
                        if abs(existing.capture_second - candidate.capture_second)
                        < self._config.same_category_min_interval_seconds
                    ),
                    None,
                )
                if conflict_index is None:
                    deduplicated.append(candidate)
                    continue
                existing = deduplicated[conflict_index]
                if (candidate.confidence, -candidate.capture_second) > (
                    existing.confidence,
                    -existing.capture_second,
                ):
                    deduplicated[conflict_index] = candidate
            strongest = sorted(
                deduplicated,
                key=lambda item: (item.priority, -item.confidence, item.capture_second),
            )[: self._config.max_per_category]
            selected.extend(strongest)
        ranked = sorted(
            selected,
            key=lambda item: (item.priority, item.category.value, item.capture_second),
        )
        return ranked[: self._config.max_total]
