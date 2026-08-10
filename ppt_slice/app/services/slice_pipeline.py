"""In-memory slide publication pipeline with sustained-dynamic suppression."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from app.core.config import Settings
from app.services.dynamic_detection import (
    ActivityAnalyzer,
    DynamicSegmentDetector,
    DynamicState,
    cluster_dynamic_segments,
)
from app.services.image_compare import compare_images


BLACK_SCREEN_MAX_MEAN_LUMA = 5.0
BLACK_SCREEN_BRIGHT_LUMA_THRESHOLD = 20
BLACK_SCREEN_MAX_BRIGHT_PIXEL_RATIO = 0.001


def is_black_screen(frame) -> bool:
    """Reject empty black frames without consuming legitimate dark slides."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    mean_luma = float(gray.mean())
    bright_pixel_ratio = float(
        np.count_nonzero(gray > BLACK_SCREEN_BRIGHT_LUMA_THRESHOLD) / gray.size
    )
    return (
        mean_luma <= BLACK_SCREEN_MAX_MEAN_LUMA
        and bright_pixel_ratio <= BLACK_SCREEN_MAX_BRIGHT_PIXEL_RATIO
    )


@dataclass(frozen=True)
class SlicePipelineConfig:
    dynamic_detection_enabled: bool
    sample_interval_ms: int
    pixel_difference_threshold: int
    changed_pixel_ratio: float
    grid_rows: int
    grid_columns: int
    active_grid_ratio: float
    window_ms: int
    confirmation_ms: int
    required_active_ratio: float
    exit_stable_ms: int
    merge_gap_ms: int
    candidate_stable_ms: int
    contiguous_similarity: float
    saved_similarity: float
    cluster_gap_ms: int = 90000
    cluster_min_segments: int = 3
    optical_flow_enabled: bool = False
    optical_flow_width: int = 320
    optical_flow_magnitude_threshold: float = 0.5
    optical_flow_active_ratio: float = 0.05
    motion_grace_ms: int = 3000

    @classmethod
    def from_settings(cls, settings: Settings, *, saved_similarity: float | None = None):
        return cls(
            dynamic_detection_enabled=settings.DYNAMIC_DETECTION_ENABLED,
            sample_interval_ms=settings.DYNAMIC_SAMPLE_INTERVAL_MS,
            pixel_difference_threshold=settings.DYNAMIC_PIXEL_DIFFERENCE_THRESHOLD,
            changed_pixel_ratio=settings.DYNAMIC_CHANGED_PIXEL_RATIO,
            grid_rows=settings.DYNAMIC_GRID_ROWS,
            grid_columns=settings.DYNAMIC_GRID_COLUMNS,
            active_grid_ratio=settings.DYNAMIC_ACTIVE_GRID_RATIO,
            window_ms=settings.DYNAMIC_WINDOW_MS,
            confirmation_ms=settings.DYNAMIC_CONFIRMATION_MS,
            required_active_ratio=settings.DYNAMIC_REQUIRED_ACTIVE_RATIO,
            exit_stable_ms=settings.DYNAMIC_EXIT_STABLE_MS,
            merge_gap_ms=settings.DYNAMIC_MERGE_GAP_MS,
            candidate_stable_ms=settings.DYNAMIC_CANDIDATE_STABLE_MS,
            contiguous_similarity=settings.DEFAULT_CONTIGUOUS_SIMILARITY,
            saved_similarity=(
                settings.DEFAULT_SAVED_SIMILARITY
                if saved_similarity is None
                else saved_similarity
            ),
            cluster_gap_ms=settings.DYNAMIC_CLUSTER_GAP_MS,
            cluster_min_segments=settings.DYNAMIC_CLUSTER_MIN_SEGMENTS,
            optical_flow_enabled=settings.DYNAMIC_OPTICAL_FLOW_ENABLED,
            optical_flow_width=settings.DYNAMIC_OPTICAL_FLOW_WIDTH,
            optical_flow_magnitude_threshold=settings.DYNAMIC_OPTICAL_FLOW_MAGNITUDE_THRESHOLD,
            optical_flow_active_ratio=settings.DYNAMIC_OPTICAL_FLOW_ACTIVE_RATIO,
            motion_grace_ms=settings.DYNAMIC_MOTION_GRACE_MS,
        )


@dataclass(frozen=True)
class PendingSlice:
    frame_seq: int
    timestamp_ms: int
    jpeg_bytes: bytes


class SlicePipeline:
    def __init__(
        self,
        writer,
        config: SlicePipelineConfig,
        *,
        comparator=compare_images,
        activity_sink=None,
    ) -> None:
        self.writer = writer
        self.config = config
        self.comparator = comparator
        self.activity_sink = activity_sink
        self.activity_analyzer = ActivityAnalyzer(
            pixel_difference_threshold=config.pixel_difference_threshold,
            changed_pixel_ratio_threshold=config.changed_pixel_ratio,
            grid_rows=config.grid_rows,
            grid_columns=config.grid_columns,
            active_grid_ratio_threshold=config.active_grid_ratio,
            optical_flow_enabled=config.optical_flow_enabled,
            optical_flow_width=config.optical_flow_width,
            optical_flow_magnitude_threshold=config.optical_flow_magnitude_threshold,
            optical_flow_active_ratio_threshold=config.optical_flow_active_ratio,
        )
        self.detector = DynamicSegmentDetector(
            confirmation_ms=config.confirmation_ms,
            window_ms=config.window_ms,
            required_active_ratio=config.required_active_ratio,
            exit_stable_ms=config.exit_stable_ms,
            merge_gap_ms=config.merge_gap_ms,
            candidate_stable_ms=config.candidate_stable_ms,
            motion_grace_ms=config.motion_grace_ms,
        )
        self.last_frame = None
        self.last_analysis_frame = None
        self.last_analysis_ms: int | None = None
        self.saved_frame = None
        self.published_frame = None
        self.candidate_frame = None
        self.candidate_since_ms: int | None = None
        self.pending_slices: list[PendingSlice] = []
        self.observation_count = 0
        self.suppressed_candidate_count = 0

    def observe(self, frame_data) -> None:
        timestamp_ms = int(frame_data.timestamp_ms)
        frame = frame_data.frame
        self.observation_count += 1

        if not self.config.dynamic_detection_enabled:
            self._observe_legacy(timestamp_ms, frame)
            self.last_frame = frame
            return

        state = self.detector.state
        if self.last_analysis_frame is None:
            self.last_analysis_frame = frame
            self.last_analysis_ms = timestamp_ms
        elif timestamp_ms - self.last_analysis_ms >= self.config.sample_interval_ms:
            activity = self.activity_analyzer.analyze(
                timestamp_ms,
                self.last_analysis_frame,
                frame,
            )
            if self.activity_sink is not None:
                self.activity_sink(activity)
            state = self.detector.observe(
                timestamp_ms,
                activity.is_active,
                activity.score,
                is_motion_active=activity.is_motion_active,
                motion_score=activity.motion_score,
            )
            self.last_analysis_frame = frame
            self.last_analysis_ms = timestamp_ms

        self._reconcile_pending(timestamp_ms)

        if state == DynamicState.STABLE:
            self._observe_stable_page(timestamp_ms, frame)
        else:
            if self.candidate_frame is not None:
                self.suppressed_candidate_count += 1
            self._clear_candidate()
        self.last_frame = frame

    def finish(self, last_timestamp_ms: int) -> None:
        if self.config.dynamic_detection_enabled:
            self.detector.finish(last_timestamp_ms)
            self._reconcile_pending(last_timestamp_ms, force=True)
            segments = [segment.as_dict() for segment in self._clustered_segments()]
        else:
            segments = []
        self.writer.set_dynamic_segments(segments)

    def _observe_legacy(self, timestamp_ms: int, frame) -> None:
        if self.last_frame is None:
            return
        if is_black_screen(frame):
            return
        contiguous = self.comparator(self.last_frame, frame)
        if self.saved_frame is None and contiguous > self.config.contiguous_similarity:
            self.saved_frame = self.last_frame
            self._publish(frame, timestamp_ms)
        elif contiguous > self.config.contiguous_similarity:
            if self.comparator(self.saved_frame, frame) < self.config.saved_similarity:
                self.saved_frame = frame
                self._publish(frame, timestamp_ms)

    def _observe_stable_page(self, timestamp_ms: int, frame) -> None:
        if self.last_frame is None:
            return
        if is_black_screen(frame):
            self._clear_candidate()
            return
        if self.comparator(self.last_frame, frame) <= self.config.contiguous_similarity:
            self._clear_candidate()
            return
        differs_from_saved = (
            self.saved_frame is None
            or self.comparator(self.saved_frame, frame) < self.config.saved_similarity
        )
        if not differs_from_saved:
            self._clear_candidate()
            return
        if self.candidate_frame is None:
            self.candidate_frame = frame
            self.candidate_since_ms = timestamp_ms
            return
        if self.comparator(self.candidate_frame, frame) <= self.config.contiguous_similarity:
            self.candidate_frame = frame
            self.candidate_since_ms = timestamp_ms
            return
        if timestamp_ms - self.candidate_since_ms >= self.config.candidate_stable_ms:
            self.saved_frame = frame
            self._publish(frame, timestamp_ms)
            self._clear_candidate()

    def _publish(self, frame, timestamp_ms: int) -> None:
        if self.config.dynamic_detection_enabled and self._cluster_decision_open(timestamp_ms):
            encoded, buffer = cv2.imencode(".jpg", frame)
            if not encoded:
                raise RuntimeError("PPT 候选切片 JPEG 内存编码失败")
            self.pending_slices.append(
                PendingSlice(
                    frame_seq=self.observation_count,
                    timestamp_ms=timestamp_ms,
                    jpeg_bytes=buffer.tobytes(),
                )
            )
            return
        self._write_image(self.observation_count, timestamp_ms, frame)

    def _write_image(self, frame_seq: int, timestamp_ms: int, frame) -> None:
        self.writer.write_image(
            frame_seq=frame_seq,
            snap_time=timestamp_ms // 1000,
            frame=frame,
        )
        self.published_frame = frame.copy()

    def _clustered_segments(self):
        return cluster_dynamic_segments(
            self.detector.segments,
            cluster_gap_ms=self.config.cluster_gap_ms,
            cluster_min_segments=self.config.cluster_min_segments,
        )

    def _cluster_decision_open(self, timestamp_ms: int) -> bool:
        segments = self.detector.segments
        if not segments:
            return False
        last_segment = segments[-1]
        active_start_ms = self.detector.active_start_ms
        if (
            self.detector.state != DynamicState.STABLE
            and active_start_ms is not None
            and active_start_ms - last_segment.end_ms <= self.config.cluster_gap_ms
        ):
            return True
        return timestamp_ms <= last_segment.end_ms + self.config.cluster_gap_ms

    def _reconcile_pending(self, timestamp_ms: int, *, force: bool = False) -> None:
        if not self.pending_slices:
            return
        segments = self._clustered_segments()
        retained = []
        for pending in self.pending_slices:
            if any(
                segment.start_ms <= pending.timestamp_ms < segment.end_ms
                for segment in segments
            ):
                self.suppressed_candidate_count += 1
            else:
                retained.append(pending)
        self.pending_slices = retained

        if not force and self._cluster_decision_open(timestamp_ms):
            return
        for pending in self.pending_slices:
            encoded = cv2.imdecode(
                np.frombuffer(pending.jpeg_bytes, dtype=np.uint8),
                cv2.IMREAD_COLOR,
            )
            if encoded is None:
                raise RuntimeError("PPT 候选切片 JPEG 内存解码失败")
            self._write_image(pending.frame_seq, pending.timestamp_ms, encoded)
        self.pending_slices = []
        self.saved_frame = self.published_frame

    def _clear_candidate(self) -> None:
        self.candidate_frame = None
        self.candidate_since_ms = None
