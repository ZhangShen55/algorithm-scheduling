import importlib
import sys
import types
import unittest

import numpy as np


class TeacherBehaviorServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._original_modules = {
            name: sys.modules.get(name)
            for name in (
                "app.core.settings",
                "app.services.teacher_behavior_service",
                "app.services.capacity_service",
            )
        }
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
            Teacher_Head_Pose={
                "Enabled": True,
            },
            Inference=types.SimpleNamespace(TeacherUseHalf=False),
        )
        sys.modules["app.core.settings"] = types.SimpleNamespace(
            yolo_teacher_behavior_model=types.SimpleNamespace(),
            settings=settings_stub,
        )
        sys.modules["app.services.capacity_service"] = types.SimpleNamespace(
            increment_connection=lambda: None,
            increment_processed_images=lambda count: None,
        )
        self.service = importlib.import_module("app.services.teacher_behavior_service")

    def tearDown(self):
        sys.modules.pop("app.services.teacher_behavior_service", None)
        for name, module in self._original_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    async def test_teacher_v1_includes_head_pose_result_when_requested_and_enabled(self):
        from app.schemas.stu_tea_behavior import HeadPoseResult, ImageItem, TeacherBehaviorRequest

        teacher_position = self.service.ObjectPosition(
            LeftTopX=10,
            LeftTopY=20,
            RightBtmX=110,
            RightBtmY=220,
            Confidence=0.92,
        )
        behavior_results = {
            "platform_person": [teacher_position],
            "standing": [teacher_position],
            "sitting": [],
            "writing": [],
            "teaching": [],
        }
        details = [{
            "position": teacher_position,
            "positions": {"platform_person": teacher_position, "standing": teacher_position},
            "confidences": {"platform_person": 0.92, "standing": 0.92},
            "best_by_behavior": {},
        }]

        self.service.load_behavior_image = lambda image_item: np.zeros((300, 300, 3), dtype=np.uint8)
        self.service.process_teacher_behavior_model_detection_with_details = (
            lambda img, offset, img_size, thresholds: (behavior_results, details)
        )
        self.service.analyze_teacher_head_pose = lambda img, position: HeadPoseResult(
            Enabled=True,
            Status="success",
            FaceDirection="right",
            Yaw=35.0,
            Pitch=31.0,
            Roll=2.0,
            Angle=10.0,
            IsLookingDown=True,
            HeadPoseConfidence=0.86,
            TeacherConfidence=0.92,
        )

        response = await self.service.analyze_teacher_behavior_by_model(
            TeacherBehaviorRequest(
                ImageList=[ImageItem(StoragePath="x.jpg", ImageId="img-1")],
                ReturnHeadPose=True,
            )
        )

        self.assertEqual(response.DataList[0].ResultList[0].ObjectType, 100)
        self.assertEqual(response.DataList[0].HeadPoseResult.Status, "success")
        self.assertEqual(response.DataList[0].HeadPoseResult.FaceDirection, "right")
        self.assertEqual(response.DataList[0].HeadPoseResult.Angle, 10.0)

    async def test_teacher_v1_ignores_return_head_pose_when_config_disabled(self):
        from app.schemas.stu_tea_behavior import ImageItem, TeacherBehaviorRequest

        self.service.settings.Teacher_Head_Pose["Enabled"] = False
        teacher_position = self.service.ObjectPosition(
            LeftTopX=10,
            LeftTopY=20,
            RightBtmX=110,
            RightBtmY=220,
            Confidence=0.92,
        )
        behavior_results = {
            "platform_person": [teacher_position],
            "standing": [teacher_position],
            "sitting": [],
            "writing": [],
            "teaching": [],
        }
        details = [{
            "position": teacher_position,
            "positions": {"platform_person": teacher_position, "standing": teacher_position},
            "confidences": {"platform_person": 0.92, "standing": 0.92},
            "best_by_behavior": {},
        }]

        self.service.load_behavior_image = lambda image_item: np.zeros((300, 300, 3), dtype=np.uint8)
        self.service.process_teacher_behavior_model_detection_with_details = (
            lambda img, offset, img_size, thresholds: (behavior_results, details)
        )
        self.service.analyze_teacher_head_pose = lambda img, position: (_ for _ in ()).throw(
            AssertionError("head pose model should not be called when config is disabled")
        )

        response = await self.service.analyze_teacher_behavior_by_model(
            TeacherBehaviorRequest(
                ImageList=[ImageItem(StoragePath="x.jpg", ImageId="img-1")],
                ReturnHeadPose=True,
            )
        )

        self.assertIsNone(response.DataList[0].HeadPoseResult)


if __name__ == "__main__":
    unittest.main()
