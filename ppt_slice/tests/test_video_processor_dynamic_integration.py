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
        while not task.frame_queue.empty():
            timestamps.append(task.frame_queue.get_nowait().timestamp_ms)
        intervals = [right - left for left, right in zip(timestamps, timestamps[1:])]
        self.assertGreaterEqual(len(timestamps), 10)
        self.assertLessEqual(max(intervals), 1100)
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
        while not task.frame_queue.empty():
            timestamps.append(task.frame_queue.get_nowait().timestamp_ms)
        self.assertEqual(timestamps, [0, 10000])
        self.assertEqual(container.video_stream.codec_context.skip_frame, "NONKEY")

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
