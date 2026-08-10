import unittest

import numpy as np

from app.services.dynamic_detection import (
    ActivityAnalyzer,
    DynamicSegment,
    DynamicSegmentDetector,
    DynamicState,
    cluster_dynamic_segments,
)


class ActivityAnalyzerTests(unittest.TestCase):
    def test_small_pointer_change_does_not_activate_enough_grid_cells(self):
        analyzer = ActivityAnalyzer(
            pixel_difference_threshold=20,
            changed_pixel_ratio_threshold=0.01,
            grid_rows=4,
            grid_columns=4,
            active_grid_ratio_threshold=0.25,
        )
        previous = np.zeros((40, 40, 3), dtype=np.uint8)
        current = previous.copy()
        current[0:3, 0:3] = 255

        observation = analyzer.analyze(1000, previous, current)

        self.assertFalse(observation.is_active)
        self.assertLess(observation.active_grid_ratio, 0.25)

    def test_sustained_full_frame_change_is_active(self):
        analyzer = ActivityAnalyzer(
            pixel_difference_threshold=20,
            changed_pixel_ratio_threshold=0.1,
            grid_rows=4,
            grid_columns=4,
            active_grid_ratio_threshold=0.5,
        )
        previous = np.zeros((40, 40, 3), dtype=np.uint8)
        current = np.full((40, 40, 3), 255, dtype=np.uint8)

        observation = analyzer.analyze(1000, previous, current)

        self.assertTrue(observation.is_active)
        self.assertEqual(observation.active_grid_ratio, 1.0)
        self.assertEqual(observation.changed_pixel_ratio, 1.0)

    def test_optical_flow_detects_low_contrast_motion_without_strong_pixel_activity(self):
        rng = np.random.default_rng(7)
        previous = rng.integers(90, 130, size=(80, 120, 3), dtype=np.uint8)
        current = np.roll(previous, shift=1, axis=1)
        analyzer = ActivityAnalyzer(
            pixel_difference_threshold=60,
            changed_pixel_ratio_threshold=0.2,
            grid_rows=4,
            grid_columns=4,
            active_grid_ratio_threshold=0.75,
            optical_flow_enabled=True,
            optical_flow_width=120,
            optical_flow_magnitude_threshold=0.3,
            optical_flow_active_ratio_threshold=0.2,
        )

        observation = analyzer.analyze(1000, previous, current)

        self.assertFalse(observation.is_active)
        self.assertTrue(observation.is_motion_active)
        self.assertGreaterEqual(observation.motion_ratio, 0.2)

    def test_optical_flow_ignores_local_pointer_motion(self):
        previous = np.zeros((80, 120, 3), dtype=np.uint8)
        current = previous.copy()
        previous[10:14, 10:14] = 255
        current[10:14, 16:20] = 255
        analyzer = ActivityAnalyzer(
            pixel_difference_threshold=20,
            changed_pixel_ratio_threshold=0.01,
            grid_rows=4,
            grid_columns=4,
            active_grid_ratio_threshold=0.25,
            optical_flow_enabled=True,
            optical_flow_width=120,
            optical_flow_magnitude_threshold=0.3,
            optical_flow_active_ratio_threshold=0.1,
        )

        observation = analyzer.analyze(1000, previous, current)

        self.assertFalse(observation.is_motion_active)


class DynamicSegmentDetectorTests(unittest.TestCase):
    def _detector(self, **overrides):
        defaults = {
            "confirmation_ms": 5000,
            "window_ms": 8000,
            "required_active_ratio": 0.7,
            "exit_stable_ms": 3000,
            "merge_gap_ms": 3000,
            "candidate_stable_ms": 2000,
        }
        defaults.update(overrides)
        return DynamicSegmentDetector(**defaults)

    def test_short_transition_returns_to_stable_without_segment(self):
        detector = self._detector()

        detector.observe(1000, True, 0.8)
        detector.observe(2000, True, 0.8)
        detector.observe(3000, False, 0.0)
        detector.observe(6000, False, 0.0)
        detector.finish(6000)

        self.assertEqual(detector.state, DynamicState.STABLE)
        self.assertEqual(detector.segments, ())

    def test_candidate_uses_its_own_stability_timeout(self):
        detector = self._detector(candidate_stable_ms=1000, exit_stable_ms=5000)
        detector.observe(0, True, 0.8)
        detector.observe(1000, False, 0.0)

        self.assertEqual(detector.state, DynamicState.STABLE)
        self.assertEqual(detector.segments, ())

    def test_sustained_activity_backtracks_start_and_ends_at_stability_start(self):
        detector = self._detector()

        for timestamp in range(1000, 7000, 1000):
            detector.observe(timestamp, True, 0.9)
        detector.observe(7000, False, 0.0)
        detector.observe(10000, False, 0.0)

        self.assertEqual(len(detector.segments), 1)
        segment = detector.segments[0]
        self.assertEqual(segment.start_ms, 1000)
        self.assertEqual(segment.end_ms, 7000)
        self.assertEqual(segment.type, "SUSPECTED_VIDEO_PLAYBACK")
        self.assertGreaterEqual(segment.confidence, 0.7)
        self.assertEqual(detector.state, DynamicState.STABLE)

    def test_short_pause_does_not_split_dynamic_segment(self):
        detector = self._detector()
        for timestamp in range(0, 6000, 1000):
            detector.observe(timestamp, True, 0.9)
        detector.observe(6000, False, 0.0)
        detector.observe(7000, False, 0.0)
        detector.observe(8000, True, 0.8)
        detector.observe(9000, False, 0.0)
        detector.observe(12000, False, 0.0)

        self.assertEqual([(item.start_ms, item.end_ms) for item in detector.segments], [(0, 9000)])

    def test_file_end_closes_active_segment(self):
        detector = self._detector()
        for timestamp in range(0, 6000, 1000):
            detector.observe(timestamp, True, 0.85)

        detector.finish(6500)

        self.assertEqual([(item.start_ms, item.end_ms) for item in detector.segments], [(0, 6500)])

    def test_nearby_segments_are_merged_and_confidence_is_bounded(self):
        detector = self._detector(confirmation_ms=2000, exit_stable_ms=1000, merge_gap_ms=3000)
        for timestamp in (0, 1000, 2000):
            detector.observe(timestamp, True, 2.0)
        detector.observe(3000, False, 0.0)
        detector.observe(4000, False, 0.0)
        for timestamp in (5000, 6000, 7000):
            detector.observe(timestamp, True, 2.0)
        detector.observe(8000, False, 0.0)
        detector.observe(9000, False, 0.0)

        self.assertEqual(len(detector.segments), 1)
        self.assertEqual((detector.segments[0].start_ms, detector.segments[0].end_ms), (0, 8000))
        self.assertGreaterEqual(detector.segments[0].confidence, 0.0)
        self.assertLessEqual(detector.segments[0].confidence, 1.0)

    def test_weak_motion_cannot_create_dynamic_segment(self):
        detector = self._detector(motion_grace_ms=5000)
        for timestamp in range(0, 12000, 1000):
            detector.observe(
                timestamp,
                False,
                0.0,
                is_motion_active=True,
                motion_score=0.4,
            )

        detector.finish(12000)

        self.assertEqual(detector.segments, ())

    def test_motion_can_confirm_candidate_only_after_strong_activity_starts_it(self):
        detector = self._detector(
            confirmation_ms=2000,
            candidate_stable_ms=5000,
            motion_grace_ms=5000,
        )
        for timestamp in (-3000, -2000, -1000):
            detector.observe(
                timestamp,
                False,
                0.0,
                is_motion_active=True,
                motion_score=0.4,
            )
        detector.observe(0, True, 0.8)
        detector.observe(
            1000,
            False,
            0.0,
            is_motion_active=True,
            motion_score=0.4,
        )
        detector.observe(
            2000,
            False,
            0.0,
            is_motion_active=True,
            motion_score=0.4,
        )

        self.assertEqual(detector.state, DynamicState.DYNAMIC)
        detector.finish(3000)
        self.assertEqual(
            [(item.start_ms, item.end_ms) for item in detector.segments],
            [(0, 3000)],
        )

    def test_weak_motion_keeps_confirmed_dynamic_segment_alive(self):
        detector = self._detector(
            confirmation_ms=2000,
            exit_stable_ms=1000,
            motion_grace_ms=5000,
        )
        for timestamp in (0, 1000, 2000):
            detector.observe(timestamp, True, 0.8)
        for timestamp in (3000, 6000, 9000, 12000):
            detector.observe(
                timestamp,
                False,
                0.0,
                is_motion_active=True,
                motion_score=0.4,
            )
        detector.observe(13000, False, 0.0)
        detector.observe(18000, False, 0.0)

        self.assertEqual(
            [(item.start_ms, item.end_ms) for item in detector.segments],
            [(0, 13000)],
        )

    def test_strong_activity_without_motion_uses_normal_stable_exit(self):
        detector = self._detector(
            confirmation_ms=2000,
            exit_stable_ms=1000,
            motion_grace_ms=5000,
        )
        for timestamp in (0, 1000, 2000):
            detector.observe(timestamp, True, 0.8)
        detector.observe(3000, False, 0.0)
        detector.observe(4000, False, 0.0)

        self.assertEqual(detector.state, DynamicState.STABLE)
        self.assertEqual(
            [(item.start_ms, item.end_ms) for item in detector.segments],
            [(0, 3000)],
        )


class DynamicSegmentClusterTests(unittest.TestCase):
    @staticmethod
    def _segment(start_ms, end_ms, confidence=0.8):
        return DynamicSegment(
            type="SUSPECTED_VIDEO_PLAYBACK",
            start_ms=start_ms,
            end_ms=end_ms,
            confidence=confidence,
        )

    def test_three_repeated_dynamic_segments_bridge_long_static_shots(self):
        segments = [
            self._segment(2_064_412, 2_076_614),
            self._segment(2_096_935, 2_116_254),
            self._segment(2_179_019, 2_189_190),
            self._segment(2_258_060, 2_279_348),
        ]

        clustered = cluster_dynamic_segments(
            segments,
            cluster_gap_ms=90_000,
            cluster_min_segments=3,
        )

        self.assertEqual(len(clustered), 1)
        self.assertEqual((clustered[0].start_ms, clustered[0].end_ms), (2_064_412, 2_279_348))
        self.assertEqual(clustered[0].reason, "repeated_dynamic_cluster")

    def test_two_dynamic_segments_do_not_consume_stable_ppt_between_them(self):
        segments = [
            self._segment(10_000, 20_000),
            self._segment(80_000, 90_000),
        ]

        clustered = cluster_dynamic_segments(
            segments,
            cluster_gap_ms=90_000,
            cluster_min_segments=3,
        )

        self.assertEqual(
            [(item.start_ms, item.end_ms) for item in clustered],
            [(10_000, 20_000), (80_000, 90_000)],
        )

    def test_motion_joined_strong_bursts_still_count_as_cluster_evidence(self):
        detector = DynamicSegmentDetector(
            confirmation_ms=2000,
            window_ms=8000,
            required_active_ratio=0.7,
            exit_stable_ms=1000,
            merge_gap_ms=1000,
            candidate_stable_ms=1000,
            motion_grace_ms=5000,
        )
        for timestamp in (0, 1000, 2000):
            detector.observe(timestamp, True, 0.8)
        for timestamp in (3000, 6000):
            detector.observe(
                timestamp,
                False,
                0.0,
                is_motion_active=True,
                motion_score=0.4,
            )
        detector.observe(9000, True, 0.8)
        detector.observe(10_000, False, 0.0)
        detector.observe(15_000, False, 0.0)

        for timestamp in (18_000, 19_000, 20_000):
            detector.observe(timestamp, True, 0.8)
        detector.observe(21_000, False, 0.0)
        detector.observe(22_000, False, 0.0)

        clustered = cluster_dynamic_segments(
            detector.segments,
            cluster_gap_ms=9000,
            cluster_min_segments=3,
        )

        self.assertEqual(len(clustered), 1)
        self.assertEqual((clustered[0].start_ms, clustered[0].end_ms), (0, 21_000))
        self.assertEqual(clustered[0].reason, "repeated_dynamic_cluster")


if __name__ == "__main__":
    unittest.main()
