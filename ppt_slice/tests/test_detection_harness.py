import json
import inspect
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from app.models.task import FrameData
from app.services.slice_pipeline import SlicePipelineConfig
from harness.tools import detect
from harness.tools.detect import detect_frames, scan_inventory


def _config():
    return SlicePipelineConfig(
        dynamic_detection_enabled=True,
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


def _slow_scanner(url, config):
    time.sleep(5)
    return {"url": url, "status": "COMPLETED"}


class DetectionHarnessTests(unittest.TestCase):
    def test_process_is_terminated_when_video_scan_exceeds_hard_timeout(self):
        self.assertTrue(
            hasattr(detect, "scan_with_hard_timeout"),
            "全量 runner 需要可终止阻塞解码的进程级超时",
        )
        started = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, "硬超时"):
            detect.scan_with_hard_timeout(
                _slow_scanner,
                "http://example.test/PPT.mp4",
                _config(),
                timeout_seconds=0.2,
            )
        self.assertLess(time.monotonic() - started, 3.0)

    def test_detection_run_id_is_distinct_from_inventory_run_id(self):
        self.assertIn(
            "run_id",
            inspect.signature(scan_inventory).parameters,
            "检测轮次必须与语料冻结轮次使用独立标识",
        )
        inventory = {
            "run_id": "inventory-run",
            "inventory_fingerprint": "fingerprint",
            "items": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            inventory_path = Path(temp_dir) / "inventory.json"
            output_path = Path(temp_dir) / "detections.json"
            inventory_path.write_text(json.dumps(inventory), encoding="utf-8")

            result = scan_inventory(
                inventory_path,
                output_path,
                _config(),
                run_id="detection-run",
            )

        self.assertEqual(result["run_id"], "detection-run")
        self.assertEqual(result["inventory_run_id"], "inventory-run")
        self.assertIn("algorithm_source_fingerprint", result)
        if "algorithm_source_fingerprint" in result:
            self.assertRegex(result["algorithm_source_fingerprint"], r"^[0-9a-f]{64}$")

    def test_detect_frames_returns_segments_and_slice_metadata_only(self):
        black = np.zeros((20, 20, 3), dtype=np.uint8)
        white = np.full((20, 20, 3), 255, dtype=np.uint8)
        frames = [FrameData(black, timestamp_ms=0), FrameData(black, timestamp_ms=1000)]
        frames.extend(
            FrameData(white if timestamp % 2000 else black, timestamp_ms=timestamp)
            for timestamp in range(2000, 7000, 1000)
        )
        frames.extend([FrameData(white, timestamp_ms=7000), FrameData(white, timestamp_ms=8000)])

        result = detect_frames(frames, _config())

        self.assertEqual(len(result["dynamic_segments"]), 1)
        self.assertFalse(result["mp4_persisted"])
        self.assertTrue(all("frame" not in item for item in result["slices"]))

    def test_detect_frames_records_near_threshold_activity_for_missed_detection_audit(self):
        black = np.zeros((20, 20, 3), dtype=np.uint8)
        partial = black.copy()
        partial[:10, :10] = 255

        result = detect_frames(
            [FrameData(black, timestamp_ms=0), FrameData(partial, timestamp_ms=1000)],
            _config(),
        )

        self.assertEqual(result["dynamic_segments"], [])
        self.assertEqual(result["near_threshold_windows"], [{"start_ms": 1000, "end_ms": 2000}])

    def test_inventory_scan_resumes_completed_items(self):
        inventory = {
            "run_id": "run-test",
            "inventory_fingerprint": "inventory-fingerprint",
            "items": [
                {"url": "http://example.test/a/PPT.mp4", "course_name": "a", "split": "CALIBRATION", "probe_status": "COMPLETED", "resource_fingerprint": "a1"},
                {"url": "http://example.test/b/PPT.mp4", "course_name": "b", "split": "CALIBRATION", "probe_status": "COMPLETED", "resource_fingerprint": "b1"},
            ],
        }
        existing = {
            "schema_version": 1,
            "run_id": "run-test",
            "inventory_fingerprint": "inventory-fingerprint",
            "items": [{"url": inventory["items"][0]["url"], "resource_fingerprint": "a1", "status": "COMPLETED"}],
        }
        calls = []

        def scanner(url, config):
            calls.append(url)
            return {"url": url, "status": "COMPLETED", "dynamic_segments": [], "slices": [], "mp4_persisted": False}

        with tempfile.TemporaryDirectory() as temp_dir:
            inventory_path = Path(temp_dir) / "inventory.json"
            output_path = Path(temp_dir) / "segments.json"
            inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
            output_path.write_text(json.dumps(existing), encoding="utf-8")

            result = scan_inventory(inventory_path, output_path, _config(), scanner=scanner)

            self.assertEqual(calls, [inventory["items"][1]["url"]])
            self.assertEqual(len(result["items"]), 2)
            self.assertEqual(list(Path(temp_dir).glob("*.mp4")), [])

    def test_inventory_scan_retries_a_temporary_failure_within_limit(self):
        inventory = {
            "run_id": "run-retry",
            "inventory_fingerprint": "fingerprint",
            "items": [
                {"url": "http://example.test/a/PPT.mp4", "course_name": "a", "split": "CALIBRATION", "probe_status": "COMPLETED", "resource_fingerprint": "a1"}
            ],
        }
        attempts = 0

        def scanner(url, config):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("temporary")
            return {"url": url, "status": "COMPLETED", "dynamic_segments": [], "slices": [], "mp4_persisted": False}

        with tempfile.TemporaryDirectory() as temp_dir:
            inventory_path = Path(temp_dir) / "inventory.json"
            output_path = Path(temp_dir) / "segments.json"
            inventory_path.write_text(json.dumps(inventory), encoding="utf-8")

            result = scan_inventory(
                inventory_path,
                output_path,
                _config(),
                scanner=scanner,
                max_retries=1,
            )

            self.assertEqual(attempts, 2)
            self.assertEqual(result["items"][0]["status"], "COMPLETED")


if __name__ == "__main__":
    unittest.main()
