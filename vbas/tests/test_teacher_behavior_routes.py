import importlib
import sys
import types
import unittest


class TeacherBehaviorRouteTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._original_modules = {
            name: sys.modules.get(name)
            for name in (
                "app.api.stu_tea_behavior",
                "app.services.student_behavior_service",
                "app.services.teacher_behavior_service",
            )
        }
        sys.modules.pop("app.api.stu_tea_behavior", None)
        self.called_with = None
        self.student_called_with = None
        self.student_parallel_called_with = None

        async def analyze_student_behavior(request):
            self.student_called_with = request
            return "student-response"

        async def analyze_student_behavior_parallel(request):
            self.student_parallel_called_with = request
            return "student-parallel-response"

        async def analyze_teacher_behavior_by_model(request):
            self.called_with = request
            return "teacher-model-response"

        sys.modules["app.services.student_behavior_service"] = types.SimpleNamespace(
            analyze_student_behavior=analyze_student_behavior,
            analyze_student_behavior_parallel=analyze_student_behavior_parallel,
        )
        sys.modules["app.services.teacher_behavior_service"] = types.SimpleNamespace(
            analyze_teacher_behavior_by_model=analyze_teacher_behavior_by_model,
        )

    def tearDown(self):
        sys.modules.pop("app.api.stu_tea_behavior", None)
        for name, module in self._original_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    async def test_teacher_route_uses_new_model_service_and_old_route_is_removed(self):
        api = importlib.import_module("app.api.stu_tea_behavior")
        paths = {route.path for route in api.router.routes}

        self.assertIn("/ImageDetect/teacher/v1.0.0", paths)
        self.assertNotIn("/ImageDetect/teacher_behavior/v1.0.0", paths)

        self.assertNotIn("/ImageDetect/teacher/v2.0.0", paths)

        request = types.SimpleNamespace(ImageList=[object()], ReturnHeadPose=True)
        response = await api.teacher_behavior_analysis(request)

        self.assertEqual(response, "teacher-model-response")
        self.assertIs(self.called_with, request)

    async def test_student_v100_uses_parallel_service_and_v101_is_removed(self):
        api = importlib.import_module("app.api.stu_tea_behavior")
        paths = {route.path for route in api.router.routes}
        request = types.SimpleNamespace(ImageList=[object()])

        response = await api.student_behavior_analysis(request)

        self.assertIn("/ImageDetect/student/v1.0.0", paths)
        self.assertNotIn("/ImageDetect/student/v1.0.1", paths)
        self.assertEqual(response, "student-parallel-response")
        self.assertIsNone(self.student_called_with)
        self.assertIs(self.student_parallel_called_with, request)


if __name__ == "__main__":
    unittest.main()
