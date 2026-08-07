import unittest
from unittest.mock import patch

from tests2.run_teacher_head_pose_eval import (
    DirectionThresholds,
    classify_face_direction,
    expand_box,
    select_head_prediction,
    validate_directmhp_runtime_dependencies,
)


class HeadPoseEvalSelfTest(unittest.TestCase):
    def test_classifies_front_left_right_down_and_board(self):
        thresholds = DirectionThresholds()

        self.assertEqual(classify_face_direction(0, 0, False, thresholds).direction, "front")
        self.assertEqual(classify_face_direction(-35, 0, False, thresholds).direction, "left")
        self.assertEqual(classify_face_direction(35, 0, False, thresholds).direction, "right")
        self.assertEqual(classify_face_direction(0, 32, False, thresholds).direction, "down")
        self.assertEqual(classify_face_direction(42, 0, True, thresholds).direction, "board")

    def test_expand_box_clamps_to_image_bounds(self):
        self.assertEqual(
            expand_box((10, 20, 110, 220), image_width=120, image_height=230, scale=1.5),
            (0, 0, 120, 230),
        )

    def test_select_head_prediction_prefers_high_confidence_then_topmost(self):
        predictions = [
            {"confidence": 0.70, "box": (10, 120, 50, 160)},
            {"confidence": 0.80, "box": (10, 150, 50, 190)},
            {"confidence": 0.80, "box": (10, 80, 50, 120)},
        ]

        selected = select_head_prediction(predictions)

        self.assertEqual(selected["box"], (10, 80, 50, 120))

    def test_validate_runtime_dependencies_reports_pkg_resources_fix(self):
        with patch("tests2.run_teacher_head_pose_eval.importlib.util.find_spec", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "setuptools<81"):
                validate_directmhp_runtime_dependencies()


if __name__ == "__main__":
    unittest.main()
