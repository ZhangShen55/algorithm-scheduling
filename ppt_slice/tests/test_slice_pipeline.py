import unittest
from dataclasses import replace

import numpy as np

from app.models.task import FrameData
from app.services.slice_pipeline import SlicePipeline, SlicePipelineConfig


class MemoryWriter:
    def __init__(self):
        self.images = []
        self.dynamic_segments = []

    def write_image(self, *, frame_seq, snap_time, frame):
        self.images.append(
            {"frame_seq": frame_seq, "snap_time": snap_time, "mean": int(frame.mean())}
        )

    def set_dynamic_segments(self, segments):
        self.dynamic_segments = list(segments)


def _config(enabled=True):
    return SlicePipelineConfig(
        dynamic_detection_enabled=enabled,
        sample_interval_ms=1,
        pixel_difference_threshold=20,
        changed_pixel_ratio=0.1,
        grid_rows=2,
        grid_columns=2,
        active_grid_ratio=0.5,
        window_ms=4000,
        confirmation_ms=2000,
        required_active_ratio=0.7,
        exit_stable_ms=1000,
        merge_gap_ms=1000,
        candidate_stable_ms=1000,
        contiguous_similarity=0.99,
        saved_similarity=0.98,
    )


class SlicePipelineTests(unittest.TestCase):
    def test_normal_page_change_publishes_one_slice_per_stable_page(self):
        writer = MemoryWriter()
        pipeline = SlicePipeline(writer, _config())
        gray = np.full((20, 20, 3), 64, dtype=np.uint8)
        white = np.full((20, 20, 3), 255, dtype=np.uint8)

        for timestamp_ms, frame in [
            (0, gray), (1000, gray), (2000, gray),
            (3000, white), (4000, white), (5000, white),
        ]:
            pipeline.observe(FrameData(frame=frame, timestamp_ms=timestamp_ms))
        pipeline.finish(5000)

        self.assertEqual([item["mean"] for item in writer.images], [64, 255])
        self.assertEqual(writer.dynamic_segments, [])

    def test_initial_black_screen_is_not_published_before_first_slide(self):
        writer = MemoryWriter()
        pipeline = SlicePipeline(writer, _config())
        black = np.zeros((20, 20, 3), dtype=np.uint8)
        white = np.full((20, 20, 3), 255, dtype=np.uint8)

        for timestamp_ms, frame in [
            (0, black), (1000, black), (2000, black),
            (3000, white), (4000, white), (5000, white),
        ]:
            pipeline.observe(FrameData(frame=frame, timestamp_ms=timestamp_ms))
        pipeline.finish(5000)

        self.assertEqual([item["mean"] for item in writer.images], [255])
        self.assertEqual(writer.dynamic_segments, [])

    def test_dark_slide_with_visible_content_is_published(self):
        writer = MemoryWriter()
        pipeline = SlicePipeline(writer, _config())
        dark_slide = np.zeros((20, 20, 3), dtype=np.uint8)
        dark_slide[:2, :2] = 255

        for timestamp_ms in (0, 1000, 2000):
            pipeline.observe(
                FrameData(frame=dark_slide, timestamp_ms=timestamp_ms)
            )
        pipeline.finish(2000)

        self.assertEqual(len(writer.images), 1)
        self.assertEqual(writer.dynamic_segments, [])

    def test_sustained_dynamic_frames_are_suppressed_and_reported(self):
        writer = MemoryWriter()
        pipeline = SlicePipeline(writer, _config())
        black = np.zeros((20, 20, 3), dtype=np.uint8)
        white = np.full((20, 20, 3), 255, dtype=np.uint8)
        frames = [(0, black), (1000, black), (2000, black)]
        frames.extend((timestamp, white if timestamp % 2000 else black) for timestamp in range(3000, 11000, 1000))
        frames.extend([(11000, white), (12000, white), (13000, white), (14000, white)])

        for timestamp_ms, frame in frames:
            pipeline.observe(FrameData(frame=frame, timestamp_ms=timestamp_ms))
        pipeline.finish(14000)

        segments = writer.dynamic_segments
        self.assertEqual(len(segments), 1)
        self.assertLessEqual(segments[0]["start_ms"], 3000)
        self.assertGreaterEqual(segments[0]["end_ms"], 11000)
        self.assertTrue(
            all(
                not (segments[0]["start_ms"] <= image["snap_time"] * 1000 < segments[0]["end_ms"])
                for image in writer.images
            )
        )
        self.assertEqual(writer.images[-1]["mean"], 255)

    def test_disabled_detection_keeps_legacy_stable_frame_flow(self):
        writer = MemoryWriter()
        pipeline = SlicePipeline(writer, _config(enabled=False))
        black = np.zeros((20, 20, 3), dtype=np.uint8)
        white = np.full((20, 20, 3), 255, dtype=np.uint8)

        for timestamp_ms, frame in [(0, black), (1000, black), (2000, white), (3000, white)]:
            pipeline.observe(FrameData(frame=frame, timestamp_ms=timestamp_ms))
        pipeline.finish(3000)

        self.assertEqual([item["mean"] for item in writer.images], [255])
        self.assertEqual(writer.dynamic_segments, [])

    def test_repeated_dynamic_cluster_suppresses_slices_from_long_static_gaps(self):
        writer = MemoryWriter()
        config = replace(
            _config(),
            cluster_gap_ms=10_000,
            cluster_min_segments=3,
        )
        pipeline = SlicePipeline(writer, config)
        colors = {
            "black": np.zeros((20, 20, 3), dtype=np.uint8),
            "white": np.full((20, 20, 3), 255, dtype=np.uint8),
            "red": np.full((20, 20, 3), (0, 0, 180), dtype=np.uint8),
            "blue": np.full((20, 20, 3), (180, 0, 0), dtype=np.uint8),
            "green": np.full((20, 20, 3), (0, 180, 0), dtype=np.uint8),
        }
        frames = [
            (0, colors["black"]), (1000, colors["black"]), (2000, colors["black"]),
            (3000, colors["white"]), (4000, colors["black"]), (5000, colors["white"]),
            (6000, colors["red"]), (7000, colors["red"]), (8000, colors["red"]),
            (11000, colors["white"]), (12000, colors["black"]), (13000, colors["white"]),
            (14000, colors["blue"]), (15000, colors["blue"]), (16000, colors["blue"]),
            (19000, colors["white"]), (20000, colors["black"]), (21000, colors["white"]),
            (22000, colors["green"]), (23000, colors["green"]), (24000, colors["green"]),
            (25000, colors["green"]),
        ]

        for timestamp_ms, frame in frames:
            pipeline.observe(FrameData(frame=frame, timestamp_ms=timestamp_ms))
        pipeline.finish(25_000)

        self.assertEqual(len(writer.dynamic_segments), 1)
        segment = writer.dynamic_segments[0]
        self.assertLessEqual(segment["start_ms"], 3000)
        self.assertGreaterEqual(segment["end_ms"], 22000)
        self.assertEqual([item["mean"] for item in writer.images], [60])

if __name__ == "__main__":
    unittest.main()
