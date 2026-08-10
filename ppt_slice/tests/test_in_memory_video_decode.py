import io
import unittest
from fractions import Fraction

import av
import numpy as np

from app.services.slice_pipeline import SlicePipelineConfig
from harness.tools import detect
from harness.tools.detect import detect_frames, stream_keyframes


def _encode_video_in_memory(images, fps=2, gop_size=1):
    buffer = io.BytesIO()
    container = av.open(buffer, mode="w", format="mp4")
    stream = container.add_stream("mpeg4", rate=fps)
    stream.width = images[0].shape[1]
    stream.height = images[0].shape[0]
    stream.pix_fmt = "yuv420p"
    stream.codec_context.gop_size = gop_size
    for index, image in enumerate(images):
        frame = av.VideoFrame.from_ndarray(image, format="bgr24")
        frame.pts = index
        frame.time_base = Fraction(1, fps)
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()
    buffer.seek(0)
    return buffer


class InMemoryVideoDecodeTests(unittest.TestCase):
    def test_time_sampled_reference_frames_detect_short_dynamic_content_in_long_gop(self):
        self.assertTrue(
            hasattr(detect, "stream_sampled_frames"),
            "动态检测需要固定时间参考帧采样入口",
        )
        black = np.zeros((64, 64, 3), dtype=np.uint8)
        white = np.full((64, 64, 3), 255, dtype=np.uint8)
        images = [black] * 8
        images.extend(
            np.full((64, 64, 3), 20 + index * 10, dtype=np.uint8)
            for index in range(16)
        )
        images.extend([white] * 8)
        source = _encode_video_in_memory(images, fps=2, gop_size=20)
        config = SlicePipelineConfig(
            dynamic_detection_enabled=True,
            sample_interval_ms=1000,
            pixel_difference_threshold=20,
            changed_pixel_ratio=0.1,
            grid_rows=2,
            grid_columns=2,
            active_grid_ratio=0.5,
            window_ms=4000,
            confirmation_ms=3000,
            required_active_ratio=0.7,
            exit_stable_ms=2000,
            merge_gap_ms=1000,
            candidate_stable_ms=1000,
            contiguous_similarity=0.99,
            saved_similarity=0.98,
        )

        result = detect_frames(
            detect.stream_sampled_frames(source, sample_interval_ms=1000),
            config,
        )

        self.assertEqual(len(result["dynamic_segments"]), 1)
        self.assertLessEqual(result["keyframe_intervals"]["p95_ms"], 1100)

    def test_real_decode_suppresses_dynamic_and_recovers_stable_slide_without_disk_mp4(self):
        black = np.zeros((64, 64, 3), dtype=np.uint8)
        white = np.full((64, 64, 3), 255, dtype=np.uint8)
        images = [black, black, black]
        images.extend(white if index % 2 else black for index in range(8))
        images.extend([white, white, white, white])
        source = _encode_video_in_memory(images)
        config = SlicePipelineConfig(
            dynamic_detection_enabled=True,
            sample_interval_ms=400,
            pixel_difference_threshold=20,
            changed_pixel_ratio=0.1,
            grid_rows=2,
            grid_columns=2,
            active_grid_ratio=0.5,
            window_ms=2000,
            confirmation_ms=1000,
            required_active_ratio=0.7,
            exit_stable_ms=500,
            merge_gap_ms=1000,
            candidate_stable_ms=500,
            contiguous_similarity=0.99,
            saved_similarity=0.98,
        )

        result = detect_frames(stream_keyframes(source), config)

        self.assertEqual(len(result["dynamic_segments"]), 1)
        self.assertTrue(result["slices"])
        self.assertGreater(
            result["slices"][0]["snap_time"],
            result["dynamic_segments"][0]["end_ms"] // 1000,
        )
        self.assertGreater(result["slices"][-1]["snap_time"], result["dynamic_segments"][0]["end_ms"] // 1000)
        self.assertFalse(result["mp4_persisted"])


if __name__ == "__main__":
    unittest.main()
