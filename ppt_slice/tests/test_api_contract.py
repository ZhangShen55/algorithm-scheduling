import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.api.v1 import video
from app.schemas import TerminalResultCallback, VideoPPTCutRequest
from app.services.task_manager import TaskManager
from fastapi import BackgroundTasks
from pydantic import ValidationError


class RequestSchemaTests(unittest.TestCase):
    def test_request_uses_internal_snake_case_contract(self):
        request = VideoPPTCutRequest(
            video_path="/data/course/course-001/media/slides.mp4",
            task_id="course-001",
            operator_task_id="ppt-run-001",
            result_callback_uri="http://orchestrator/internal/ppt-slice/callback",
            threshold=0.98,
        )

        self.assertEqual(request.video_path, "/data/course/course-001/media/slides.mp4")
        self.assertEqual(request.task_id, "course-001")
        self.assertEqual(request.operator_task_id, "ppt-run-001")
        self.assertEqual(request.model_dump()["result_callback_uri"], request.result_callback_uri)

    def test_request_accepts_remote_url_and_absolute_local_path(self):
        for video_path in (
            "https://media.example/course-001/PPT.mp4",
            "/data/course/course-001/media/slides.mp4",
        ):
            with self.subTest(video_path=video_path):
                request = VideoPPTCutRequest(
                    video_path=video_path,
                    task_id="course-001",
                    operator_task_id="ppt-run-001",
                    result_callback_uri="http://orchestrator/internal/ppt-slice/callback",
                )
                self.assertEqual(request.video_path, video_path)

    def test_request_normalizes_legacy_uri_to_video_path(self):
        request = VideoPPTCutRequest.model_validate(
            {
                "uri": "https://media.example/course-001/PPT.mp4",
                "task_id": "course-001",
                "operator_task_id": "ppt-run-001",
                "result_callback_uri": "http://orchestrator/internal/ppt-slice/callback",
            }
        )

        self.assertEqual(request.video_path, "https://media.example/course-001/PPT.mp4")
        self.assertNotIn("uri", request.model_dump())

    def test_request_rejects_relative_local_path(self):
        with self.assertRaises(ValidationError):
            VideoPPTCutRequest(
                video_path="media/slides.mp4",
                task_id="course-001",
                operator_task_id="ppt-run-001",
                result_callback_uri="http://orchestrator/internal/ppt-slice/callback",
            )

    def test_request_rejects_path_traversal_identifiers(self):
        for unsafe in ("../escape", ".", ".."):
            with self.subTest(task_id=unsafe), self.assertRaises(ValidationError):
                VideoPPTCutRequest(
                    video_path="/data/course/course-001/media/slides.mp4",
                    task_id=unsafe,
                    operator_task_id="ppt-run-001",
                    result_callback_uri="http://orchestrator/internal/ppt-slice/callback",
                )

    def test_request_rejects_legacy_camel_case_fields(self):
        with self.assertRaises(ValidationError):
            VideoPPTCutRequest.model_validate(
                {
                    "video_path": "/data/course/course-001/media/slides.mp4",
                    "taskId": "course-001",
                    "resultCallbackUri": "http://orchestrator/internal/ppt-slice/callback",
                    "threshold": 0.98,
                }
            )

    def test_terminal_callback_accepts_dynamic_segments_without_changing_existing_fields(self):
        callback = TerminalResultCallback(
            task_id="course-001",
            operator_task_id="ppt-run-001",
            status=60,
            path="/data/result/course-001/ppt/slices",
            manifest_path="/data/result/course-001/ppt/manifest.json",
            count=1,
            reason="",
            dynamic_segments=[
                {
                    "type": "SUSPECTED_VIDEO_PLAYBACK",
                    "start_ms": 1000,
                    "end_ms": 9000,
                    "confidence": 0.8,
                    "reason": "sustained_visual_change",
                }
            ],
        )

        self.assertEqual(callback.count, 1)
        self.assertEqual(callback.dynamic_segments[0].start_ms, 1000)


class SubmissionCapacityTests(unittest.TestCase):
    def test_submission_accepts_n_tasks_and_rejects_n_plus_one(self):
        manager = TaskManager()
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            video, "task_manager", manager
        ), patch.object(video.settings, "MAX_CONCURRENT_TASKS", 2), patch.object(
            video.settings, "RESULT_ROOT", Path(temp_dir)
        ):
            responses = []
            for index in range(3):
                request = VideoPPTCutRequest(
                    video_path=f"/data/course/course-{index}/media/slides.mp4",
                    task_id=f"course-{index}",
                    operator_task_id=f"ppt-run-{index}",
                    result_callback_uri="http://orchestrator/internal/ppt-slice/callback",
                )
                responses.append(
                    asyncio.run(video.process_rtsp(request, BackgroundTasks()))
                )

        self.assertEqual([response.status for response in responses], [50, 50, 70])
        self.assertEqual(manager.get_task_count(), 2)
        self.assertEqual(responses[-1].reason, "当前任务数已达到最大值[2]，请稍后重试")

    def test_same_inflight_operator_task_is_idempotently_accepted(self):
        manager = TaskManager()
        request = VideoPPTCutRequest(
            video_path="/data/course/course-001/media/slides.mp4",
            task_id="course-001",
            operator_task_id="ppt-run-001",
            result_callback_uri="http://orchestrator/internal/ppt-slice/callback",
        )
        first_background = BackgroundTasks()
        duplicate_background = BackgroundTasks()
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            video, "task_manager", manager
        ), patch.object(video.settings, "MAX_CONCURRENT_TASKS", 2), patch.object(
            video.settings, "RESULT_ROOT", Path(temp_dir)
        ):
            first = asyncio.run(video.process_rtsp(request, first_background))
            duplicate = asyncio.run(video.process_rtsp(request, duplicate_background))

        self.assertEqual(first.status, 50)
        self.assertEqual(duplicate.status, 50)
        self.assertEqual(duplicate.reason, "相同 PPT 切片任务已受理")
        self.assertEqual(manager.get_task_count(), 1)
        self.assertEqual(len(first_background.tasks), 1)
        self.assertEqual(len(duplicate_background.tasks), 0)

    def test_same_operator_task_with_conflicting_payload_is_rejected(self):
        manager = TaskManager()
        first_request = VideoPPTCutRequest(
            video_path="/data/course/course-001/media/slides.mp4",
            task_id="course-001",
            operator_task_id="ppt-run-001",
            result_callback_uri="http://orchestrator/internal/ppt-slice/callback",
        )
        conflicting_request = first_request.model_copy(
            update={"video_path": "/data/course/course-002/media/slides.mp4"}
        )
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            video, "task_manager", manager
        ), patch.object(video.settings, "MAX_CONCURRENT_TASKS", 2), patch.object(
            video.settings, "RESULT_ROOT", Path(temp_dir)
        ):
            first = asyncio.run(video.process_rtsp(first_request, BackgroundTasks()))
            conflict = asyncio.run(
                video.process_rtsp(conflicting_request, BackgroundTasks())
            )

        self.assertEqual(first.status, 50)
        self.assertEqual(conflict.status, 70)
        self.assertEqual(conflict.reason, "operator_task_id 已存在且请求内容不一致")
        self.assertEqual(manager.get_task_count(), 1)


if __name__ == "__main__":
    unittest.main()
