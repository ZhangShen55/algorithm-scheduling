import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from harness.tools.evidence import (
    evidence_timestamps,
    generate_review_queue_evidence,
    generate_segment_evidence,
)


class EvidenceHarnessTests(unittest.TestCase):
    def test_evidence_timestamps_cover_context_start_middle_and_end(self):
        timestamps = evidence_timestamps(10000, 20000, context_ms=2000)

        self.assertEqual(timestamps, [8000, 10000, 15000, 19999, 22000])

    def test_evidence_timestamps_are_clamped_to_video_duration(self):
        timestamps = evidence_timestamps(
            3295000,
            3300107,
            context_ms=5000,
            duration_ms=3300107,
        )

        self.assertLessEqual(max(timestamps), 3299107)

    def test_generates_static_contact_sheet_without_video_artifacts(self):
        calls = []

        def extractor(url, timestamp_ms, destination):
            calls.append((url, timestamp_ms))
            image = np.full((40, 60, 3), timestamp_ms % 255, dtype=np.uint8)
            self.assertTrue(cv2.imwrite(str(destination), image))

        with tempfile.TemporaryDirectory() as temp_dir:
            result = generate_segment_evidence(
                "http://example.test/course/PPT.mp4",
                [{"start_ms": 10000, "end_ms": 20000}],
                Path(temp_dir),
                extractor=extractor,
            )

            self.assertEqual(len(calls), 5)
            self.assertTrue(Path(result[0]["contact_sheet_path"]).is_file())
            self.assertEqual(list(Path(temp_dir).rglob("*.mp4")), [])
            self.assertEqual(list(Path(temp_dir).rglob("*.gif")), [])

    def test_review_queue_evidence_updates_each_candidate_with_contact_sheet(self):
        def extractor(url, timestamp_ms, destination):
            image = np.full((20, 30, 3), timestamp_ms % 255, dtype=np.uint8)
            self.assertTrue(cv2.imwrite(str(destination), image))

        queue = {
            "candidates": [
                {"url": "http://example.test/a/PPT.mp4", "candidate_kind": "DETECTION", "start_ms": 1000, "end_ms": 3000, "evidence_path": ""},
                {"url": "http://example.test/b/PPT.mp4", "candidate_kind": "AUDIT", "start_ms": 5000, "end_ms": 7000, "evidence_path": ""},
            ]
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            updated = generate_review_queue_evidence(
                queue,
                Path(temp_dir),
                extractor=extractor,
                max_workers=2,
            )

            self.assertTrue(all(Path(item["evidence_path"]).is_file() for item in updated["candidates"]))
            self.assertEqual(list(Path(temp_dir).rglob("*.mp4")), [])

    def test_review_queue_evidence_resumes_existing_static_artifacts(self):
        queue = {
            "candidates": [
                {
                    "url": "http://example.test/a/PPT.mp4",
                    "candidate_kind": "AUDIT",
                    "start_ms": 5000,
                    "end_ms": 7000,
                    "duration_ms": 10000,
                    "evidence_path": "",
                }
            ]
        }

        def extractor(url, timestamp_ms, destination):
            image = np.full((20, 30, 3), timestamp_ms % 255, dtype=np.uint8)
            self.assertTrue(cv2.imwrite(str(destination), image))

        with tempfile.TemporaryDirectory() as temp_dir:
            generate_review_queue_evidence(queue, Path(temp_dir), extractor=extractor)

            def unexpected_extractor(url, timestamp_ms, destination):
                raise AssertionError("已有完整静态证据时不应重新提取")

            resumed = generate_review_queue_evidence(
                queue,
                Path(temp_dir),
                extractor=unexpected_extractor,
            )

            self.assertEqual(resumed["candidates"][0]["evidence_status"], "COMPLETED")
            self.assertTrue(Path(resumed["candidates"][0]["evidence_path"]).is_file())

    def test_review_queue_evidence_checkpoints_each_completed_candidate(self):
        def extractor(url, timestamp_ms, destination):
            image = np.full((20, 30, 3), timestamp_ms % 255, dtype=np.uint8)
            self.assertTrue(cv2.imwrite(str(destination), image))

        queue = {
            "candidates": [
                {
                    "url": f"http://example.test/{index}/PPT.mp4",
                    "candidate_kind": "AUDIT",
                    "start_ms": 1000,
                    "end_ms": 3000,
                    "evidence_path": "",
                }
                for index in range(2)
            ]
        }
        checkpoints = []
        with tempfile.TemporaryDirectory() as temp_dir:
            generate_review_queue_evidence(
                queue,
                Path(temp_dir),
                extractor=extractor,
                max_workers=2,
                checkpoint=lambda payload: checkpoints.append(payload),
            )

        self.assertEqual(len(checkpoints), 2)
        self.assertEqual(
            sum(item.get("evidence_status") == "COMPLETED" for item in checkpoints[-1]["candidates"]),
            2,
        )


if __name__ == "__main__":
    unittest.main()
