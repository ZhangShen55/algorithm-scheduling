import unittest

from harness.tools.review_decisions import apply_review_decisions


class ReviewDecisionHarnessTests(unittest.TestCase):
    def test_applies_numbered_decision_without_mutating_other_candidates(self):
        queue = {
            "candidates": [
                {"label": "", "review_status": "PENDING", "notes": ""},
                {"label": "", "review_status": "PENDING", "notes": ""},
            ]
        }
        decisions = {
            "reviewer": "codex-static-evidence-v6",
            "decisions": [
                {
                    "candidate_number": 2,
                    "label": "CONFIRMED_VIDEO",
                    "notes": "静态证据确认连续视频播放。",
                }
            ],
        }

        updated = apply_review_decisions(queue, decisions, reviewed_at="2026-08-07T00:00:00Z")

        self.assertEqual(updated["candidates"][0]["review_status"], "PENDING")
        self.assertEqual(updated["candidates"][1]["review_status"], "COMPLETED")
        self.assertEqual(updated["candidates"][1]["label"], "CONFIRMED_VIDEO")
        self.assertEqual(updated["candidates"][1]["reviewer"], "codex-static-evidence-v6")

    def test_rejects_unknown_label(self):
        with self.assertRaises(ValueError):
            apply_review_decisions(
                {"candidates": [{"label": "", "review_status": "PENDING"}]},
                {"reviewer": "tester", "decisions": [{"candidate_number": 1, "label": "VIDEO"}]},
            )


if __name__ == "__main__":
    unittest.main()
