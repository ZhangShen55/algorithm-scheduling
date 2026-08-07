import unittest

from app.schemas.stu_tea_behavior import (
    HeadPoseResult,
    StudentBehaviorRequest,
    Stu_Tea_BehaviorRequest,
    TeacherBehaviorImageResult,
    TeacherBehaviorRequest,
)


class StuTeaBehaviorSchemaTest(unittest.TestCase):
    def test_teacher_behavior_thresholds_are_optional_request_fields(self):
        request = Stu_Tea_BehaviorRequest(
            ImageList=[],
            Teacher_Behavior_Thresd={
                "sit": 0.7,
                "stand": 0.6,
            },
        )

        self.assertEqual(request.Teacher_Behavior_Thresd.sit, 0.7)
        self.assertEqual(request.Teacher_Behavior_Thresd.stand, 0.6)
        self.assertIsNone(request.Teacher_Behavior_Thresd.bbwriting)
        self.assertIsNone(request.Teacher_Behavior_Thresd.teach)

    def test_teacher_request_can_enable_head_pose(self):
        request = TeacherBehaviorRequest(
            ImageList=[],
            ReturnHeadPose=True,
        )

        self.assertTrue(request.ReturnHeadPose)

    def test_student_behavior_thresholds_use_simplified_request_fields(self):
        request = StudentBehaviorRequest(
            ImageList=[],
            Student_Thresd={
                "phone": 0.5,
                "hand": 0.99,
                "sleep": 0.45,
                "stand": 0.99,
                "read": 0.5,
            },
        )

        self.assertEqual(request.Student_Thresd.phone, 0.5)
        self.assertEqual(request.Student_Thresd.hand, 0.99)
        self.assertEqual(request.Student_Thresd.sleep, 0.45)
        self.assertEqual(request.Student_Thresd.stand, 0.99)
        self.assertEqual(request.Student_Thresd.read, 0.5)

    def test_teacher_image_result_can_include_head_pose_result_without_changing_student_schema(self):
        head_pose = HeadPoseResult(
            Enabled=True,
            Status="success",
            FaceDirection="right",
            Yaw=35.0,
            Pitch=31.0,
            Roll=2.0,
            Angle=10.0,
            IsLookingDown=True,
            HeadPoseConfidence=0.86,
            TeacherConfidence=0.95,
        )
        result = TeacherBehaviorImageResult(
            StatusObject={"StatusString": "success", "StatusCode": 0},
            ResultList=[],
            HeadPoseResult=head_pose,
        )

        self.assertEqual(result.HeadPoseResult.FaceDirection, "right")
        self.assertTrue(result.HeadPoseResult.IsLookingDown)
        self.assertFalse(hasattr(Stu_Tea_BehaviorRequest, "ReturnHeadPose"))


if __name__ == "__main__":
    unittest.main()
