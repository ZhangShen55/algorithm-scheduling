import importlib
import sys
import types
import unittest

import numpy as np

from app.schemas.geometry import Point, PolygonArea


class _FakeBoxesData:
    def tolist(self):
        return []


class _FakeModel:
    def __init__(self):
        self.half_values = []

    def predict(self, *args, **kwargs):
        self.half_values.append(kwargs["half"])
        return [types.SimpleNamespace(boxes=types.SimpleNamespace(data=_FakeBoxesData()))]


class SyncTasksInferenceWiringTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_settings = sys.modules.get("app.core.settings")
        self.person_model = _FakeModel()
        self.face_model = _FakeModel()
        self.settings = types.SimpleNamespace(
            Person_Thresd={
                "Head": 0.25,
                "Top_Head": 0.1,
                "Hat": 0.1,
                "Headphones": 0.1,
                "Shoulder": 0.1,
            },
            Face_Thresd={"face": 0.1},
            Inference=types.SimpleNamespace(
                PersonUseHalf=True,
                FaceUseHalf=False,
                SyncTasks2PolygonsSequential=True,
            ),
        )
        sys.modules["app.core.settings"] = types.SimpleNamespace(
            yolo_person_model=self.person_model,
            yolo_face_model=self.face_model,
            settings=self.settings,
            Total_HaveProcess_Tasks={"val": 0},
        )
        self.modules = []
        for name in ("app.services.task_service", "app.services.task_service_base64"):
            sys.modules.pop(name, None)
            self.modules.append(importlib.import_module(name))

    def tearDown(self):
        for module in self.modules:
            sys.modules.pop(module.__name__, None)
        if self.original_settings is None:
            sys.modules.pop("app.core.settings", None)
        else:
            sys.modules["app.core.settings"] = self.original_settings

    async def test_file_and_base64_polygon_paths_use_independent_half_flags(self):
        polygon = PolygonArea(
            Label=1,
            Enable=True,
            Points=[
                Point(X=0, Y=0),
                Point(X=7, Y=0),
                Point(X=7, Y=7),
                Point(X=0, Y=7),
            ],
        )
        image = np.zeros((8, 8, 3), dtype=np.uint8)

        for module in self.modules:
            await module.process_polygon(0, polygon, image, 8, 8, "task-1")

        self.assertEqual(self.person_model.half_values, [True, True])
        self.assertEqual(self.face_model.half_values, [False, False])


if __name__ == "__main__":
    unittest.main()
