from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from packages.platform_common.repository import NodeRecord, NodeResultWrite
from packages.platform_common.workspace import task_workspace
from packages.platform_contracts.status import TaskType
from packages.platform_contracts.vision import VisualAnalysisCommand

from ..core.config import VisionSettings
from ..domain.adaptive_scan import (
    AdaptiveScanConfig,
    AdaptiveScanPlanner,
    BehaviorInterval,
)
from ..domain.behavior_intervals import (
    TeacherBehaviorAggregationConfig,
    build_teacher_behavior_result,
)
from ..domain.evidence import (
    EvidenceCandidate,
    EvidenceCategory,
    VisionEvidencePublisher,
)
from ..domain.student_aggregation import (
    StudentBehaviorAggregator,
    StudentFrameObservation,
)
from ..infrastructure.cache import VisionStream
from ..infrastructure.media import (
    ExtractedFrame,
    FFmpegFrameExtractor,
    FrameBatchPlan,
    build_frame_batch_plans,
)
from ..infrastructure.metrics import VisionPipelineMetrics
from ..infrastructure.vbas import VbasBatchClient, VbasFrame
from .events import ProgressCallback

JsonObject = dict[str, Any]

TEACHER_OBJECT_TYPES = {
    201: "sitting",
    202: "standing",
    203: "writing",
    204: "teaching",
}
STUDENT_OBJECT_TYPES = {
    100: "detected_total",
    101: "stable_person_count",
    201: "phone_count",
    202: "sleep_count",
    205: "read_count",
}


class VisionRepository(Protocol):
    def get_node(self, node_id: int) -> NodeRecord: ...

    def get_or_create_visual_fallback(
        self,
        course_task_type_id: int,
        metric_code: str,
        candidate_value: float,
    ) -> float: ...


class FrameExtractor(Protocol):
    async def duration_seconds(self, video_path: Path) -> float: ...

    async def extract(
        self,
        *,
        task_id: str,
        stream: VisionStream,
        video_path: Path,
        timestamps: list[float],
    ) -> list[ExtractedFrame]: ...


@dataclass(frozen=True, slots=True)
class _FrameResult:
    timestamp_seconds: float
    path: Path
    counts: JsonObject
    confidences: dict[str, float]


@dataclass(frozen=True, slots=True)
class _PreparedFrameBatch:
    plan: FrameBatchPlan
    frames: tuple[ExtractedFrame, ...]


class _MonotonicProgress:
    def __init__(self, callback: ProgressCallback) -> None:
        self._callback = callback
        self._last_percent = 0
        self._lock = asyncio.Lock()

    async def report(self, percent: int, stage: str, reason: str) -> None:
        async with self._lock:
            self._last_percent = max(self._last_percent, percent)
            await self._callback(self._last_percent, stage, reason)


class CourseVisualAnalyzer:
    def __init__(
        self,
        repository: VisionRepository,
        frame_extractor: FrameExtractor,
        vbas_client: VbasBatchClient,
        evidence_publisher: VisionEvidencePublisher,
        *,
        settings: VisionSettings,
        metrics: VisionPipelineMetrics | None = None,
    ) -> None:
        self._repository = repository
        self._frames = frame_extractor
        self._vbas = vbas_client
        self._evidence = evidence_publisher
        self._settings = settings
        self._metrics = metrics

    async def analyze(
        self,
        command: VisualAnalysisCommand,
        progress: ProgressCallback,
    ) -> NodeResultWrite:
        progress_reporter = _MonotonicProgress(progress)
        await progress_reporter.report(5, "视频校验", "正在校验本地视频")
        video_path = self._validated_command_video(command)
        await progress_reporter.report(10, "视频探测", "正在探测视频时长")
        duration = await self._frames.duration_seconds(video_path)
        if command.task_type is TaskType.TEACHER_BEHAVIOR:
            return await self._analyze_teacher(
                command,
                video_path,
                duration,
                progress_reporter,
            )
        return await self._analyze_student(
            command,
            video_path,
            duration,
            progress_reporter,
        )

    def _validated_command_video(self, command: VisualAnalysisCommand) -> Path:
        try:
            video_path = Path(command.local_video_path).resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"视觉视频文件不存在: {command.local_video_path}") from exc
        task_root = task_workspace(
            self._settings.storage.course_root,
            command.task_id,
        ).resolve()
        if not video_path.is_relative_to(task_root):
            raise ValueError(f"视觉视频不属于当前课程目录: {video_path}")
        if not video_path.is_file():
            raise ValueError(f"视觉视频路径不是文件: {video_path}")
        return video_path

    async def _analyze_teacher(
        self,
        command: VisualAnalysisCommand,
        video_path: Path,
        duration: float,
        progress: _MonotonicProgress,
    ) -> NodeResultWrite:
        settings = self._settings
        coarse = _strategy_positive_float(
            command.strategy,
            "coarse_interval_seconds",
            settings.scan.default_interval_seconds,
        )
        refinements = _strategy_positive_floats(
            command.strategy,
            "refinement_intervals_seconds",
            settings.scan.refinement_intervals_seconds,
        )
        planner = AdaptiveScanPlanner(
            AdaptiveScanConfig(
                coarse_interval_seconds=coarse,
                refinement_intervals_seconds=tuple(refinements),
                max_candidate_windows=settings.scan.max_candidate_windows,
                max_detection_points=settings.scan.max_detection_points,
            )
        )
        observations: dict[float, _FrameResult] = {}

        async def detect(points: Any) -> dict[float, bool]:
            selected = [float(point) for point in points]
            inferred = await self._infer(
                command,
                VisionStream.TEACHER,
                video_path,
                selected,
                progress=progress,
            )
            observations.update(inferred)
            return {
                point: bool(
                    inferred[point].counts.get("writing", 0)
                    or inferred[point].counts.get("sitting", 0)
                )
                for point in selected
            }

        await progress.report(20, "教师扫描", "正在抽取教师行为采样帧")
        scan_duration = _safe_sampling_end(
            duration,
            settings.scan.end_frame_margin_seconds,
        )
        scan = await planner.scan_async(
            duration_seconds=scan_duration,
            detector=detect,
        )
        await progress.report(75, "教师聚合", "正在聚合教师行为区间")
        intervals = {
            behavior: _intervals_from_observations(
                observations,
                behavior,
                duration_seconds=duration,
                fallback_step_seconds=coarse,
            )
            for behavior in ("writing", "sitting", "standing", "teaching")
        }
        outcome = build_teacher_behavior_result(
            intervals=intervals,
            valid_frame_count=len(observations),
            total_frame_count=len(observations),
            config=TeacherBehaviorAggregationConfig(
                writing_max_gap_seconds=3,
                sitting_max_gap_seconds=5,
            ),
        )
        evidence = self._publish_teacher_evidence(command.task_id, observations)
        result = dict(outcome.result)
        result.update(
            {
                "duration_seconds": duration,
                "scan": {
                    "stages": list(scan.stages),
                    "candidate_windows": [
                        list(item) for item in scan.candidate_windows
                    ],
                    "evaluated_point_count": scan.evaluated_point_count,
                },
                "evidence": _evidence_result(evidence),
            }
        )
        await progress.report(95, "结果持久化", "正在持久化视觉结果")
        return self._node_result(command.task_id, result, evidence)

    async def _analyze_student(
        self,
        command: VisualAnalysisCommand,
        video_path: Path,
        duration: float,
        progress: _MonotonicProgress,
    ) -> NodeResultWrite:
        interval = _strategy_positive_float(
            command.strategy,
            "interval_seconds",
            self._settings.scan.default_interval_seconds,
        )
        points = _sample_points(
            duration,
            interval,
            self._settings.scan.end_frame_margin_seconds,
        )
        await progress.report(20, "学生扫描", "正在抽取学生视频采样帧")
        regions: dict[str, list[JsonObject] | None] = {"full": None}
        if command.front_points:
            regions["front"] = command.front_points
        if command.back_point:
            regions["back"] = command.back_point
        inferred_regions = await self._infer_regions(
            command,
            VisionStream.STUDENT,
            video_path,
            points,
            regions=regions,
            progress=progress,
        )
        total = inferred_regions["full"]
        front = inferred_regions.get("front", {})
        back = inferred_regions.get("back", {})
        await progress.report(75, "学生聚合", "正在聚合学生人数和行为结果")
        observations = [
            StudentFrameObservation(
                detected_total=int(item.counts.get("detected_total", 0)),
                stable_person_count=int(item.counts.get("stable_person_count", 0)),
                front_stable_person_count=int(
                    front.get(point, item).counts.get("stable_person_count", 0)
                    if front
                    else 0
                ),
                back_stable_person_count=int(
                    back.get(point, item).counts.get("stable_person_count", 0)
                    if back
                    else 0
                ),
            )
            for point, item in sorted(total.items())
        ]
        node = await asyncio.to_thread(self._repository.get_node, command.node_id)
        aggregator = StudentBehaviorAggregator(self._repository)
        summary = await asyncio.to_thread(
            aggregator.aggregate,
            course_task_type_id=node.course_task_type_id,
            student_count=int(command.student_count or 0),
            observations=observations,
            front_points=command.front_points,
            back_point=command.back_point,
        )
        evidence = self._publish_student_evidence(command.task_id, total)
        result: JsonObject = {
            **summary,
            "duration_seconds": duration,
            "sample_interval_seconds": interval,
            "frames": [
                {
                    "timestamp_seconds": point,
                    **item.counts,
                }
                for point, item in sorted(total.items())
            ],
            "evidence": _evidence_result(evidence),
        }
        await progress.report(95, "结果持久化", "正在持久化视觉结果")
        return self._node_result(command.task_id, result, evidence)

    async def _infer(
        self,
        command: VisualAnalysisCommand,
        stream: VisionStream,
        video_path: Path,
        points: list[float],
        *,
        region: list[JsonObject] | None = None,
        identity_suffix: str = "full",
        progress: _MonotonicProgress | None = None,
    ) -> dict[float, _FrameResult]:
        results = await self._infer_regions(
            command,
            stream,
            video_path,
            points,
            regions={identity_suffix: region},
            progress=progress,
        )
        return results[identity_suffix]

    async def _infer_regions(
        self,
        command: VisualAnalysisCommand,
        stream: VisionStream,
        video_path: Path,
        points: list[float],
        *,
        regions: dict[str, list[JsonObject] | None],
        progress: _MonotonicProgress | None = None,
    ) -> dict[str, dict[float, _FrameResult]]:
        plans = build_frame_batch_plans(
            task_id=command.task_id,
            stream=stream,
            timestamps=points,
            batch_size=min(
                self._settings.scan.batch_size,
                self._settings.vbas.max_batch_size,
            ),
            identity_suffix="assets",
        )
        parsed: dict[str, dict[float, _FrameResult]] = {
            identity: {} for identity in regions
        }
        if not plans:
            return parsed
        ready: asyncio.Queue[_PreparedFrameBatch | None] = asyncio.Queue(
            maxsize=self._settings.scan.batch_prefetch
        )
        progress_interval = self._settings.scan.progress_update_interval_batches
        pipeline_started = time.monotonic()
        first_batch_observed = False
        first_vbas_observed = False

        async def report_batch(
            completed: int,
            total: int,
            *,
            inference: bool,
        ) -> None:
            if progress is None:
                return
            if completed != total and completed % progress_interval:
                return
            if inference:
                await progress.report(
                    60,
                    "VBas 推理",
                    f"正在执行 VBas 推理（已完成 {completed}/{total} batch）",
                )
            else:
                await progress.report(
                    30,
                    "视觉抽帧",
                    f"正在抽取采样帧（已完成 {completed}/{total} batch）",
                )

        async def produce() -> None:
            nonlocal first_batch_observed
            try:
                for index, plan in enumerate(plans, start=1):
                    extracted = await self._frames.extract(
                        task_id=command.task_id,
                        stream=stream,
                        video_path=video_path,
                        timestamps=list(plan.timestamps),
                    )
                    by_timestamp = {
                        item.timestamp_seconds: item for item in extracted
                    }
                    prepared = tuple(by_timestamp[point] for point in plan.timestamps)
                    await ready.put(_PreparedFrameBatch(plan, prepared))
                    if self._metrics is not None:
                        self._metrics.record_batch(stream.value.lower(), "prepared")
                        if not first_batch_observed:
                            first_batch_observed = True
                            self._metrics.observe_first_batch_wait(
                                stream.value.lower(),
                                time.monotonic() - pipeline_started,
                            )
                    await report_batch(index, len(plans), inference=False)
            finally:
                await ready.put(None)

        producer = asyncio.create_task(
            produce(),
            name=f"vision-media-{command.task_id}-{stream.value.lower()}",
        )
        total_inference_batches = len(plans) * len(regions)
        completed_inference_batches = 0
        try:
            while True:
                prepared = await ready.get()
                if prepared is None:
                    break
                for identity_suffix, region in regions.items():
                    if progress is not None:
                        await progress.report(
                            45,
                            "VBas 容量等待",
                            "正在等待 VBas 离线容量",
                        )
                    frames = [
                        VbasFrame(
                            image_id=(
                                f"{stream.value.lower()}-{identity_suffix}-"
                                f"{round(frame.timestamp_seconds * 1000):012d}"
                            ),
                            path=frame.path,
                            frame_index=frame.frame_index,
                            timestamp_seconds=frame.timestamp_seconds,
                            points=region,
                        )
                        for frame in prepared.frames
                    ]
                    if self._metrics is not None and not first_vbas_observed:
                        first_vbas_observed = True
                        self._metrics.observe_first_vbas_request(
                            stream.value.lower(),
                            time.monotonic() - pipeline_started,
                        )
                    responses = await self._vbas.analyze(
                        task_id=command.task_id,
                        stream=stream,
                        frames=frames,
                        trace_id=str(command.command_id),
                    )
                    for frame, response in zip(frames, responses, strict=True):
                        item = response["response"]
                        counts, confidences = _parse_result_list(item, stream)
                        parsed[identity_suffix][frame.timestamp_seconds] = _FrameResult(
                            timestamp_seconds=frame.timestamp_seconds,
                            path=frame.path,
                            counts=counts,
                            confidences=confidences,
                        )
                    completed_inference_batches += 1
                    if self._metrics is not None:
                        self._metrics.record_batch(stream.value.lower(), "inferred")
                    await report_batch(
                        completed_inference_batches,
                        total_inference_batches,
                        inference=True,
                    )
            await producer
        finally:
            if not producer.done():
                producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)
        expected_points = {
            point for plan in plans for point in plan.timestamps
        }
        for identity_suffix, results in parsed.items():
            if set(results) != expected_points:
                raise RuntimeError(
                    f"视觉批次结果不完整: {identity_suffix} "
                    f"{len(results)}/{len(expected_points)}"
                )
        return parsed

    def _publish_teacher_evidence(
        self,
        task_id: str,
        observations: dict[float, _FrameResult],
    ) -> list[Any]:
        categories = {
            "writing": EvidenceCategory.TEACHER_WRITING,
            "sitting": EvidenceCategory.TEACHER_SITTING,
            "teaching": EvidenceCategory.TEACHER_TEACHING,
        }
        candidates = [
            EvidenceCandidate(
                category=category,
                capture_second=point,
                confidence=item.confidences.get(behavior, 1.0),
                source_path=item.path,
            )
            for point, item in observations.items()
            for behavior, category in categories.items()
            if item.counts.get(behavior, 0)
        ]
        return self._evidence.publish(task_id, candidates)

    def _publish_student_evidence(
        self,
        task_id: str,
        observations: dict[float, _FrameResult],
    ) -> list[Any]:
        categories = {
            "stable_person_count": EvidenceCategory.STUDENT_HEAD_UP,
            "read_count": EvidenceCategory.STUDENT_READING,
            "sleep_count": EvidenceCategory.STUDENT_SLEEPING,
            "phone_count": EvidenceCategory.STUDENT_PHONE_USE,
        }
        candidates: list[EvidenceCandidate] = []
        for point, item in observations.items():
            total = int(item.counts.get("detected_total", 0))
            for behavior, category in categories.items():
                count = int(item.counts.get(behavior, 0))
                if count <= 0:
                    continue
                confidence = min(1.0, count / total) if total > 0 else 0.0
                candidates.append(
                    EvidenceCandidate(category, point, confidence, item.path)
                )
        return self._evidence.publish(task_id, candidates)

    def _node_result(
        self,
        task_id: str,
        result: JsonObject,
        evidence: list[Any],
    ) -> NodeResultWrite:
        artifact_path = None
        if evidence:
            artifact_path = str(
                task_workspace(self._settings.storage.result_root, task_id) / "vision"
            )
        return NodeResultWrite(
            result=result,
            artifact_path=artifact_path,
            artifact_count=len(evidence),
        )


def build_course_visual_analyzer(
    repository: VisionRepository,
    frame_extractor: FFmpegFrameExtractor,
    vbas_client: VbasBatchClient,
    evidence_publisher: VisionEvidencePublisher,
    settings: VisionSettings,
) -> CourseVisualAnalyzer:
    return CourseVisualAnalyzer(
        repository,
        frame_extractor,
        vbas_client,
        evidence_publisher,
        settings=settings,
    )


def _parse_result_list(
    item: object,
    stream: VisionStream,
) -> tuple[JsonObject, dict[str, float]]:
    if not isinstance(item, dict):
        raise TypeError("VBas 单帧结果不是对象")
    status = item.get("StatusObject")
    if not isinstance(status, dict) or status.get("StatusCode") != 0:
        raise ValueError("VBas 单帧结果状态失败")
    raw_results = item.get("ResultList")
    if not isinstance(raw_results, list):
        raise TypeError("VBas 单帧结果缺少 ResultList")
    mapping = (
        TEACHER_OBJECT_TYPES if stream is VisionStream.TEACHER else STUDENT_OBJECT_TYPES
    )
    counts: JsonObject = {name: 0 for name in mapping.values()}
    confidences: dict[str, float] = {}
    for raw in raw_results:
        if not isinstance(raw, dict):
            continue
        try:
            name = mapping.get(int(raw.get("ObjectType", -1)))
            count = int(raw.get("ObjectCount", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("VBas ObjectType/ObjectCount 不合法") from exc
        if name is None:
            continue
        counts[name] = max(0, count)
        positions = raw.get("ObjectPostList")
        if isinstance(positions, list):
            confidence_values = [
                float(position["Confidence"])
                for position in positions
                if isinstance(position, dict)
                and isinstance(position.get("Confidence"), (int, float))
            ]
            if confidence_values:
                confidences[name] = min(1.0, max(0.0, max(confidence_values)))
    return counts, confidences


def _intervals_from_observations(
    observations: dict[float, _FrameResult],
    behavior: str,
    *,
    duration_seconds: float,
    fallback_step_seconds: float,
) -> list[BehaviorInterval]:
    points = sorted(observations)
    intervals: list[BehaviorInterval] = []
    for index, point in enumerate(points):
        if not observations[point].counts.get(behavior, 0):
            continue
        next_point = (
            points[index + 1]
            if index + 1 < len(points)
            else min(duration_seconds, point + fallback_step_seconds)
        )
        if next_point > point:
            intervals.append(BehaviorInterval(point, next_point))
    return intervals


def _sample_points(
    duration_seconds: float,
    interval_seconds: float,
    end_frame_margin_seconds: float,
) -> list[float]:
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("视频时长必须大于 0")
    points: list[float] = []
    point = 0.0
    last_safe = _safe_sampling_end(duration_seconds, end_frame_margin_seconds)
    while point <= last_safe:
        points.append(round(point, 6))
        point += interval_seconds
    return points or [0.0]


def _safe_sampling_end(
    duration_seconds: float,
    end_frame_margin_seconds: float,
) -> float:
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("视频时长必须大于 0")
    if not math.isfinite(end_frame_margin_seconds) or end_frame_margin_seconds <= 0:
        raise ValueError("视频末端抽帧裕量必须大于 0")
    return max(
        duration_seconds - end_frame_margin_seconds,
        duration_seconds / 2,
    )


def _strategy_positive_float(
    strategy: JsonObject,
    key: str,
    default: float,
) -> float:
    value = strategy.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"视觉策略 {key} 必须是正数")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(f"视觉策略 {key} 必须是正数")
    return parsed


def _strategy_positive_floats(
    strategy: JsonObject,
    key: str,
    default: tuple[float, ...],
) -> tuple[float, ...]:
    value = strategy.get(key, default)
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(f"视觉策略 {key} 必须是非空正数列表")
    return tuple(_strategy_positive_float({key: item}, key, 1.0) for item in value)


def _evidence_result(evidence: list[Any]) -> list[JsonObject]:
    return [
        {
            "category": item.category.value,
            "capture_second": item.capture_second,
            "confidence": item.confidence,
            "path": str(item.path),
        }
        for item in evidence
    ]
