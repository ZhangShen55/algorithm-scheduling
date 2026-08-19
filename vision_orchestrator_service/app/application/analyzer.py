from __future__ import annotations

import asyncio
import math
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
from ..infrastructure.media import ExtractedFrame, FFmpegFrameExtractor
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


class CourseVisualAnalyzer:
    def __init__(
        self,
        repository: VisionRepository,
        frame_extractor: FrameExtractor,
        vbas_client: VbasBatchClient,
        evidence_publisher: VisionEvidencePublisher,
        *,
        settings: VisionSettings,
    ) -> None:
        self._repository = repository
        self._frames = frame_extractor
        self._vbas = vbas_client
        self._evidence = evidence_publisher
        self._settings = settings

    async def analyze(
        self,
        command: VisualAnalysisCommand,
        progress: ProgressCallback,
    ) -> NodeResultWrite:
        video_path = self._validated_command_video(command)
        await progress(5, "视频校验", "正在校验本地视觉视频")
        duration = await self._frames.duration_seconds(video_path)
        if command.task_type is TaskType.TEACHER_BEHAVIOR:
            return await self._analyze_teacher(command, video_path, duration, progress)
        return await self._analyze_student(command, video_path, duration, progress)

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
        progress: ProgressCallback,
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
            )
            observations.update(inferred)
            return {
                point: bool(
                    inferred[point].counts.get("writing", 0)
                    or inferred[point].counts.get("sitting", 0)
                )
                for point in selected
            }

        await progress(20, "粗粒度扫描", "正在执行教师行为粗粒度扫描")
        scan_duration = max(min(duration, duration - 0.001), 0.001)
        scan = await planner.scan_async(
            duration_seconds=scan_duration,
            detector=detect,
        )
        await progress(75, "边界细化", "正在聚合教师行为区间")
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
        await progress(95, "结果持久化", outcome.reason)
        return self._node_result(command.task_id, result, evidence)

    async def _analyze_student(
        self,
        command: VisualAnalysisCommand,
        video_path: Path,
        duration: float,
        progress: ProgressCallback,
    ) -> NodeResultWrite:
        interval = _strategy_positive_float(
            command.strategy,
            "interval_seconds",
            self._settings.scan.default_interval_seconds,
        )
        points = _sample_points(duration, interval)
        await progress(20, "学生抽帧", "正在抽取学生视频采样帧")
        total = await self._infer(
            command,
            VisionStream.STUDENT,
            video_path,
            points,
        )
        front = (
            await self._infer(
                command,
                VisionStream.STUDENT,
                video_path,
                points,
                region=command.front_points,
                identity_suffix="front",
            )
            if command.front_points
            else {}
        )
        back = (
            await self._infer(
                command,
                VisionStream.STUDENT,
                video_path,
                points,
                region=command.back_point,
                identity_suffix="back",
            )
            if command.back_point
            else {}
        )
        await progress(75, "学生聚合", "正在聚合学生人数和行为结果")
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
        await progress(95, "结果持久化", "学生行为分析完成")
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
    ) -> dict[float, _FrameResult]:
        extracted = await self._frames.extract(
            task_id=command.task_id,
            stream=stream,
            video_path=video_path,
            timestamps=points,
        )
        by_timestamp = {item.timestamp_seconds: item for item in extracted}
        frames = [
            VbasFrame(
                image_id=(
                    f"{stream.value.lower()}-{identity_suffix}-"
                    f"{round(point * 1000):012d}"
                ),
                path=by_timestamp[point].path,
                frame_index=round(point * 1000),
                timestamp_seconds=point,
                points=region,
            )
            for point in points
        ]
        responses = await self._vbas.analyze(
            task_id=command.task_id,
            stream=stream,
            frames=frames,
            trace_id=str(command.command_id),
        )
        parsed: dict[float, _FrameResult] = {}
        for frame, response in zip(frames, responses, strict=True):
            item = response["response"]
            counts, confidences = _parse_result_list(item, stream)
            parsed[frame.timestamp_seconds] = _FrameResult(
                timestamp_seconds=frame.timestamp_seconds,
                path=frame.path,
                counts=counts,
                confidences=confidences,
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


def _sample_points(duration_seconds: float, interval_seconds: float) -> list[float]:
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("视频时长必须大于 0")
    points: list[float] = []
    point = 0.0
    last_safe = max(duration_seconds - 0.001, 0.0)
    while point <= last_safe:
        points.append(round(point, 6))
        point += interval_seconds
    return points or [0.0]


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
