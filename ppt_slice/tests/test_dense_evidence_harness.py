import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from harness.tools.dense_evidence import (
    dense_evidence_timestamps,
    generate_dense_candidate_evidence,
)


class DenseEvidenceHarnessTests(unittest.TestCase):
    def test_dense_timestamps_cover_context_and_respect_limit(self):
        values = dense_evidence_timestamps(
            10000,
            30000,
            step_ms=1000,
            context_ms=2000,
            duration_ms=40000,
            max_frames=8,
        )

        self.assertEqual(values[0], 8000)
        self.assertEqual(values[-1], 32000)
        self.assertLessEqual(len(values), 8)

    def test_dense_timestamps_keep_one_second_from_container_end(self):
        values = dense_evidence_timestamps(
            9000,
            10000,
            duration_ms=10500,
        )

        self.assertLessEqual(max(values), 9500)

    def test_generates_only_static_dense_pages(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            queue = {
                "candidates": [
                    {
                        "url": "http://example.test/course/PPT.mp4",
                        "course_name": "测试课程",
                        "candidate_kind": "DETECTION",
                        "start_ms": 1000,
                        "end_ms": 5000,
                        "duration_ms": 10000,
                    }
                ]
            }

            def extractor(url, timestamp_ms, destination):
                image = np.full((40, 80, 3), timestamp_ms % 255, dtype=np.uint8)
                cv2.imwrite(str(destination), image)

            result = generate_dense_candidate_evidence(
                queue,
                [1],
                root,
                extractor=extractor,
                step_ms=1000,
                context_ms=0,
                frames_per_page=4,
            )

            self.assertEqual(result["candidate_count"], 1)
            self.assertGreaterEqual(len(result["candidates"][0]["pages"]), 1)
            self.assertTrue(all(Path(path).suffix == ".jpg" for path in result["candidates"][0]["pages"]))
            self.assertFalse(any(root.rglob("*.mp4")))


if __name__ == "__main__":
    unittest.main()
