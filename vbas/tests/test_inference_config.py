import unittest

from app.core.inference_config import InferenceSettings


class InferenceSettingsTest(unittest.TestCase):
    def test_defaults_are_sequential_fp32(self):
        settings = InferenceSettings()

        self.assertTrue(settings.StudentModelsSequential)
        self.assertTrue(settings.SyncTasks2PolygonsSequential)
        self.assertFalse(settings.PersonUseHalf)
        self.assertFalse(settings.FaceUseHalf)
        self.assertFalse(settings.StudentUseHalf)
        self.assertFalse(settings.TeacherUseHalf)
        self.assertFalse(hasattr(settings, "GpuInferenceConcurrency"))

    def test_explicit_values_are_independent(self):
        settings = InferenceSettings(
            StudentModelsSequential=False,
            SyncTasks2PolygonsSequential=False,
            PersonUseHalf=True,
            FaceUseHalf=False,
            StudentUseHalf=True,
            TeacherUseHalf=False,
        )

        self.assertFalse(settings.StudentModelsSequential)
        self.assertFalse(settings.SyncTasks2PolygonsSequential)
        self.assertTrue(settings.PersonUseHalf)
        self.assertFalse(settings.FaceUseHalf)
        self.assertTrue(settings.StudentUseHalf)
        self.assertFalse(settings.TeacherUseHalf)

    def test_partial_mapping_keeps_missing_defaults(self):
        settings = InferenceSettings.model_validate({"PersonUseHalf": True})

        self.assertTrue(settings.StudentModelsSequential)
        self.assertTrue(settings.SyncTasks2PolygonsSequential)
        self.assertTrue(settings.PersonUseHalf)
        self.assertFalse(settings.FaceUseHalf)


if __name__ == "__main__":
    unittest.main()
