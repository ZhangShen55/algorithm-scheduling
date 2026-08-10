import csv
import json
import tempfile
import unittest
from pathlib import Path

from harness.tools.report import build_report, write_report


class ReportHarnessTests(unittest.TestCase):
    def test_writes_json_csv_and_chinese_markdown_with_blockers(self):
        inventory = {
            "run_id": "run-test",
            "inventory_fingerprint": "fingerprint",
            "discovery_errors": [],
            "items": [
                {
                    "url": "http://example.test/a/PPT.mp4",
                    "course_name": "课程A",
                    "duration": 60.0,
                    "probe_status": "COMPLETED",
                },
                {
                    "url": "http://example.test/b/PPT.mp4",
                    "course_name": "课程B",
                    "duration": None,
                    "probe_status": "FAILED",
                    "error_reason": "不可访问",
                },
            ],
        }
        detections = {
            "run_id": "detection-run",
            "inventory_run_id": "run-test",
            "algorithm_version": "dynamic-v1",
            "algorithm_source_fingerprint": "a" * 64,
            "effective_config": {"merge_gap_ms": 20000},
            "items": [
                {
                    "url": inventory["items"][0]["url"],
                    "course_name": "课程A",
                    "status": "COMPLETED",
                    "dynamic_segments": [
                        {"type": "SUSPECTED_VIDEO_PLAYBACK", "start_ms": 1000, "end_ms": 5000, "confidence": 0.8, "reason": "sustained_visual_change"}
                    ],
                    "slices": [],
                }
            ],
        }

        report = build_report(inventory, detections, reviews=[], audit_windows=[])

        self.assertEqual(report["completion"]["status"], "BLOCKED")
        self.assertIn("不可访问", " ".join(report["completion"]["reasons"]))
        self.assertEqual(report["metrics"]["discovery_coverage"], 1.0)
        self.assertEqual(report["metrics"]["processing_completion"], 0.5)
        self.assertEqual(report["metrics"]["candidate_review_coverage"], 0.0)
        self.assertIn("false_positives_per_video_hour", report["metrics"])
        self.assertIn("processing_speed_x", report["metrics"])
        self.assertIn("peak_memory_bytes", report["metrics"])
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_report(Path(temp_dir), report)
            markdown = paths["markdown"].read_text(encoding="utf-8")
            with paths["csv"].open(encoding="utf-8-sig") as handle:
                rows = list(csv.DictReader(handle))
            payload = json.loads(paths["json"].read_text(encoding="utf-8"))

            self.assertIn("# 疑似动态区间复核报告", markdown)
            self.assertIn("课程A", markdown)
            self.assertEqual(len(rows), 1)
            self.assertEqual(payload["run_id"], "detection-run")
            self.assertEqual(payload["inventory_run_id"], "run-test")
            self.assertEqual(payload["algorithm_source_fingerprint"], "a" * 64)


if __name__ == "__main__":
    unittest.main()
