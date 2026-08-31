import importlib
import sys
import types
import unittest


class TeacherBehaviorModelRuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._original_settings_module = sys.modules.get("app.core.settings")
        sys.modules.pop("app.services.teacher_behavior_service", None)
        settings_stub = types.SimpleNamespace(
            Teacher_Behavior_Thresd={
                "MergeIoU": 0.8,
                "ImageSize": 640,
                "sit": 0.4,
                "stand": 0.4,
                "bbwriting": 0.25,
                "teach": 0.25,
                "KeepOnlyMainSubject": True,
                "MainSubjectStrategy": "posture_confidence",
                "SubjectClusterIoU": 0.45,
                "PostureConflictRatio": 0.10,
                "PostureConflictDefault": "stand",
                "ForcePostureWhenMissing": True,
            },
            Inference=types.SimpleNamespace(TeacherUseHalf=False),
        )
        sys.modules["app.core.settings"] = types.SimpleNamespace(
            yolo_teacher_behavior_model=types.SimpleNamespace(),
            settings=settings_stub,
        )
        cls.service = importlib.import_module("app.services.teacher_behavior_service")

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("app.services.teacher_behavior_service", None)
        if cls._original_settings_module is None:
            sys.modules.pop("app.core.settings", None)
        else:
            sys.modules["app.core.settings"] = cls._original_settings_module

    def test_high_iou_boxes_are_one_subject_and_keep_all_teaching_labels(self):
        names = {0: "sit", 1: "stand", 2: "bbwriting", 3: "teach"}
        detections = [
            [10, 10, 110, 210, 0.91, 0],
            [11, 11, 111, 211, 0.88, 1],
            [12, 12, 112, 212, 0.83, 2],
            [13, 13, 113, 213, 0.81, 3],
        ]

        results = self.service.collect_teacher_behavior_results(
            detections,
            names,
            offset=(0, 0),
            merge_iou=0.8,
        )

        self.assertEqual(len(results["platform_person"]), 1)
        self.assertEqual(len(results["sitting"]), 0)
        self.assertEqual(len(results["standing"]), 1)
        self.assertEqual(len(results["writing"]), 1)
        self.assertEqual(len(results["teaching"]), 1)
        self.assertTrue(results["standing"][0].SuspectedSitting)
        self.assertFalse(results["standing"][0].PostureFallback)

    def test_new_teacher_behavior_object_types_map_sit_to_201_and_stand_to_202(self):
        self.assertEqual(self.service.TEACHER_BEHAVIOR_OBJECT_TYPES["sitting"], 201)
        self.assertEqual(self.service.TEACHER_BEHAVIOR_OBJECT_TYPES["standing"], 202)

    def test_old_teacher_behavior_entry_points_are_removed(self):
        self.assertFalse(hasattr(self.service, "TEACHER_OBJECT_TYPES"))
        self.assertFalse(hasattr(self.service, "analyze_teacher_behavior"))

    def test_teacher_result_list_is_sorted_by_object_type(self):
        position = self.service.ObjectPosition(
            LeftTopX=1,
            LeftTopY=2,
            RightBtmX=3,
            RightBtmY=4,
        )
        behavior_results = {
            "platform_person": [position],
            "standing": [position],
            "sitting": [position],
            "writing": [],
            "teaching": [],
        }

        result_list = self.service.build_teacher_result_list(
            behavior_results,
            self.service.TEACHER_BEHAVIOR_OBJECT_TYPES,
        )

        self.assertEqual([item.ObjectType for item in result_list], [100, 201, 202, 203, 204])
        self.assertEqual(result_list[1].ObjectPostList[0], position)
        self.assertEqual(result_list[2].ObjectPostList[0], position)

    def test_topmost_strategy_can_keep_topmost_subject_only(self):
        self.service.settings.Teacher_Behavior_Thresd["MainSubjectStrategy"] = "topmost"
        names = {1: "stand"}
        detections = [
            [10, 120, 110, 320, 0.91, 1],
            [300, 10, 400, 210, 0.89, 1],
        ]

        try:
            results = self.service.collect_teacher_behavior_results(
                detections,
                names,
                offset=(0, 0),
                merge_iou=0.8,
            )
        finally:
            self.service.settings.Teacher_Behavior_Thresd["MainSubjectStrategy"] = "posture_confidence"

        self.assertEqual(len(results["platform_person"]), 1)
        self.assertEqual(len(results["standing"]), 1)
        self.assertEqual(results["platform_person"][0].LeftTopY, 10)

    def test_posture_confidence_subject_wins_over_lower_confidence_multi_label_subject(self):
        names = {0: "sit", 1: "stand", 3: "teach"}
        detections = [
            [1257, 474, 1433, 658, 0.93, 1],
            [743, 904, 956, 1079, 0.42, 0],
            [742, 903, 957, 1080, 0.40, 3],
        ]

        results = self.service.collect_teacher_behavior_results(
            detections,
            names,
            offset=(0, 0),
            merge_iou=0.8,
        )

        self.assertEqual(len(results["platform_person"]), 1)
        self.assertEqual(results["platform_person"][0].LeftTopX, 1257)
        self.assertEqual(len(results["standing"]), 1)
        self.assertEqual(len(results["sitting"]), 0)
        self.assertEqual(len(results["teaching"]), 0)

    def test_subject_cluster_iou_merges_same_teacher_boxes_before_selecting_labels(self):
        names = {1: "stand", 3: "teach"}
        detections = [
            [10, 10, 110, 210, 0.90, 1],
            [10, 10, 110, 310, 0.85, 3],
        ]

        results = self.service.collect_teacher_behavior_results(
            detections,
            names,
            offset=(0, 0),
            merge_iou=0.8,
        )

        self.assertEqual(len(results["platform_person"]), 1)
        self.assertEqual(len(results["standing"]), 1)
        self.assertEqual(len(results["teaching"]), 1)

    def test_teacher_behavior_group_details_include_label_confidences(self):
        names = {1: "stand", 3: "teach"}
        detections = [
            [10, 10, 110, 210, 0.90, 1],
            [10, 10, 110, 310, 0.85, 3],
        ]

        details = self.service.collect_teacher_behavior_group_details(
            detections,
            names,
            offset=(0, 0),
            merge_iou=0.8,
        )

        self.assertEqual(len(details), 1)
        self.assertEqual(details[0]["position"].LeftTopX, 10)
        self.assertEqual(details[0]["confidences"]["platform_person"], 0.90)
        self.assertEqual(details[0]["confidences"]["standing"], 0.90)
        self.assertEqual(details[0]["confidences"]["teaching"], 0.85)

    def test_can_disable_main_subject_filter_from_config(self):
        self.service.settings.Teacher_Behavior_Thresd["KeepOnlyMainSubject"] = False
        names = {1: "stand"}
        detections = [
            [10, 120, 110, 320, 0.91, 1],
            [300, 10, 400, 210, 0.89, 1],
        ]

        try:
            results = self.service.collect_teacher_behavior_results(
                detections,
                names,
                offset=(0, 0),
                merge_iou=0.8,
            )
        finally:
            self.service.settings.Teacher_Behavior_Thresd["KeepOnlyMainSubject"] = True

        self.assertEqual(len(results["platform_person"]), 2)
        self.assertEqual(len(results["standing"]), 2)

    def test_merge_iou_threshold_comes_from_config(self):
        self.service.settings.Teacher_Behavior_Thresd["MergeIoU"] = 0.75

        try:
            self.assertEqual(self.service.get_teacher_behavior_merge_iou(), 0.75)
        finally:
            self.service.settings.Teacher_Behavior_Thresd["MergeIoU"] = 0.8

    def test_subject_cluster_iou_threshold_comes_from_config(self):
        self.service.settings.Teacher_Behavior_Thresd["SubjectClusterIoU"] = 0.5

        try:
            self.assertEqual(self.service.get_teacher_behavior_subject_cluster_iou(), 0.5)
        finally:
            self.service.settings.Teacher_Behavior_Thresd["SubjectClusterIoU"] = 0.45

    def test_posture_conflict_ratio_comes_from_config(self):
        self.service.settings.Teacher_Behavior_Thresd["PostureConflictRatio"] = 0.2

        try:
            self.assertEqual(self.service.get_teacher_behavior_posture_conflict_ratio(), 0.2)
        finally:
            self.service.settings.Teacher_Behavior_Thresd["PostureConflictRatio"] = 0.10

    def test_filters_each_label_by_configured_confidence_threshold(self):
        names = {0: "sit", 1: "stand", 3: "teach"}
        detections = [
            [10, 10, 110, 210, 0.24, 0],
            [10, 10, 110, 210, 0.40, 1],
            [10, 10, 110, 210, 0.25, 3],
        ]

        results = self.service.collect_teacher_behavior_results(
            detections,
            names,
            offset=(0, 0),
            merge_iou=0.8,
        )

        self.assertEqual(len(results["sitting"]), 0)
        self.assertEqual(len(results["standing"]), 1)
        self.assertEqual(len(results["teaching"]), 1)

    def test_predict_conf_uses_lowest_configured_label_threshold(self):
        self.service.settings.Teacher_Behavior_Thresd["teach"] = 0.1

        try:
            self.assertEqual(self.service.get_teacher_behavior_predict_conf(), 0.1)
        finally:
            self.service.settings.Teacher_Behavior_Thresd["teach"] = 0.25

    def test_teacher_model_uses_its_own_half_setting(self):
        captured = {}

        class FakeModel:
            names = {}

            def predict(self, *args, **kwargs):
                captured["half"] = kwargs["half"]
                return []

        original_model = self.service.yolo_teacher_behavior_model
        self.service.yolo_teacher_behavior_model = FakeModel()
        self.service.settings.Inference.TeacherUseHalf = True
        try:
            results, details = self.service.process_teacher_behavior_model_detection_with_details(
                self.service.np.zeros((64, 64, 3), dtype=self.service.np.uint8),
                (0, 0),
                (64, 64),
            )
        finally:
            self.service.settings.Inference.TeacherUseHalf = False
            self.service.yolo_teacher_behavior_model = original_model

        self.assertTrue(captured["half"])
        self.assertEqual(results, self.service.empty_teacher_behavior_results())
        self.assertEqual(details, [])

    def test_request_threshold_overrides_configured_label_thresholds(self):
        request_thresholds = {"sit": 0.7, "teach": 0.4}

        self.assertEqual(
            self.service.get_teacher_behavior_label_threshold("sit", request_thresholds),
            0.7,
        )
        self.assertEqual(
            self.service.get_teacher_behavior_label_threshold("stand", request_thresholds),
            0.4,
        )
        self.assertEqual(
            self.service.get_teacher_behavior_predict_conf(request_thresholds),
            0.25,
        )

    def test_collect_results_applies_request_threshold_overrides_per_label(self):
        names = {0: "sit", 1: "stand", 3: "teach"}
        detections = [
            [10, 10, 110, 210, 0.60, 0],
            [10, 10, 110, 210, 0.80, 1],
            [10, 10, 110, 210, 0.45, 3],
        ]

        results = self.service.collect_teacher_behavior_results(
            detections,
            names,
            offset=(0, 0),
            merge_iou=0.8,
            threshold_overrides={"sit": 0.7, "teach": 0.4},
        )

        self.assertEqual(len(results["sitting"]), 0)
        self.assertEqual(len(results["standing"]), 1)
        self.assertEqual(len(results["teaching"]), 1)

    def test_conflicting_postures_keep_high_confidence_when_ratio_is_large(self):
        names = {0: "sit", 1: "stand"}
        detections = [
            [10, 10, 110, 210, 0.50, 0],
            [10, 10, 110, 210, 0.80, 1],
        ]

        results = self.service.collect_teacher_behavior_results(
            detections,
            names,
            offset=(0, 0),
            merge_iou=0.8,
        )

        self.assertEqual(len(results["sitting"]), 0)
        self.assertEqual(len(results["standing"]), 1)
        self.assertEqual(results["standing"][0].Confidence, 0.80)
        self.assertFalse(results["standing"][0].SuspectedSitting)
        self.assertFalse(results["standing"][0].PostureFallback)

    def test_conflicting_close_postures_default_to_standing_and_marks_suspected_sitting(self):
        names = {0: "sit", 1: "stand"}
        detections = [
            [10, 10, 110, 210, 0.76, 0],
            [10, 10, 110, 210, 0.80, 1],
        ]

        results = self.service.collect_teacher_behavior_results(
            detections,
            names,
            offset=(0, 0),
            merge_iou=0.8,
        )

        self.assertEqual(len(results["sitting"]), 0)
        self.assertEqual(len(results["standing"]), 1)
        self.assertEqual(results["standing"][0].Confidence, 0.80)
        self.assertTrue(results["standing"][0].SuspectedSitting)
        self.assertFalse(results["standing"][0].PostureFallback)

    def test_missing_posture_falls_back_to_standing_with_subject_confidence(self):
        names = {3: "teach"}
        detections = [
            [10, 10, 110, 210, 0.60, 3],
        ]

        results = self.service.collect_teacher_behavior_results(
            detections,
            names,
            offset=(0, 0),
            merge_iou=0.8,
        )

        self.assertEqual(len(results["platform_person"]), 1)
        self.assertEqual(len(results["sitting"]), 0)
        self.assertEqual(len(results["standing"]), 1)
        self.assertEqual(results["standing"][0].Confidence, 0.60)
        self.assertFalse(results["standing"][0].SuspectedSitting)
        self.assertTrue(results["standing"][0].PostureFallback)

    def test_grouping_uses_low_threshold_posture_candidates_before_output_filtering(self):
        names = {1: "stand", 3: "teach"}
        detections = [
            [1811, 390, 1915, 524, 0.51, 3],
            [951, 502, 1130, 682, 0.35, 3],
            [952, 502, 1129, 865, 0.34, 1],
        ]

        results = self.service.collect_teacher_behavior_results(
            detections,
            names,
            offset=(0, 0),
            merge_iou=0.8,
        )

        self.assertEqual(len(results["platform_person"]), 1)
        self.assertEqual(results["platform_person"][0].LeftTopX, 952)
        self.assertEqual(len(results["standing"]), 1)
        self.assertTrue(results["standing"][0].PostureFallback)


if __name__ == "__main__":
    unittest.main()
