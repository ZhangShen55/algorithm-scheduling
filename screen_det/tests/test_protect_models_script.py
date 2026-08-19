from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "docker" / "protect_models.py"
SCRIPT_AVAILABLE = SCRIPT_PATH.exists()

if SCRIPT_AVAILABLE:
    spec = importlib.util.spec_from_file_location("protect_models", SCRIPT_PATH)
    assert spec and spec.loader
    protect_models_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(protect_models_module)


class ProtectModelsScriptAvailabilityTests(unittest.TestCase):
    def test_protect_models_script_exists(self) -> None:
        self.assertTrue(SCRIPT_AVAILABLE)


@unittest.skipUnless(SCRIPT_AVAILABLE, "protect_models.py not implemented")
class ProtectModelsScriptTests(unittest.TestCase):
    def test_default_model_names_are_screen_and_occlusion(self) -> None:
        self.assertEqual(
            ["occlusion.pt", "screen.pt"],
            protect_models_module.DEFAULT_MODEL_NAMES,
        )

    def test_protect_models_creates_both_encrypted_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "model"
            target = root / "encrypted"
            source.mkdir()
            (source / "screen.pt").write_bytes(b"screen")
            (source / "occlusion.pt").write_bytes(b"occlusion")
            key = protect_models_module.generate_key()

            outputs = protect_models_module.protect_models(source, target, key)

            self.assertEqual(
                [target / "occlusion.pt.enc", target / "screen.pt.enc"],
                outputs,
            )
            self.assertTrue(all(path.is_file() for path in outputs))

    def test_model_materials_are_ignored_by_git_and_docker(self) -> None:
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

        self.assertIn("docker/models-encrypted/*", gitignore)
        self.assertIn("docker/models-encrypted/", dockerignore)
        self.assertNotIn("model/", dockerignore)
