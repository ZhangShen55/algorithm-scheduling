"""Time-based detection of sustained visual activity in PPT recordings."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum

import cv2
import numpy as np


class DynamicState(str, Enum):
    STABLE = "STABLE"
    DYNAMIC_CANDIDATE = "DYNAMIC_CANDIDATE"
    DYNAMIC = "DYNAMIC"
    STABILIZING = "STABILIZING"


@dataclass(frozen=True)
class ActivityObservation:
    timestamp_ms: int
    changed_pixel_ratio: float
    active_grid_ratio: float
    is_active: bool
    score: float
    is_motion_active: bool = False
    motion_ratio: float = 0.0
    motion_score: float = 0.0


@dataclass(frozen=True)
class DynamicSegment:
    type: str
    start_ms: int
    end_ms: int
    confidence: float
    reason: str = "sustained_visual_change"
    evidence_count: int = 1

    def as_dict(self) -> dict:
        return {
            "type": self.type,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "confidence": self.confidence,
            "reason": self.reason,
        }


def cluster_dynamic_segments(
    segments,
    *,
    cluster_gap_ms: int,
    cluster_min_segments: int,
) -> tuple[DynamicSegment, ...]:
    """Bridge repeated dynamic segments without widening the normal merge gap."""
    if cluster_gap_ms <= 0:
        raise ValueError("cluster_gap_ms 必须大于 0")
    if cluster_min_segments < 3:
        raise ValueError("cluster_min_segments 不能小于 3")
    ordered = sorted(tuple(segments), key=lambda item: (item.start_ms, item.end_ms))
    if not ordered:
        return ()

    clustered: list[DynamicSegment] = []
    chain: list[DynamicSegment] = [ordered[0]]

    def flush() -> None:
        evidence_count = sum(item.evidence_count for item in chain)
        if evidence_count < cluster_min_segments:
            clustered.extend(chain)
            return
        clustered.append(
            DynamicSegment(
                type=chain[0].type,
                start_ms=chain[0].start_ms,
                end_ms=chain[-1].end_ms,
                confidence=round(max(item.confidence for item in chain), 4),
                reason="repeated_dynamic_cluster",
                evidence_count=evidence_count,
            )
        )

    for segment in ordered[1:]:
        if segment.start_ms - chain[-1].end_ms <= cluster_gap_ms:
            chain.append(segment)
            continue
        flush()
        chain = [segment]
    flush()
    return tuple(clustered)


class ActivityAnalyzer:
    def __init__(
        self,
        *,
        pixel_difference_threshold: int,
        changed_pixel_ratio_threshold: float,
        grid_rows: int,
        grid_columns: int,
        active_grid_ratio_threshold: float,
        optical_flow_enabled: bool = False,
        optical_flow_width: int = 320,
        optical_flow_magnitude_threshold: float = 0.5,
        optical_flow_active_ratio_threshold: float = 0.05,
    ) -> None:
        self.pixel_difference_threshold = pixel_difference_threshold
        self.changed_pixel_ratio_threshold = changed_pixel_ratio_threshold
        self.grid_rows = grid_rows
        self.grid_columns = grid_columns
        self.active_grid_ratio_threshold = active_grid_ratio_threshold
        self.optical_flow_enabled = optical_flow_enabled
        self.optical_flow_width = optical_flow_width
        self.optical_flow_magnitude_threshold = optical_flow_magnitude_threshold
        self.optical_flow_active_ratio_threshold = optical_flow_active_ratio_threshold

    def analyze(self, timestamp_ms: int, previous, current) -> ActivityObservation:
        if previous.shape != current.shape:
            current = cv2.resize(current, (previous.shape[1], previous.shape[0]))
        difference = cv2.absdiff(previous, current)
        gray = cv2.cvtColor(difference, cv2.COLOR_BGR2GRAY)
        changed = gray >= self.pixel_difference_threshold
        changed_pixel_ratio = float(np.count_nonzero(changed) / changed.size)

        active_cells = 0
        row_groups = np.array_split(changed, self.grid_rows, axis=0)
        for row_group in row_groups:
            for cell in np.array_split(row_group, self.grid_columns, axis=1):
                cell_ratio = float(np.count_nonzero(cell) / cell.size) if cell.size else 0.0
                if cell_ratio >= self.changed_pixel_ratio_threshold:
                    active_cells += 1
        grid_count = self.grid_rows * self.grid_columns
        active_grid_ratio = active_cells / grid_count
        is_active = (
            changed_pixel_ratio >= self.changed_pixel_ratio_threshold
            and active_grid_ratio >= self.active_grid_ratio_threshold
        )
        score = max(changed_pixel_ratio, active_grid_ratio) if is_active else 0.0
        motion_ratio = 0.0
        is_motion_active = False
        if self.optical_flow_enabled:
            height = max(1, round(previous.shape[0] * self.optical_flow_width / previous.shape[1]))
            size = (self.optical_flow_width, height)
            previous_gray = cv2.cvtColor(cv2.resize(previous, size), cv2.COLOR_BGR2GRAY)
            current_gray = cv2.cvtColor(cv2.resize(current, size), cv2.COLOR_BGR2GRAY)
            flow = cv2.calcOpticalFlowFarneback(
                previous_gray,
                current_gray,
                None,
                0.5,
                3,
                15,
                3,
                5,
                1.2,
                0,
            )
            magnitude = np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2)
            motion_ratio = float(
                np.count_nonzero(magnitude >= self.optical_flow_magnitude_threshold)
                / magnitude.size
            )
            is_motion_active = motion_ratio >= self.optical_flow_active_ratio_threshold
        return ActivityObservation(
            timestamp_ms=int(timestamp_ms),
            changed_pixel_ratio=round(changed_pixel_ratio, 6),
            active_grid_ratio=round(active_grid_ratio, 6),
            is_active=is_active,
            score=round(min(max(score, 0.0), 1.0), 6),
            is_motion_active=is_motion_active,
            motion_ratio=round(motion_ratio, 6),
            motion_score=round(min(max(motion_ratio, 0.0), 1.0), 6),
        )


class DynamicSegmentDetector:
    def __init__(
        self,
        *,
        confirmation_ms: int,
        window_ms: int,
        required_active_ratio: float,
        exit_stable_ms: int,
        merge_gap_ms: int,
        candidate_stable_ms: int,
        motion_grace_ms: int | None = None,
    ) -> None:
        self.confirmation_ms = confirmation_ms
        self.window_ms = window_ms
        self.required_active_ratio = required_active_ratio
        self.exit_stable_ms = exit_stable_ms
        self.merge_gap_ms = merge_gap_ms
        self.candidate_stable_ms = candidate_stable_ms
        self.motion_grace_ms = exit_stable_ms if motion_grace_ms is None else motion_grace_ms
        self.state = DynamicState.STABLE
        self._candidate_start_ms: int | None = None
        self._last_active_ms: int | None = None
        self._stable_start_ms: int | None = None
        self._last_signal_was_motion_only = False
        self._last_strong_active_ms: int | None = None
        self._strong_burst_count = 0
        self._window: deque[tuple[int, bool, float]] = deque()
        self._segment_scores: list[float] = []
        self._segments: list[DynamicSegment] = []

    @property
    def segments(self) -> tuple[DynamicSegment, ...]:
        return tuple(self._segments)

    @property
    def active_start_ms(self) -> int | None:
        return self._candidate_start_ms

    def _trim_window(self, timestamp_ms: int) -> None:
        cutoff = timestamp_ms - self.window_ms
        while self._window and self._window[0][0] < cutoff:
            self._window.popleft()

    def _active_ratio(self) -> float:
        if not self._window:
            return 0.0
        return sum(active for _, active, _ in self._window) / len(self._window)

    def observe(
        self,
        timestamp_ms: int,
        is_active: bool,
        score: float,
        *,
        is_motion_active: bool = False,
        motion_score: float = 0.0,
    ) -> DynamicState:
        timestamp_ms = int(timestamp_ms)
        bounded_score = min(max(float(score), 0.0), 1.0)
        bounded_motion_score = min(max(float(motion_score), 0.0), 1.0)
        window_active = bool(
            is_active
            or (
                self.state == DynamicState.DYNAMIC_CANDIDATE
                and is_motion_active
            )
        )
        self._window.append(
            (
                timestamp_ms,
                window_active,
                bounded_score if is_active else bounded_motion_score,
            )
        )
        self._trim_window(timestamp_ms)

        if self.state == DynamicState.STABLE:
            if is_active:
                self._window.clear()
                self._window.append((timestamp_ms, True, bounded_score))
                self.state = DynamicState.DYNAMIC_CANDIDATE
                self._candidate_start_ms = timestamp_ms
                self._last_active_ms = timestamp_ms
                self._last_strong_active_ms = timestamp_ms
                self._strong_burst_count = 1
                self._segment_scores = [bounded_score]
            return self.state

        if self.state == DynamicState.DYNAMIC_CANDIDATE:
            if is_active:
                self._record_strong_activity(timestamp_ms)
                self._last_active_ms = timestamp_ms
                self._last_signal_was_motion_only = False
                self._segment_scores.append(bounded_score)
            elif is_motion_active:
                self._last_active_ms = timestamp_ms
                self._last_signal_was_motion_only = True
                self._segment_scores.append(bounded_motion_score)
            if (
                self._candidate_start_ms is not None
                and timestamp_ms - self._candidate_start_ms >= self.confirmation_ms
                and self._active_ratio() >= self.required_active_ratio
            ):
                self.state = DynamicState.DYNAMIC
            elif (
                not is_active
                and not is_motion_active
                and self._last_active_ms is not None
                and timestamp_ms - self._last_active_ms >= self.candidate_stable_ms
            ):
                self._reset_to_stable()
            return self.state

        if self.state == DynamicState.DYNAMIC:
            if is_active:
                self._record_strong_activity(timestamp_ms)
                self._last_active_ms = timestamp_ms
                self._last_signal_was_motion_only = False
                self._segment_scores.append(bounded_score)
            elif is_motion_active:
                self._last_active_ms = timestamp_ms
                self._last_signal_was_motion_only = True
                self._segment_scores.append(bounded_motion_score)
            else:
                self.state = DynamicState.STABILIZING
                self._stable_start_ms = timestamp_ms
            return self.state

        if is_active:
            self._record_strong_activity(timestamp_ms)
            self.state = DynamicState.DYNAMIC
            self._stable_start_ms = None
            self._last_active_ms = timestamp_ms
            self._last_signal_was_motion_only = False
            self._segment_scores.append(bounded_score)
        elif is_motion_active:
            self.state = DynamicState.DYNAMIC
            self._stable_start_ms = None
            self._last_active_ms = timestamp_ms
            self._last_signal_was_motion_only = True
            self._segment_scores.append(bounded_motion_score)
        elif (
            self._stable_start_ms is not None
            and timestamp_ms - self._stable_start_ms
            >= (
                self.motion_grace_ms
                if self._last_signal_was_motion_only
                else self.exit_stable_ms
            )
        ):
            self._close_segment(self._stable_start_ms)
            self._reset_to_stable()
        return self.state

    def finish(self, last_timestamp_ms: int) -> tuple[DynamicSegment, ...]:
        if self.state in {DynamicState.DYNAMIC, DynamicState.STABILIZING}:
            self._close_segment(int(last_timestamp_ms))
        self._reset_to_stable()
        return self.segments

    def _close_segment(self, end_ms: int) -> None:
        if self._candidate_start_ms is None or end_ms <= self._candidate_start_ms:
            return
        confidence = (
            sum(self._segment_scores) / len(self._segment_scores)
            if self._segment_scores
            else self.required_active_ratio
        )
        segment = DynamicSegment(
            type="SUSPECTED_VIDEO_PLAYBACK",
            start_ms=self._candidate_start_ms,
            end_ms=end_ms,
            confidence=round(min(max(confidence, 0.0), 1.0), 4),
            evidence_count=max(1, self._strong_burst_count),
        )
        if self._segments and segment.start_ms - self._segments[-1].end_ms <= self.merge_gap_ms:
            previous = self._segments[-1]
            self._segments[-1] = DynamicSegment(
                type=previous.type,
                start_ms=previous.start_ms,
                end_ms=max(previous.end_ms, segment.end_ms),
                confidence=round(max(previous.confidence, segment.confidence), 4),
                reason=previous.reason,
                evidence_count=previous.evidence_count + segment.evidence_count,
            )
        else:
            self._segments.append(segment)

    def _record_strong_activity(self, timestamp_ms: int) -> None:
        if (
            self._last_strong_active_ms is None
            or timestamp_ms - self._last_strong_active_ms >= self.motion_grace_ms
        ):
            self._strong_burst_count += 1
        self._last_strong_active_ms = timestamp_ms

    def _reset_to_stable(self) -> None:
        self.state = DynamicState.STABLE
        self._candidate_start_ms = None
        self._last_active_ms = None
        self._stable_start_ms = None
        self._last_signal_was_motion_only = False
        self._last_strong_active_ms = None
        self._strong_burst_count = 0
        self._segment_scores = []
        self._window.clear()
