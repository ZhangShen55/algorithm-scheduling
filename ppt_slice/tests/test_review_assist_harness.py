import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from harness.tools.review_assist import apply_static_stability_assist


class ReviewAssistHarnessTests(unittest.TestCase):
    def test_completes_static_audit_without_changing_detection_review(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            static_paths = []
            for index in range(5):
                path = root / f"static-{index}.jpg"
                cv2.imwrite(str(path), np.full((80, 120, 3), 100, dtype=np.uint8))
                static_paths.append(str(path))
            queue = {
                "candidates": [
                    {
                        "candidate_kind": "AUDIT",
                        "review_status": "PENDING",
                        "label": "",
                        "evidence_frames": static_paths,
                    },
                    {
                        "candidate_kind": "DETECTION",
                        "review_status": "PENDING",
                        "label": "",
                        "evidence_frames": static_paths,
                    },
                ]
            }

            updated = apply_static_stability_assist(queue)

            self.assertEqual(updated["candidates"][0]["label"], "FALSE_POSITIVE")
            self.assertEqual(updated["candidates"][0]["review_status"], "COMPLETED")
            self.assertEqual(updated["candidates"][1]["review_status"], "PENDING")

    def test_keeps_audit_pending_when_any_transition_is_broadly_active(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            paths = []
            for index, value in enumerate((0, 255, 0)):
                path = root / f"change-{index}.jpg"
                cv2.imwrite(str(path), np.full((80, 120, 3), value, dtype=np.uint8))
                paths.append(str(path))
            queue = {
                "candidates": [
                    {
                        "candidate_kind": "AUDIT",
                        "review_status": "PENDING",
                        "label": "",
                        "evidence_frames": paths,
                    }
                ]
            }

            updated = apply_static_stability_assist(queue)

            candidate = updated["candidates"][0]
            self.assertEqual(candidate["review_status"], "PENDING")
            self.assertEqual(candidate["label"], "")
            self.assertGreater(candidate["review_assist"]["active_transition_count"], 0)


if __name__ == "__main__":
    unittest.main()
