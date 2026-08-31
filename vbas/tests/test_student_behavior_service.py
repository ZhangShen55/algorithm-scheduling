import importlib
import sys
import threading
import types
import unittest

import numpy as np


class _FakeBoxesData:
    def __init__(self, rows):
        self._rows = rows

    def tolist(self):
        return self._rows


class _FakeStudentModel:
    def __init__(self, rows):
        self._rows = rows
        self.last_conf = None
        self.last_imgsz = None
        self.last_half = None

    def predict(self, img, conf, imgsz, half, verbose):
        self.last_conf = conf
        self.last_imgsz = imgsz
        self.last_half = half
        return [types.SimpleNamespace(boxes=types.SimpleNamespace(data=_FakeBoxesData(self._rows)))]


class _FakeDetectionModel:
    def __init__(self):
        self.last_imgsz = None
        self.last_half = None

    def predict(self, img, conf, imgsz, half, verbose):
        self.last_imgsz = imgsz
        self.last_half = half
        return [types.SimpleNamespace(boxes=types.SimpleNamespace(data=_FakeBoxesData([])))]


class StudentBehaviorServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._original_modules = {
            name: sys.modules.get(name)
            for name in (
                "app.core.settings",
                "app.services.student_behavior_service",
            )
        }
        sys.modules.pop("app.services.student_behavior_service", None)
        self.student_model = _FakeStudentModel([
            [10, 10, 30, 30, 0.51, 0],  # Using_phone
            [40, 10, 60, 30, 0.80, 1],  # Hand_raising
            [70, 10, 90, 30, 0.44, 2],  # Sleep
            [100, 10, 120, 30, 0.84, 3],  # standing
            [130, 10, 150, 30, 0.52, 4],  # Read_W
        ])
        self.person_model = _FakeDetectionModel()
        self.face_model = _FakeDetectionModel()
        settings_stub = types.SimpleNamespace(
            IMAGE_ROOT="/tmp",
            Student_Thresd={
                "phone": 0.3,
                "hand": 0.75,
                "sleep": 0.15,
                "stand": 0.2,
                "read": 0.4,
            },
            Inference=types.SimpleNamespace(
                StudentModelsSequential=True,
                SyncTasks2PolygonsSequential=True,
                PersonUseHalf=False,
                FaceUseHalf=False,
                StudentUseHalf=False,
                TeacherUseHalf=False,
            ),
        )
        sys.modules["app.core.settings"] = types.SimpleNamespace(
            yolo_person_model=self.person_model,
            yolo_face_model=self.face_model,
            yolo_student_model=self.student_model,
            settings=settings_stub,
        )
        self.service = importlib.import_module("app.services.student_behavior_service")

    def tearDown(self):
        sys.modules.pop("app.services.student_behavior_service", None)
        for name, module in self._original_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def test_request_thresholds_override_student_behavior_config_by_simplified_fields(self):
        from app.schemas.stu_tea_behavior import StudentBehaviorRequest

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

        results = self.service.process_student_behavior(
            np.zeros((200, 200, 3), dtype=np.uint8),
            offset=(0, 0),
            img_size=(200, 200),
            threshold_overrides=request.Student_Thresd,
        )

        self.assertEqual(len(results["Using_phone"]), 1)
        self.assertEqual(len(results["Hand_raising"]), 0)
        self.assertEqual(len(results["Sleep"]), 0)
        self.assertEqual(len(results["standing"]), 0)
        self.assertEqual(len(results["Read_W"]), 1)
        self.assertEqual(self.student_model.last_conf, 0.45)

    def test_student_behavior_uses_config_thresholds_when_request_has_no_override(self):
        results = self.service.process_student_behavior(
            np.zeros((200, 200, 3), dtype=np.uint8),
            offset=(0, 0),
            img_size=(200, 200),
        )

        self.assertEqual(len(results["Using_phone"]), 1)
        self.assertEqual(len(results["Hand_raising"]), 1)
        self.assertEqual(len(results["Sleep"]), 1)
        self.assertEqual(len(results["standing"]), 1)
        self.assertEqual(len(results["Read_W"]), 1)
        self.assertEqual(self.student_model.last_conf, 0.15)

    def test_simplified_config_keys_map_to_model_labels(self):
        self.assertEqual(self.service.get_student_behavior_label_threshold("Using_phone"), 0.3)
        self.assertEqual(self.service.get_student_behavior_label_threshold("Hand_raising"), 0.75)
        self.assertEqual(self.service.get_student_behavior_label_threshold("Sleep"), 0.15)
        self.assertEqual(self.service.get_student_behavior_label_threshold("standing"), 0.2)
        self.assertEqual(self.service.get_student_behavior_label_threshold("Read_W"), 0.4)

    async def test_parallel_detection_runs_all_three_models_concurrently(self):
        barrier = threading.Barrier(3)
        thread_ids = set()

        def make_detector(result):
            def detector(*args, **kwargs):
                thread_ids.add(threading.get_ident())
                barrier.wait(timeout=2)
                return result

            return detector

        original_person = self.service.process_person_detection
        original_face = self.service.process_face_detection
        original_student = self.service.process_student_behavior
        self.service.process_person_detection = make_detector("person")
        self.service.process_face_detection = make_detector("face")
        self.service.process_student_behavior = make_detector("student")

        try:
            results = await self.service.process_student_detections_parallel(
                np.zeros((200, 200, 3), dtype=np.uint8),
                offset=(0, 0),
                img_size=(200, 200),
                threshold_overrides=None,
            )
        finally:
            self.service.process_person_detection = original_person
            self.service.process_face_detection = original_face
            self.service.process_student_behavior = original_student

        self.assertEqual(results, ("person", "face", "student"))
        self.assertEqual(len(thread_ids), 3)

    async def test_parallel_detection_uses_1920_for_student_model(self):
        captured = {}

        def person_detector(img, offset, img_size):
            captured["person"] = img_size
            return "person"

        def face_detector(img, offset, img_size):
            captured["face"] = img_size
            return "face"

        def student_detector(img, offset, img_size, threshold_overrides, inference_imgsz=None):
            captured["student"] = inference_imgsz
            return "student"

        original_person = self.service.process_person_detection
        original_face = self.service.process_face_detection
        original_student = self.service.process_student_behavior
        self.service.process_person_detection = person_detector
        self.service.process_face_detection = face_detector
        self.service.process_student_behavior = student_detector

        try:
            await self.service.process_student_detections_parallel(
                np.zeros((1080, 1920, 3), dtype=np.uint8),
                offset=(0, 0),
                img_size=(1080, 1920),
                threshold_overrides=None,
            )
        finally:
            self.service.process_person_detection = original_person
            self.service.process_face_detection = original_face
            self.service.process_student_behavior = original_student

        self.assertEqual(captured["person"], (1080, 1920))
        self.assertEqual(captured["face"], (1080, 1920))
        self.assertEqual(captured["student"], 1920)

    async def test_sequential_detection_runs_models_in_order(self):
        calls = []

        def person_detector(*args, **kwargs):
            calls.append("person")
            return "person"

        def face_detector(*args, **kwargs):
            calls.append("face")
            return "face"

        def student_detector(*args, **kwargs):
            calls.append("student")
            return "student"

        original_person = self.service.process_person_detection
        original_face = self.service.process_face_detection
        original_student = self.service.process_student_behavior
        self.service.process_person_detection = person_detector
        self.service.process_face_detection = face_detector
        self.service.process_student_behavior = student_detector
        try:
            results = await self.service.process_student_detections_sequential(
                np.zeros((200, 200, 3), dtype=np.uint8),
                offset=(0, 0),
                img_size=(200, 200),
            )
        finally:
            self.service.process_person_detection = original_person
            self.service.process_face_detection = original_face
            self.service.process_student_behavior = original_student

        self.assertEqual(calls, ["person", "face", "student"])
        self.assertEqual(results, ("person", "face", "student"))

    async def test_analyze_uses_inference_config_to_select_detection_mode(self):
        from app.schemas.stu_tea_behavior import ImageItem, StudentBehaviorRequest

        calls = []

        async def sequential_detector(*args, **kwargs):
            calls.append("sequential")
            return [], [], {}

        async def parallel_detector(*args, **kwargs):
            calls.append("parallel")
            return [], [], {}

        original_imread = self.service.cv2.imread
        original_sequential = self.service.process_student_detections_sequential
        original_parallel = self.service.process_student_detections_parallel
        self.service.cv2.imread = lambda path: np.zeros((64, 64, 3), dtype=np.uint8)
        self.service.process_student_detections_sequential = sequential_detector
        self.service.process_student_detections_parallel = parallel_detector
        request = StudentBehaviorRequest(
            ImageList=[ImageItem(StoragePath="fixture.jpg", ImageId="img-1")],
        )

        try:
            self.service.settings.Inference.StudentModelsSequential = True
            await self.service.analyze_student_behavior(request)
            self.service.settings.Inference.StudentModelsSequential = False
            await self.service.analyze_student_behavior(request)
        finally:
            self.service.settings.Inference.StudentModelsSequential = True
            self.service.cv2.imread = original_imread
            self.service.process_student_detections_sequential = original_sequential
            self.service.process_student_detections_parallel = original_parallel

        self.assertEqual(calls, ["sequential", "parallel"])

    def test_each_student_model_uses_its_own_half_setting(self):
        self.service.settings.Inference.PersonUseHalf = True
        self.service.settings.Inference.FaceUseHalf = False
        self.service.settings.Inference.StudentUseHalf = True
        image = np.zeros((200, 200, 3), dtype=np.uint8)

        self.service.process_person_detection(image, (0, 0), (200, 200))
        self.service.process_face_detection(image, (0, 0), (200, 200))
        self.service.process_student_behavior(image, (0, 0), (200, 200))

        self.assertTrue(self.person_model.last_half)
        self.assertFalse(self.face_model.last_half)
        self.assertTrue(self.student_model.last_half)

    def test_inference_size_is_capped_to_full_hd_for_large_images(self):
        self.assertEqual(self.service.get_capped_inference_size((2160, 3840)), (1088, 1920))
        self.assertEqual(self.service.get_capped_inference_size((1440, 2560)), (1088, 1920))
        self.assertEqual(self.service.get_capped_inference_size((2000, 1000)), (1088, 544))
        self.assertEqual(self.service.get_capped_inference_size((720, 1280)), (720, 1280))

    def test_all_models_cap_large_images_and_student_uses_1920_for_full_hd(self):
        image = np.zeros((200, 200, 3), dtype=np.uint8)

        self.service.process_person_detection(image, (0, 0), (2160, 3840))
        self.service.process_face_detection(image, (0, 0), (2160, 3840))
        self.service.process_student_behavior(image, (0, 0), (2160, 3840))

        self.assertEqual(self.person_model.last_imgsz, (1088, 1920))
        self.assertEqual(self.face_model.last_imgsz, (1088, 1920))
        self.assertEqual(self.student_model.last_imgsz, (1088, 1920))

        self.service.process_student_behavior(image, (0, 0), (1080, 1920))
        self.assertEqual(self.student_model.last_imgsz, 1920)


if __name__ == "__main__":
    unittest.main()
