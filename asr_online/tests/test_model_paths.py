import os
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


if __name__ == "__main__":
    unittest.main()
