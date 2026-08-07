import os
import tempfile
import unittest
from pathlib import Path

from app.core.config_loader import load_config


class ConfigLoaderTest(unittest.TestCase):
    def test_loads_toml_config_without_ias_registration_settings(self):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as config_file:
            config_file.write(
                """
IMAGE_ROOT = "/mnt/ias-images"
INSTANCE_COUNT = 1
WORKERS_PER_INSTANCE = 1
GPU_ID = 0

[Person_Thresd]
Head = 0.25
Top_Head = 0.1
Hat = 0.1
Headphones = 0.1
Shoulder = 0.1

[Face_Thresd]
face = 0.1

[Student_Thresd]
Using_phone = 0.3
Hand_raising = 0.75
Sleep = 0.15
standing = 0.2
Read_W = 0.4

[Teacher_Behavior_Thresd]
MergeIoU = 0.8
ImageSize = 640
sit = 0.4
stand = 0.4
bbwriting = 0.25
teach = 0.25
KeepOnlyMainSubject = true
SubjectClusterIoU = 0.45
MainSubjectStrategy = "posture_confidence"
PostureConflictRatio = 0.10
PostureConflictDefault = "stand"
ForcePostureWhenMissing = true

[ModelProtection]
Enabled = true
EncryptedModelRoot = "/secure/models"
DecryptedTempRoot = "/dev/shm/tias-models"
KeyFile = "/run/secrets/tias_model_key"
CleanupAfterLoad = true
"""
            )
            config_path = config_file.name

        try:
            config = load_config(config_path)
        finally:
            os.unlink(config_path)

        self.assertNotIn("AEM", config)
        self.assertNotIn("SERVER", config)
        self.assertEqual(config["GPU_ID"], 0)
        self.assertEqual(config["Person_Thresd"]["Head"], 0.25)
        self.assertEqual(config["Student_Thresd"]["Read_W"], 0.4)
        self.assertNotIn("Teacher_Thresd", config)
        self.assertEqual(config["Teacher_Behavior_Thresd"]["MergeIoU"], 0.8)
        self.assertEqual(config["Teacher_Behavior_Thresd"]["ImageSize"], 640)
        self.assertEqual(config["Teacher_Behavior_Thresd"]["sit"], 0.4)
        self.assertEqual(config["Teacher_Behavior_Thresd"]["stand"], 0.4)
        self.assertEqual(config["Teacher_Behavior_Thresd"]["bbwriting"], 0.25)
        self.assertEqual(config["Teacher_Behavior_Thresd"]["teach"], 0.25)
        self.assertTrue(config["Teacher_Behavior_Thresd"]["KeepOnlyMainSubject"])
        self.assertEqual(config["Teacher_Behavior_Thresd"]["SubjectClusterIoU"], 0.45)
        self.assertEqual(config["Teacher_Behavior_Thresd"]["MainSubjectStrategy"], "posture_confidence")
        self.assertEqual(config["Teacher_Behavior_Thresd"]["PostureConflictRatio"], 0.10)
        self.assertEqual(config["Teacher_Behavior_Thresd"]["PostureConflictDefault"], "stand")
        self.assertTrue(config["Teacher_Behavior_Thresd"]["ForcePostureWhenMissing"])
        self.assertTrue(config["ModelProtection"]["Enabled"])
        self.assertEqual(config["ModelProtection"]["EncryptedModelRoot"], "/secure/models")

    def test_repository_config_has_no_ias_registration_settings(self):
        config_path = Path(__file__).resolve().parents[1] / "config.toml"

        config = load_config(str(config_path))

        self.assertNotIn("AEM", config)
        self.assertNotIn("SERVER", config)
        self.assertNotIn("Teacher_Thresd", config)
        self.assertEqual(config["Teacher_Behavior_Thresd"]["MergeIoU"], 0.8)
        self.assertEqual(config["Teacher_Behavior_Thresd"]["ImageSize"], 640)
        self.assertEqual(config["Teacher_Behavior_Thresd"]["sit"], 0.4)
        self.assertEqual(config["Teacher_Behavior_Thresd"]["stand"], 0.4)
        self.assertEqual(config["Teacher_Behavior_Thresd"]["bbwriting"], 0.25)
        self.assertEqual(config["Teacher_Behavior_Thresd"]["teach"], 0.25)
        self.assertTrue(config["Teacher_Behavior_Thresd"]["KeepOnlyMainSubject"])
        self.assertEqual(config["Teacher_Behavior_Thresd"]["SubjectClusterIoU"], 0.45)
        self.assertEqual(config["Teacher_Behavior_Thresd"]["MainSubjectStrategy"], "posture_confidence")
        self.assertEqual(config["Teacher_Behavior_Thresd"]["PostureConflictRatio"], 0.10)
        self.assertEqual(config["Teacher_Behavior_Thresd"]["PostureConflictDefault"], "stand")
        self.assertTrue(config["Teacher_Behavior_Thresd"]["ForcePostureWhenMissing"])

    def test_loads_toml_example_file(self):
        config_path = Path(__file__).resolve().parents[1] / "config.toml.example"

        config = load_config(str(config_path))

        self.assertIn("ModelProtection", config)
        self.assertFalse(config["ModelProtection"]["Enabled"])


if __name__ == "__main__":
    unittest.main()
