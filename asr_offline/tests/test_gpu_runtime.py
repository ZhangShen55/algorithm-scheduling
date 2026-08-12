import unittest
from unittest.mock import patch

import ctranslate2


class GpuRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        import app.core.models as models

        self.models = models
        self.original_cfg = dict(models.settings._cfg)
        models._model_asr = None
        models._model_emotion = None
        models._model_whisper = None

    def tearDown(self) -> None:
        self.models.settings._cfg.clear()
        self.models.settings._cfg.update(self.original_cfg)

    def _configure_gpu(self, *, device: str = "cuda:0", ngpu: int = 1) -> None:
        self.models.settings._cfg["device"] = device
        self.models.settings._cfg["ngpu"] = ngpu

    def test_required_gpu_rejects_cpu_configuration(self) -> None:
        self._configure_gpu(device="cpu", ngpu=0)

        with patch.dict("os.environ", {"REQUIRE_GPU": "true"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "部署要求使用 GPU.*cuda:<index>"):
                self.models.resolve_runtime_device()

    def test_required_gpu_rejects_unavailable_cuda(self) -> None:
        self._configure_gpu()

        with (
            patch.dict("os.environ", {"REQUIRE_GPU": "true"}, clear=False),
            patch.object(self.models.torch.cuda, "is_available", return_value=False),
            self.assertRaisesRegex(RuntimeError, "要求使用 GPU.*CUDA 不可用"),
        ):
            self.models.resolve_runtime_device()

    def test_required_gpu_rejects_out_of_range_index(self) -> None:
        self._configure_gpu(device="cuda:1")

        with (
            patch.dict("os.environ", {"REQUIRE_GPU": "true"}, clear=False),
            patch.object(self.models.torch.cuda, "is_available", return_value=True),
            patch.object(self.models.torch.cuda, "device_count", return_value=1),
            self.assertRaisesRegex(RuntimeError, "cuda:1.*索引越界"),
        ):
            self.models.resolve_runtime_device()

    def test_required_gpu_rejects_inconsistent_ngpu(self) -> None:
        self._configure_gpu(ngpu=0)

        with (
            patch.dict("os.environ", {"REQUIRE_GPU": "true"}, clear=False),
            patch.object(self.models.torch.cuda, "is_available", return_value=True),
            patch.object(self.models.torch.cuda, "device_count", return_value=1),
            self.assertRaisesRegex(RuntimeError, "ngpu 必须为 1"),
        ):
            self.models.resolve_runtime_device()

    async def test_torch_gpu_failure_precedes_all_enabled_model_constructors(self) -> None:
        self._configure_gpu()
        self.models.settings._cfg["features"] = {
            "open_spk": True,
            "open_emotion": True,
            "open_mul_lang": True,
        }

        with (
            patch.dict("os.environ", {"REQUIRE_GPU": "true"}, clear=False),
            patch.object(self.models.torch.cuda, "is_available", return_value=False),
            patch.object(self.models, "AutoModel") as auto_model,
            patch.object(self.models, "WhisperModel") as whisper_model,
            self.assertRaisesRegex(RuntimeError, "CUDA 不可用"),
        ):
            await self.models.load_models_if_needed()

        auto_model.assert_not_called()
        whisper_model.assert_not_called()

    async def test_ctranslate2_gpu_failure_precedes_whisper_constructor(self) -> None:
        self._configure_gpu()
        self.models.settings._cfg["features"] = {
            "open_spk": False,
            "open_emotion": False,
            "open_mul_lang": True,
        }

        with (
            patch.dict("os.environ", {"REQUIRE_GPU": "true"}, clear=False),
            patch.object(self.models.torch.cuda, "is_available", return_value=True),
            patch.object(self.models.torch.cuda, "device_count", return_value=1),
            patch.object(ctranslate2, "get_cuda_device_count", return_value=0),
            patch.object(self.models, "WhisperModel") as whisper_model,
            self.assertRaisesRegex(RuntimeError, "CTranslate2.*CUDA"),
        ):
            await self.models.load_models_if_needed()

        whisper_model.assert_not_called()

    async def test_startup_loads_retained_asr_emotion_and_whisper_models(self) -> None:
        self._configure_gpu(device="cpu", ngpu=0)
        self.models.settings._cfg["features"] = {
            "open_spk": True,
            "open_emotion": True,
            "open_mul_lang": True,
        }

        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(self.models, "AutoModel") as auto_model,
            patch.object(self.models, "WhisperModel") as whisper_model,
        ):
            await self.models.load_models_if_needed()

        self.assertEqual(auto_model.call_count, 2)
        paraformer_call, emotion_call = auto_model.call_args_list
        self.assertEqual(
            paraformer_call.kwargs["model"],
            self.models.settings.asr_model_dir,
        )
        self.assertEqual(
            paraformer_call.kwargs["spk_model"],
            self.models.settings.spk_model_dir,
        )
        self.assertEqual(
            emotion_call.kwargs["model"],
            self.models.settings.emotion_model_dir,
        )
        whisper_model.assert_called_once_with(
            self.models.settings.whisper_model_dir,
            compute_type=self.models.settings.compute_type,
            device="cpu",
            device_index=0,
        )

    def test_local_cpu_mode_is_preserved_without_deployment_switch(self) -> None:
        self._configure_gpu(device="cpu", ngpu=0)

        with patch.dict("os.environ", {}, clear=True):
            resolved = self.models.resolve_runtime_device()

        self.assertEqual(str(resolved), "cpu")


if __name__ == "__main__":
    unittest.main()
