import unittest

from pydantic import ValidationError

from app.core.config import Settings


class DynamicConfigurationTests(unittest.TestCase):
    def test_accepts_valid_dynamic_detection_configuration(self):
        settings = Settings(
            DYNAMIC_DETECTION_ENABLED=True,
            DYNAMIC_SAMPLE_INTERVAL_MS=1000,
            DYNAMIC_PIXEL_DIFFERENCE_THRESHOLD=30,
            DYNAMIC_CHANGED_PIXEL_RATIO=0.08,
            DYNAMIC_ACTIVE_GRID_RATIO=0.35,
            DYNAMIC_WINDOW_MS=8000,
            DYNAMIC_CONFIRMATION_MS=5000,
            DYNAMIC_REQUIRED_ACTIVE_RATIO=0.7,
            DYNAMIC_EXIT_STABLE_MS=3000,
            DYNAMIC_MERGE_GAP_MS=3000,
            DYNAMIC_CLUSTER_GAP_MS=90000,
            DYNAMIC_CLUSTER_MIN_SEGMENTS=3,
            DYNAMIC_OPTICAL_FLOW_ENABLED=True,
            DYNAMIC_OPTICAL_FLOW_WIDTH=320,
            DYNAMIC_OPTICAL_FLOW_MAGNITUDE_THRESHOLD=0.5,
            DYNAMIC_OPTICAL_FLOW_ACTIVE_RATIO=0.05,
            DYNAMIC_MOTION_GRACE_MS=15000,
            DYNAMIC_CANDIDATE_STABLE_MS=2000,
        )

        self.assertTrue(settings.DYNAMIC_DETECTION_ENABLED)
        self.assertEqual(settings.DYNAMIC_SAMPLE_INTERVAL_MS, 1000)

    def test_rejects_non_positive_time_threshold(self):
        with self.assertRaises(ValidationError):
            Settings(DYNAMIC_CONFIRMATION_MS=0)

    def test_rejects_ratio_outside_zero_to_one(self):
        with self.assertRaises(ValidationError):
            Settings(DYNAMIC_REQUIRED_ACTIVE_RATIO=1.1)

    def test_rejects_cluster_with_fewer_than_three_segments(self):
        with self.assertRaises(ValidationError):
            Settings(DYNAMIC_CLUSTER_MIN_SEGMENTS=2)

    def test_rejects_optical_flow_ratio_outside_zero_to_one(self):
        with self.assertRaises(ValidationError):
            Settings(DYNAMIC_OPTICAL_FLOW_ACTIVE_RATIO=1.1)


if __name__ == "__main__":
    unittest.main()
