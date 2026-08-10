import unittest

from harness.tools.prepare_review import prepare_review_queue


class ReviewQueueTests(unittest.TestCase):
    def test_records_detection_and_inventory_run_ids_separately(self):
        inventory = {
            "run_id": "inventory-v1",
            "inventory_fingerprint": "inventory-sha256",
            "items": [],
        }
        detections = {
            "run_id": "detection-v2",
            "inventory_run_id": "inventory-v1",
            "items": [],
        }

        queue = prepare_review_queue(inventory, detections)

        self.assertEqual(queue["run_id"], "detection-v2")
        self.assertEqual(queue["inventory_run_id"], "inventory-v1")

    def test_includes_detection_candidates_and_missed_detection_audits(self):
        url = "http://example.test/course/PPT.mp4"
        inventory = {
            "items": [{"url": url, "course_name": "课程", "duration": 600.0}]
        }
        detections = {
            "items": [
                {
                    "url": url,
                    "status": "COMPLETED",
                    "dynamic_segments": [{"start_ms": 100000, "end_ms": 160000, "confidence": 0.8}],
                    "slices": [{"snap_time": 0}, {"snap_time": 300}],
                    "near_threshold_windows": [],
                }
            ]
        }

        queue = prepare_review_queue(inventory, detections)

        self.assertEqual(queue["candidates"][0]["candidate_kind"], "DETECTION")
        self.assertTrue(any(item["candidate_kind"] == "AUDIT" for item in queue["candidates"]))
        self.assertTrue(all(item["review_status"] == "PENDING" for item in queue["candidates"]))

    def test_preserves_existing_completed_review_by_stable_candidate_key(self):
        url = "http://example.test/course/PPT.mp4"
        inventory = {"items": [{"url": url, "course_name": "课程", "duration": 60.0}]}
        detections = {
            "items": [
                {
                    "url": url,
                    "status": "COMPLETED",
                    "dynamic_segments": [{"start_ms": 1000, "end_ms": 5000, "confidence": 0.8}],
                    "slices": [],
                    "near_threshold_windows": [],
                }
            ]
        }
        existing = [
            {
                "url": url,
                "candidate_kind": "DETECTION",
                "start_ms": 1000,
                "end_ms": 5000,
                "label": "CONFIRMED_VIDEO",
                "review_status": "COMPLETED",
                "reviewer": "codex",
            }
        ]

        queue = prepare_review_queue(inventory, detections, existing_reviews=existing)

        self.assertEqual(queue["candidates"][0]["label"], "CONFIRMED_VIDEO")
        self.assertEqual(queue["candidates"][0]["review_status"], "COMPLETED")


if __name__ == "__main__":
    unittest.main()
