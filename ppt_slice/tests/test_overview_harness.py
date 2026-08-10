import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from harness.tools.overview import build_overview_pages


class OverviewHarnessTests(unittest.TestCase):
    def test_builds_static_overview_pages_and_index(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            evidence = root / "contact-sheet.jpg"
            cv2.imwrite(str(evidence), np.full((120, 320, 3), 180, dtype=np.uint8))
            queue = {
                "run_id": "run-v1",
                "candidates": [
                    {
                        "course_name": "测试课程",
                        "candidate_kind": "DETECTION",
                        "source": "ALGORITHM",
                        "start_ms": 1000,
                        "end_ms": 8000,
                        "evidence_path": str(evidence),
                    }
                ],
            }

            result = build_overview_pages(queue, root / "overview", columns=1, rows=1)

            self.assertEqual(result["item_count"], 1)
            self.assertEqual(result["page_count"], 1)
            self.assertTrue(Path(result["pages"][0]["path"]).is_file())
            index = json.loads((root / "overview" / "overview-index.json").read_text())
            self.assertEqual(index["items"][0]["candidate_index"], 0)
            self.assertFalse(any(root.rglob("*.mp4")))

    def test_rejects_non_static_page_extension(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaises(ValueError):
                build_overview_pages(
                    {"candidates": []},
                    Path(temporary_directory),
                page_extension=".mp4",
            )

    def test_pending_only_excludes_completed_reviews(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            evidence = root / "evidence.jpg"
            cv2.imwrite(str(evidence), np.zeros((20, 20, 3), dtype=np.uint8))
            queue = {
                "candidates": [
                    {
                        "candidate_kind": "AUDIT",
                        "source": "FIXED_GRID",
                        "start_ms": 0,
                        "end_ms": 1000,
                        "evidence_path": str(evidence),
                        "review_status": "COMPLETED",
                    },
                    {
                        "candidate_kind": "AUDIT",
                        "source": "FIXED_GRID",
                        "start_ms": 2000,
                        "end_ms": 3000,
                        "evidence_path": str(evidence),
                        "review_status": "PENDING",
                    },
                ]
            }

            result = build_overview_pages(queue, root / "overview", pending_only=True)

            self.assertEqual(result["item_count"], 1)
            self.assertEqual(result["items"][0]["candidate_number"], 2)


if __name__ == "__main__":
    unittest.main()
