import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import PROJECT_ROOT, Settings


class ModelPathConfigTests(unittest.TestCase):
    def test_v2_model_paths_resolve_from_config(self):
        settings = Settings()

        self.assertEqual(
            Path(settings.vad_model_dir),
            PROJECT_ROOT / "model" / "speech_fsmn_vad_zh-cn-16k-common-pytorch",
        )
        self.assertEqual(
            Path(settings.correction_model_dir),
            PROJECT_ROOT
            / "model"
            / "speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
        )
        self.assertEqual(
            Path(settings.sensevoice_model_dir),
            PROJECT_ROOT / "model" / "SenseVoiceSmall",
        )

    def test_legacy_environment_variables_do_not_override_config(self):
        with patch.dict(
            os.environ,
            {
                "ASR_ONLINE_VAD_MODEL_DIR": "/tmp/legacy-vad",
                "ASR_ONLINE_CORRECTION_MODEL_DIR": "/tmp/legacy-correction",
            },
        ):
            settings = Settings()

            self.assertEqual(
                Path(settings.vad_model_dir),
                PROJECT_ROOT / "model" / "speech_fsmn_vad_zh-cn-16k-common-pytorch",
            )
            self.assertEqual(
                Path(settings.correction_model_dir),
                PROJECT_ROOT
                / "model"
                / "speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
            )

    def test_vad_end_silence_is_configurable_and_must_be_positive(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text("vad_max_end_silence_ms = 1200\n", encoding="utf-8")
            settings = Settings(config_path=str(config_path))
            self.assertEqual(settings.vad_max_end_silence_ms, 1200)

            config_path.write_text("vad_max_end_silence_ms = 0\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                _ = settings.vad_max_end_silence_ms

    def test_vad_merge_gap_is_configurable_and_must_be_positive(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text("vad_merge_max_gap_ms = 900\n", encoding="utf-8")
            settings = Settings(config_path=str(config_path))
            self.assertEqual(settings.vad_merge_max_gap_ms, 900)

            config_path.write_text("vad_merge_max_gap_ms = 0\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                _ = settings.vad_merge_max_gap_ms


if __name__ == "__main__":
    unittest.main()
