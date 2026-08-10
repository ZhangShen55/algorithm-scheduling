import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import BackgroundTasks
from pydantic import ValidationError

from app.api.v1 import video
from app.schemas import TerminalResultCallback, VideoPPTCutRequest
from app.services.task_manager import TaskManager


class RequestSchemaTests(unittest.TestCase):
    def test_request_uses_internal_snake_case_contract(self):
        request = VideoPPTCutRequest(
            uri="/data/course/course-001/media/slides.mp4",
            task_id="course-001",
            operator_task_id="ppt-run-001",
            result_callback_uri="http://orchestrator/internal/ppt-slice/callback",
            threshold=0.98,
        )

        self.assertEqual(request.task_id, "course-001")
        self.assertEqual(request.operator_task_id, "ppt-run-001")
        self.assertEqual(request.model_dump()["result_callback_uri"], request.result_callback_uri)

    def test_request_rejects_path_traversal_identifiers(self):
        for unsafe in ("../escape", ".", ".."):
            with self.subTest(task_id=unsafe), self.assertRaises(ValidationError):
                VideoPPTCutRequest(
                    uri="/data/course/course-001/media/slides.mp4",
                    task_id=unsafe,
                    operator_task_id="ppt-run-001",
                    result_callback_uri="http://orchestrator/internal/ppt-slice/callback",
                )

    def test_request_rejects_legacy_camel_case_fields(self):
        with self.assertRaises(ValidationError):
            VideoPPTCutRequest.model_validate(
                {
                    "uri": "/data/course/course-001/media/slides.mp4",
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
                    uri=f"/data/course/course-{index}/media/slides.mp4",
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


if __name__ == "__main__":
    unittest.main()
