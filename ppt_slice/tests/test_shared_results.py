import asyncio
import importlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from queue import Queue
from unittest.mock import AsyncMock, patch

import numpy as np

from app.models.task import LocalVideoAnalysisTaskObject
from app.models.task import FrameData
from app.services import video_processor
from app.services.task_manager import TaskManager


def _shared_result_module():
    try:
        return importlib.import_module("app.services.shared_result")
    except ModuleNotFoundError:
        return None


def _task(task_id: str = "course-001", operator_task_id: str = "ppt-run-001"):
    return LocalVideoAnalysisTaskObject(
        task_id=task_id,
        operator_task_id=operator_task_id,
        video_id=task_id,
        video_path="/data/course/course-001/media/slides.mp4",
        result_callback_uri="http://orchestrator/internal/ppt-slice/callback",
    )


class SharedResultWriterTests(unittest.TestCase):
    def setUp(self):
        self.shared_result = _shared_result_module()
        self.assertIsNotNone(
            self.shared_result,
            "缺少共享结果写入模块 app.services.shared_result",
        )

    def test_success_writes_images_and_manifest_to_fixed_shared_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = self.shared_result.SharedResultWriter(
                result_root=Path(temp_dir),
                task_id="course-001",
                operator_task_id="ppt-run-001",
            )
            frame = np.full((8, 8, 3), 127, dtype=np.uint8)

            image = writer.write_image(frame_seq=17, snap_time=16, frame=frame)
            manifest_path = writer.write_manifest(status=60)

            expected_dir = Path(temp_dir).resolve() / "course-001" / "ppt" / "slices"
            self.assertEqual(writer.output_dir, expected_dir)
            self.assertTrue(Path(image.path).is_file())
            self.assertEqual(manifest_path, expected_dir.parent / "manifest.json")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(manifest["task_id"], "course-001")
            self.assertEqual(manifest["operator_task_id"], "ppt-run-001")
            self.assertEqual(manifest["status"], 60)
            self.assertEqual(manifest["path"], str(expected_dir))
            self.assertEqual(manifest["count"], 1)
            self.assertEqual(
                manifest["images"],
                [{"frame_seq": 17, "snap_time": 16, "path": image.path}],
            )
            self.assertFalse(list(expected_dir.glob("*.part")))
            self.assertFalse(list(expected_dir.parent.glob("*.part")))

    def test_images_and_manifest_are_published_with_atomic_replace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = self.shared_result.SharedResultWriter(
                result_root=Path(temp_dir),
                task_id="course-atomic",
                operator_task_id="ppt-run-atomic",
            )
            frame = np.zeros((8, 8, 3), dtype=np.uint8)

            with patch("app.services.shared_result.os.replace", wraps=self.shared_result.os.replace) as replace:
                writer.write_image(frame_seq=1, snap_time=0, frame=frame)
                writer.write_manifest(status=60)

            replacements = [(Path(call.args[0]), Path(call.args[1])) for call in replace.call_args_list]
            self.assertEqual(len(replacements), 2)
            for partial, final in replacements:
                self.assertEqual(partial.name, f"{final.name}.part")
                self.assertTrue(final.exists())
                self.assertFalse(partial.exists())

    def test_task_identifiers_cannot_escape_result_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            for unsafe in ("../escape", "/tmp/escape", "nested/task", "..", ".", ""):
                with self.subTest(task_id=unsafe):
                    with self.assertRaises(ValueError):
                        self.shared_result.SharedResultWriter(
                            result_root=Path(temp_dir),
                            task_id=unsafe,
                            operator_task_id="ppt-run-safe",
                        )
            with self.assertRaises(ValueError):
                self.shared_result.SharedResultWriter(
                    result_root=Path(temp_dir),
                    task_id="course-safe",
                    operator_task_id="../operator-escape",
                )

    def test_existing_symlink_cannot_redirect_output_outside_result_root(self):
        with tempfile.TemporaryDirectory() as temp_dir, tempfile.TemporaryDirectory() as outside:
            result_root = Path(temp_dir)
            (result_root / "course-link").symlink_to(Path(outside), target_is_directory=True)

            with self.assertRaises(ValueError):
                self.shared_result.SharedResultWriter(
                    result_root=result_root,
                    task_id="course-link",
                    operator_task_id="ppt-run-safe",
                )

            self.assertFalse((Path(outside) / "ppt").exists())


class TerminalPublisherTests(unittest.IsolatedAsyncioTestCase):
    async def test_terminal_callback_is_sent_once_without_base64(self):
        shared_result = _shared_result_module()
        self.assertIsNotNone(
            shared_result,
            "缺少共享结果写入模块 app.services.shared_result",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = shared_result.SharedResultWriter(
                result_root=Path(temp_dir),
                task_id="course-once",
                operator_task_id="ppt-run-once",
            )
            writer.write_image(
                frame_seq=2,
                snap_time=1,
                frame=np.zeros((8, 8, 3), dtype=np.uint8),
            )
            callback = AsyncMock(return_value=True)
            publisher = shared_result.TerminalResultPublisher(writer, callback)

            first = await publisher.publish_once(status=60)
            second = await publisher.publish_once(status=70, reason="late error")

            self.assertTrue(first)
            self.assertFalse(second)
            callback.assert_awaited_once()
            payload = callback.await_args.args[0]
            self.assertEqual(payload["status"], 60)
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["path"], str(writer.output_dir))
            self.assertEqual(payload["manifest_path"], str(writer.output_dir.parent / "manifest.json"))
            self.assertEqual(payload["reason"], "")
            self.assertNotIn("snapImage", payload)
            self.assertNotIn("snap_image", payload)

    async def test_terminal_callback_failure_is_logged_without_retry(self):
        shared_result = _shared_result_module()
        self.assertIsNotNone(
            shared_result,
            "缺少共享结果写入模块 app.services.shared_result",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = shared_result.SharedResultWriter(
                result_root=Path(temp_dir),
                task_id="course-callback-failure",
                operator_task_id="ppt-run-callback-failure",
            )
            callback = AsyncMock(side_effect=RuntimeError("callback unavailable"))
            publisher = shared_result.TerminalResultPublisher(writer, callback)

            with patch.object(shared_result.logger, "error") as log_error:
                published = await publisher.publish_once(status=70, reason="processing failed")

            self.assertTrue(published)
            callback.assert_awaited_once()
            log_error.assert_called_once()


class TaskManagerCapacityTests(unittest.TestCase):
    def test_capacity_accepts_exactly_n_active_tasks(self):
        manager = TaskManager()
        first = _task(task_id="course-1", operator_task_id="ppt-run-1")
        second = _task(task_id="course-2", operator_task_id="ppt-run-2")
        third = _task(task_id="course-3", operator_task_id="ppt-run-3")

        self.assertTrue(manager.try_add_task(first.task_id, first, max_tasks=2))
        self.assertTrue(manager.try_add_task(second.task_id, second, max_tasks=2))
        self.assertFalse(manager.try_add_task(third.task_id, third, max_tasks=2))
        self.assertEqual(manager.get_task_count(), 2)

    def test_concurrent_capacity_check_does_not_oversubscribe(self):
        manager = TaskManager()

        async def reserve(index: int) -> bool:
            task = _task(
                task_id=f"course-{index}",
                operator_task_id=f"ppt-run-{index}",
            )
            return await asyncio.to_thread(
                manager.try_add_task,
                task.task_id,
                task,
                max_tasks=3,
            )

        async def run_all():
            return await asyncio.gather(*(reserve(index) for index in range(20)))

        accepted = asyncio.run(run_all())
        self.assertEqual(sum(accepted), 3)
        self.assertEqual(manager.get_task_count(), 3)


class ProcessingPipelineTests(unittest.TestCase):
    def test_success_pipeline_writes_manifest_and_only_one_terminal_callback(self):
        shared_result = _shared_result_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = shared_result.SharedResultWriter(
                result_root=Path(temp_dir),
                task_id="course-pipeline",
                operator_task_id="ppt-run-pipeline",
            )
            callback = AsyncMock(return_value=True)
            publisher = shared_result.TerminalResultPublisher(writer, callback)
            frame_queue = Queue()
            for timestamp in range(6):
                frame_queue.put(
                    FrameData(
                        frame=np.zeros((8, 8, 3), dtype=np.uint8),
                        timestamp=timestamp,
                    )
                )
            task = LocalVideoAnalysisTaskObject(
                task_id="course-pipeline",
                operator_task_id="ppt-run-pipeline",
                video_id="course-pipeline",
                video_path="/data/course/course-pipeline/media/slides.mp4",
                result_callback_uri="http://orchestrator/internal/ppt-slice/callback",
                frame_queue=frame_queue,
                result_writer=writer,
                terminal_publisher=publisher,
            )
            task.stream_finished_event.set()
            with patch.object(video_processor, "compare_images", return_value=1.0):
                video_processor.process_frames(task, threading.Event())

            callback.assert_awaited_once()
            manifest = json.loads(writer.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], 60)
            self.assertEqual(manifest["count"], 1)


if __name__ == "__main__":
    unittest.main()
