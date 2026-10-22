import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import PROJECT_ROOT, Settings
from app.core import model_assets


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

    def test_progressive_correction_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text("", encoding="utf-8")
            settings = Settings(config_path=str(config_path))

            self.assertTrue(settings.progressive_correction_enabled)
            self.assertEqual(settings.progressive_correction_chars, 8)
            self.assertEqual(settings.progressive_correction_audio_ms, 4800)
            self.assertEqual(settings.progressive_correction_cooldown_ms, 2000)
            self.assertEqual(settings.sensevoice_correction_workers, 1)
            self.assertEqual(settings.correction_queue_capacity, 16)
            self.assertEqual(settings.correction_task_timeout_ms, 5000)
            self.assertEqual(settings.sensevoice_correction_device, "cuda:0")
            self.assertEqual(settings.v4_final_budget_ms, 100)
            self.assertEqual(settings.v4_max_segment_seconds, 20)
            self.assertEqual(settings.v4_partial_punctuation_chars, 8)
            self.assertEqual(settings.v4_partial_punctuation_interval_ms, 2000)
            self.assertEqual(settings.v4_correction_queue_capacity, 8)
            self.assertTrue(settings.v4_itn_enabled)
            self.assertEqual(settings.v4_itn_failure_mode, "passthrough")
            self.assertTrue(settings.v4_itn_remove_interjections)
            self.assertTrue(settings.v4_itn_enable_standalone_number)
            self.assertFalse(settings.v4_itn_enable_0_to_9)
            self.assertFalse(settings.v4_itn_enable_million)

    def test_progressive_correction_configuration_is_validated(self):
        invalid_configs = (
            "progressive_correction_enabled = 1\n",
            "progressive_correction_chars = 0\n",
            "progressive_correction_audio_ms = 0\n",
            "progressive_correction_cooldown_ms = 0\n",
            "sensevoice_correction_workers = 0\n",
            "sensevoice_correction_workers = 65\n",
            "sensevoice_correction_workers = 2\ncorrection_queue_capacity = 1\n",
            "correction_task_timeout_ms = 0\n",
        )
        properties = (
            "progressive_correction_enabled",
            "progressive_correction_chars",
            "progressive_correction_audio_ms",
            "progressive_correction_cooldown_ms",
            "sensevoice_correction_workers",
            "sensevoice_correction_workers",
            "correction_queue_capacity",
            "correction_task_timeout_ms",
        )
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            for content, property_name in zip(invalid_configs, properties):
                config_path.write_text(content, encoding="utf-8")
                settings = Settings(config_path=str(config_path))
                with self.subTest(content=content), self.assertRaises(ValueError):
                    getattr(settings, property_name)

    def test_v4_configuration_is_validated(self):
        invalid_configs = (
            "v4_final_budget_ms = 0\n",
            "v4_final_budget_ms = 101\n",
            "v4_max_segment_seconds = 0\n",
            "v4_max_segment_seconds = 121\n",
            "v4_partial_punctuation_chars = 0\n",
            "v4_partial_punctuation_interval_ms = 0\n",
            "v4_correction_queue_capacity = 0\n",
            "v4_correction_workers = 9\n",
            'sensevoice_correction_device = ""\n',
            'v4_itn_enabled = 1\n',
            'v4_itn_failure_mode = "raise"\n',
        )
        properties = (
            "v4_final_budget_ms",
            "v4_final_budget_ms",
            "v4_max_segment_seconds",
            "v4_max_segment_seconds",
            "v4_partial_punctuation_chars",
            "v4_partial_punctuation_interval_ms",
            "v4_correction_queue_capacity",
            "v4_correction_workers",
            "sensevoice_correction_device",
            "v4_itn_enabled",
            "v4_itn_failure_mode",
        )
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            for content, property_name in zip(invalid_configs, properties):
                config_path.write_text(content, encoding="utf-8")
                settings = Settings(config_path=str(config_path))
                with self.subTest(content=content), self.assertRaises(ValueError):
                    getattr(settings, property_name)

    def test_sensevoice_device_can_be_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(
                'device = "cuda:0"\nngpu = 1\nsensevoice_correction_device = "cuda:1"\n',
                encoding="utf-8",
            )
            settings = Settings(config_path=str(config_path))
            self.assertEqual(settings.device, "cuda:0")
            self.assertEqual(settings.sensevoice_correction_device, "cuda:1")

    def test_prepared_model_directories_are_cleaned_by_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "encrypted-model"
            source.mkdir()
            (source / "model.pt.enc").write_bytes(b"encrypted")
            with patch.object(model_assets, "_xor_model_file") as decrypt:
                runtime = Path(model_assets.prepare_decrypted_model_dir(str(source)))

            self.assertTrue(runtime.exists())
            model_assets.cleanup_prepared_model_dirs()
            self.assertFalse(runtime.exists())
            decrypt.assert_called_once()

    def test_qwen_stream_configuration_is_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(
                """
qwen_stream_enabled = true
qwen_stream_only_mode = true
qwen_stream_base_url = "http://127.0.0.1:8826"
qwen_stream_base_urls = [
  "http://127.0.0.1:8826",
  "http://127.0.0.1:8827",
]
qwen_stream_chunk_seconds = 0.48
qwen_stream_connect_timeout_ms = 5000
qwen_stream_chunk_timeout_ms = 15000
qwen_stream_silence_rms = 0.004
qwen_stream_silence_chunks = 3
qwen_stream_max_segment_seconds = 20
qwen_stream_max_sessions = 2
qwen_stream_backend_capacity = 1
""",
                encoding="utf-8",
            )
            settings = Settings(config_path=str(config_path))
            self.assertTrue(settings.qwen_stream_enabled)
            self.assertTrue(settings.qwen_stream_only_mode)
            self.assertEqual(settings.qwen_stream_base_url, "http://127.0.0.1:8826")
            self.assertEqual(
                settings.qwen_stream_base_urls,
                ("http://127.0.0.1:8826", "http://127.0.0.1:8827"),
            )
            self.assertEqual(settings.qwen_stream_backend_capacity, 1)
            self.assertEqual(settings.qwen_stream_chunk_seconds, 0.48)
            self.assertEqual(settings.qwen_stream_silence_chunks, 3)


if __name__ == "__main__":
    unittest.main()
