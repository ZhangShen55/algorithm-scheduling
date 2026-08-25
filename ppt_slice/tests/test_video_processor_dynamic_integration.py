import tempfile
import threading
import unittest
from fractions import Fraction
from pathlib import Path
from queue import Queue
from unittest.mock import AsyncMock, patch

import av
import numpy as np

from app.models.task import FrameData, LocalVideoAnalysisTaskObject
from app.services import video_processor
from app.services.shared_result import SharedResultWriter, TerminalResultPublisher


class _FakeCodecContext:
    skip_frame = "DEFAULT"


class _FakeVideoStream:
    width = 64
    height = 64
    average_rate = Fraction(2, 1)
    time_base = Fraction(1, 1000)
    codec_context = _FakeCodecContext()


class _FakePacket:
    def __init__(self, timestamp_ms: int, *, is_keyframe: bool):
        self.is_keyframe = is_keyframe
        frame = av.VideoFrame.from_ndarray(
            np.full((64, 64, 3), timestamp_ms // 100, dtype=np.uint8),
            format="bgr24",
        )
        frame.pts = timestamp_ms
        frame.time_base = Fraction(1, 1000)
        self._frame = frame

    def decode(self):
        return [self._frame]


class _FakeContainer:
    def __init__(self):
        self.video_stream = _FakeVideoStream()
        self.streams = type("Streams", (), {"video": [self.video_stream]})()
        self.closed = False

    def demux(self, stream):
        assert stream is self.video_stream
        return [
            _FakePacket(timestamp_ms, is_keyframe=timestamp_ms in {0, 10000})
            for timestamp_ms in range(0, 16000, 1000)
        ]

    def close(self):
        self.closed = True


class _ShortFakeContainer(_FakeContainer):
    def demux(self, stream):
        assert stream is self.video_stream
        return [
            _FakePacket(timestamp_ms, is_keyframe=timestamp_ms == 0)
            for timestamp_ms in range(0, 5000, 100)
        ]


class _NoEmptyPollingQueue(Queue):
    def get(self, block=True, timeout=None):
        if self.empty():
            raise AssertionError("clean EOF must terminate through the queue sentinel")
        return super().get(block=block, timeout=timeout)


class VideoProcessorDynamicIntegrationTests(unittest.TestCase):
    def test_open_stream_decodes_absolute_local_video_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "PPT.mp4"
            output = av.open(str(video_path), mode="w")
            stream = output.add_stream("mpeg4", rate=2)
            stream.width = 64
            stream.height = 64
            stream.pix_fmt = "yuv420p"
            for index in range(2):
                frame = av.VideoFrame.from_ndarray(
                    np.full((64, 64, 3), index * 80, dtype=np.uint8),
                    format="bgr24",
                )
                for packet in stream.encode(frame):
                    output.mux(packet)
            for packet in stream.encode():
                output.mux(packet)
            output.close()

            task = LocalVideoAnalysisTaskObject(
                task_id="course-local-path",
                operator_task_id="ppt-run-local-path",
                video_id="course-local-path",
                video_path=str(video_path),
                result_callback_uri="http://orchestrator/internal/ppt-slice/callback",
            )
            error_event = threading.Event()
            container = video_processor.open_stream(task, error_event)
            self.assertIsNotNone(container)
            try:
                frames = list(container.decode(video=0))
            finally:
                container.close()

            self.assertFalse(error_event.is_set())
            self.assertEqual(len(frames), 2)

    def test_get_stream_time_samples_reference_frames_when_dynamic_detection_is_enabled(self):
        container = _FakeContainer()
        task = LocalVideoAnalysisTaskObject(
            task_id="course-reference-sampling",
            operator_task_id="ppt-run-reference-sampling",
            video_id="course-reference-sampling",
            video_path="memory://PPT.mp4",
            result_callback_uri="http://orchestrator/internal/ppt-slice/callback",
            frame_queue=Queue(maxsize=64),
        )

        with (
            patch.object(video_processor, "open_stream", return_value=container),
            patch.object(video_processor.settings, "DYNAMIC_DETECTION_ENABLED", True),
            patch.object(video_processor.settings, "DYNAMIC_SAMPLE_INTERVAL_MS", 1000),
        ):
            video_processor.get_stream(task, threading.Event())

        timestamps = []
        while True:
            item = task.frame_queue.get_nowait()
            if item is video_processor._FRAME_QUEUE_EOF:
                break
            timestamps.append(item.timestamp_ms)
        intervals = [right - left for left, right in zip(timestamps, timestamps[1:])]
        self.assertGreaterEqual(len(timestamps), 10)
        self.assertLessEqual(max(intervals), 1100)
        self.assertTrue(task.frame_queue.empty())
        self.assertTrue(task.stream_finished_event.is_set())

    def test_get_stream_keeps_keyframe_only_path_when_dynamic_detection_is_disabled(self):
        container = _FakeContainer()
        task = LocalVideoAnalysisTaskObject(
            task_id="course-keyframe-rollback",
            operator_task_id="ppt-run-keyframe-rollback",
            video_id="course-keyframe-rollback",
            video_path="memory://PPT.mp4",
            result_callback_uri="http://orchestrator/internal/ppt-slice/callback",
            frame_queue=Queue(maxsize=64),
        )

        with (
            patch.object(video_processor, "open_stream", return_value=container),
            patch.object(video_processor.settings, "DYNAMIC_DETECTION_ENABLED", False),
        ):
            video_processor.get_stream(task, threading.Event())

        timestamps = []
        while True:
            item = task.frame_queue.get_nowait()
            if item is video_processor._FRAME_QUEUE_EOF:
                break
            timestamps.append(item.timestamp_ms)
        self.assertEqual(timestamps, [0, 10000])
        self.assertTrue(task.frame_queue.empty())
        self.assertEqual(container.video_stream.codec_context.skip_frame, "NONKEY")

    def test_clean_eof_with_exact_minimum_frames_finishes_without_empty_queue_polling(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = SharedResultWriter(
                result_root=Path(temp_dir),
                task_id="course-short-clean-eof",
                operator_task_id="ppt-run-short-clean-eof",
            )
            publisher = TerminalResultPublisher(writer, AsyncMock(return_value=True))
            task = LocalVideoAnalysisTaskObject(
                task_id="course-short-clean-eof",
                operator_task_id="ppt-run-short-clean-eof",
                video_id="course-short-clean-eof",
                video_path="http://example.test/PPT.mp4",
                result_callback_uri="http://orchestrator/internal/ppt-slice/callback",
                frame_queue=_NoEmptyPollingQueue(maxsize=64),
                result_writer=writer,
                terminal_publisher=publisher,
            )
            error_event = threading.Event()

            with (
                patch.object(video_processor, "open_stream", return_value=_ShortFakeContainer()),
                patch.object(video_processor.settings, "DYNAMIC_DETECTION_ENABLED", True),
                patch.object(video_processor.settings, "DYNAMIC_SAMPLE_INTERVAL_MS", 1000),
                patch.object(video_processor, "MIN_FRAMES_OK", 5),
            ):
                video_processor.get_stream(task, error_event)
                video_processor.process_frames(task, error_event)

            self.assertFalse(error_event.is_set())
            self.assertEqual(task.file_frame_sum, 50)
            self.assertEqual(task.task_status_code, 2)
            manifest = writer.build_manifest(status=60)
            self.assertEqual(manifest["count"], 1)
            self.assertEqual(writer.manifest_path.read_text(encoding="utf-8").count('"status": 60'), 1)

    def test_queue_timeout_while_producer_is_running_fails_and_cancels(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = SharedResultWriter(
                result_root=Path(temp_dir),
                task_id="course-frame-timeout",
                operator_task_id="ppt-run-frame-timeout",
            )
            publisher = TerminalResultPublisher(writer, AsyncMock(return_value=True))
            task = LocalVideoAnalysisTaskObject(
                task_id="course-frame-timeout",
                operator_task_id="ppt-run-frame-timeout",
                video_id="course-frame-timeout",
                video_path="http://example.test/PPT.mp4",
                result_callback_uri="http://orchestrator/internal/ppt-slice/callback",
                result_writer=writer,
                terminal_publisher=publisher,
            )

            with patch.object(
                video_processor,
                "get_frame_from_queue",
                return_value=(None, False),
            ):
                video_processor.process_frames(task, threading.Event())

            self.assertTrue(task.cancel_event.is_set())
            self.assertEqual(task.failure_reason, "等待视频帧超时")
            manifest = writer.build_manifest(status=70, reason=task.failure_reason)
            self.assertEqual(manifest["reason"], "等待视频帧超时")

    def test_producer_error_reason_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = SharedResultWriter(
                result_root=Path(temp_dir),
                task_id="course-producer-error",
                operator_task_id="ppt-run-producer-error",
            )
            publisher = TerminalResultPublisher(writer, AsyncMock(return_value=True))
            task = LocalVideoAnalysisTaskObject(
                task_id="course-producer-error",
                operator_task_id="ppt-run-producer-error",
                video_id="course-producer-error",
                video_path="http://example.test/PPT.mp4",
                result_callback_uri="http://orchestrator/internal/ppt-slice/callback",
                result_writer=writer,
                terminal_publisher=publisher,
            )
            error_event = threading.Event()
            task.mark_failed("producer failed", 10)
            task.frame_queue.put(video_processor._FRAME_QUEUE_ERROR)
            error_event.set()
            task.stream_finished_event.set()

            video_processor.process_frames(task, error_event)

            self.assertEqual(task.failure_reason, "producer failed")
            self.assertEqual(task.task_status_code, 10)
            self.assertIn('"reason": "producer failed"', writer.manifest_path.read_text(encoding="utf-8"))

    def test_cancel_is_not_reported_as_clean_eof(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = SharedResultWriter(
                result_root=Path(temp_dir),
                task_id="course-cancelled",
                operator_task_id="ppt-run-cancelled",
            )
            publisher = TerminalResultPublisher(writer, AsyncMock(return_value=True))
            task = LocalVideoAnalysisTaskObject(
                task_id="course-cancelled",
                operator_task_id="ppt-run-cancelled",
                video_id="course-cancelled",
                video_path="http://example.test/PPT.mp4",
                result_callback_uri="http://orchestrator/internal/ppt-slice/callback",
                result_writer=writer,
                terminal_publisher=publisher,
            )
            task.cancel_event.set()

            video_processor.process_frames(task, threading.Event())

            self.assertEqual(task.task_status_code, 4)
            self.assertIn('"reason": "任务已取消"', writer.manifest_path.read_text(encoding="utf-8"))

    def test_process_frames_routes_all_frames_through_slice_pipeline_and_finishes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = SharedResultWriter(
                result_root=Path(temp_dir),
                task_id="course-pipeline-integration",
                operator_task_id="ppt-run-pipeline-integration",
            )
            publisher = TerminalResultPublisher(writer, AsyncMock(return_value=True))
            frame_queue = Queue()
            for timestamp_ms in range(0, 6000, 1000):
                frame_queue.put(
                    FrameData(
                        frame=np.zeros((8, 8, 3), dtype=np.uint8),
                        timestamp_ms=timestamp_ms,
                    )
                )
            frame_queue.put(video_processor._FRAME_QUEUE_EOF)
            task = LocalVideoAnalysisTaskObject(
                task_id="course-pipeline-integration",
                operator_task_id="ppt-run-pipeline-integration",
                video_id="course-pipeline-integration",
                video_path="http://example.test/PPT.mp4",
                result_callback_uri="http://orchestrator/internal/ppt-slice/callback",
                frame_queue=frame_queue,
                result_writer=writer,
                terminal_publisher=publisher,
            )
            task.stream_finished_event.set()

            with patch.object(video_processor, "SlicePipeline") as pipeline_type:
                pipeline = pipeline_type.return_value
                video_processor.process_frames(task, threading.Event())

            self.assertEqual(pipeline.observe.call_count, 6)
            pipeline.finish.assert_called_once_with(5000)
            self.assertEqual(task.task_status_code, 2)

    def test_frame_data_preserves_millisecond_timestamp_and_legacy_seconds(self):
        frame = FrameData(frame=np.zeros((1, 1, 3)), timestamp_ms=2837)

        self.assertEqual(frame.timestamp_ms, 2837)
        self.assertEqual(frame.timestamp, 2)


if __name__ == "__main__":
    unittest.main()
