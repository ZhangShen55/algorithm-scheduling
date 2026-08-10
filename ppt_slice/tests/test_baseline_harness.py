import unittest

from harness.tools import baseline
from harness.tools.baseline import BaselineAccumulator, summarize_intervals


class BaselineAccumulatorTests(unittest.TestCase):
    def test_reproduces_stable_page_slice_decision_without_writing_images(self):
        comparator = lambda left, right: 1.0 if left == right else 0.0
        accumulator = BaselineAccumulator(
            contiguous_threshold=0.99,
            saved_threshold=0.98,
            comparator=comparator,
        )

        for timestamp_ms, frame in [(0, "A"), (1000, "A"), (2000, "B"), (3000, "B")]:
            accumulator.observe(timestamp_ms, frame)

        self.assertEqual(accumulator.slice_timestamps_ms, [1000, 3000])
        self.assertEqual(accumulator.observation_count, 4)

    def test_dynamic_changes_do_not_become_slices_until_a_frame_repeats(self):
        comparator = lambda left, right: 1.0 if left == right else 0.0
        accumulator = BaselineAccumulator(comparator=comparator)

        for index, frame in enumerate(["A", "B", "C", "D"]):
            accumulator.observe(index * 1000, frame)

        self.assertEqual(accumulator.slice_timestamps_ms, [])


class IntervalSummaryTests(unittest.TestCase):
    def test_reports_keyframe_interval_distribution(self):
        summary = summarize_intervals([0, 1000, 3000, 6000])

        self.assertEqual(summary["count"], 3)
        self.assertEqual(summary["min_ms"], 1000)
        self.assertEqual(summary["max_ms"], 3000)
        self.assertEqual(summary["p50_ms"], 2000)

    def test_slice_density_reports_per_minute_and_dense_windows(self):
        self.assertTrue(
            hasattr(baseline, "summarize_slice_density"),
            "全量旧算法基线需要切片密度统计",
        )
        summary = baseline.summarize_slice_density(
            [1000, 2000, 3000, 61000],
            duration_ms=120000,
            dense_slice_count=3,
        )

        self.assertEqual(summary["average_slices_per_minute"], 2.0)
        self.assertEqual(
            summary["minute_slice_counts"],
            [
                {"start_ms": 0, "end_ms": 60000, "count": 3},
                {"start_ms": 60000, "end_ms": 120000, "count": 1},
            ],
        )
        self.assertEqual(
            summary["dense_slice_windows"],
            [summary["minute_slice_counts"][0]],
        )


if __name__ == "__main__":
    unittest.main()
