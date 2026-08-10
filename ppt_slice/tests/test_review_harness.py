import unittest

from harness.tools.review import build_audit_windows, calculate_metrics


class AuditWindowTests(unittest.TestCase):
    def test_builds_fixed_long_gap_dense_and_near_threshold_windows_outside_detections(self):
        detection = {
            "dynamic_segments": [{"start_ms": 100000, "end_ms": 160000}],
            "slices": [
                {"snap_time": 0},
                {"snap_time": 10}, {"snap_time": 11}, {"snap_time": 12},
                {"snap_time": 13}, {"snap_time": 14},
                {"snap_time": 300},
            ],
            "near_threshold_windows": [{"start_ms": 400000, "end_ms": 410000}],
        }

        windows = build_audit_windows(
            detection,
            duration_ms=600000,
            fixed_grid_ms=200000,
            long_gap_ms=120000,
            dense_window_ms=60000,
            dense_slice_count=5,
        )

        sources = {item["source"] for item in windows}
        self.assertTrue({"FIXED_GRID", "LONG_SLICE_GAP", "DENSE_SLICES", "NEAR_THRESHOLD"}.issubset(sources))
        self.assertTrue(
            all(item["end_ms"] <= 100000 or item["start_ms"] >= 160000 for item in windows)
        )


class MetricTests(unittest.TestCase):
    def test_metrics_include_false_positive_missed_detection_and_uncertain(self):
        predicted = [
            {"start_ms": 1000, "end_ms": 5000},
            {"start_ms": 10000, "end_ms": 14000},
        ]
        reviews = [
            {"candidate_kind": "DETECTION", "start_ms": 1000, "end_ms": 5000, "label": "CONFIRMED_VIDEO"},
            {"candidate_kind": "DETECTION", "start_ms": 10000, "end_ms": 14000, "label": "FALSE_POSITIVE"},
            {"candidate_kind": "AUDIT", "start_ms": 20000, "end_ms": 24000, "label": "CONFIRMED_SCROLL"},
            {"candidate_kind": "AUDIT", "start_ms": 30000, "end_ms": 34000, "label": "UNCERTAIN"},
        ]

        metrics = calculate_metrics(predicted, reviews)

        self.assertEqual(metrics["confirmed_predictions"], 1)
        self.assertEqual(metrics["false_positive_count"], 1)
        self.assertEqual(metrics["missed_detection_count"], 1)
        self.assertEqual(metrics["uncertain_count"], 1)
        self.assertEqual(metrics["segment_precision"], 0.5)
        self.assertEqual(metrics["segment_recall"], 0.5)
        self.assertFalse(metrics["has_no_known_errors"])


if __name__ == "__main__":
    unittest.main()
