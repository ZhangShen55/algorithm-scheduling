import unittest
from unittest.mock import patch


class GpuRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        import app.core.models as models

        self.models = models
        self.config = {"device": "cuda:0", "ngpu": 1}
        models._model_online = None
        models._punct_pipeline = None

    def _settings_config_patch(self):
        return patch.object(
            type(self.models.settings),
            "_cfg",
            new_callable=lambda: property(lambda _: self.config),
        )

    async def test_required_gpu_failure_precedes_funasr_and_punctuation_constructors(self) -> None:
        with (
            patch.dict("os.environ", {"REQUIRE_GPU": "true"}, clear=False),
            self._settings_config_patch(),
            patch.object(self.models.torch.cuda, "is_available", return_value=False),
            patch.object(self.models, "AutoModel") as auto_model,
            patch.object(self.models, "pipeline") as punctuation_pipeline,
            self.assertRaisesRegex(RuntimeError, "要求使用 GPU.*CUDA 不可用"),
        ):
            await self.models.load_models_if_needed()

        auto_model.assert_not_called()
        punctuation_pipeline.assert_not_called()

    def test_required_gpu_rejects_out_of_range_index(self) -> None:
        self.config["device"] = "cuda:1"

        with (
            patch.dict("os.environ", {"REQUIRE_GPU": "true"}, clear=False),
            self._settings_config_patch(),
            patch.object(self.models.torch.cuda, "is_available", return_value=True),
            patch.object(self.models.torch.cuda, "device_count", return_value=1),
            self.assertRaisesRegex(RuntimeError, "cuda:1.*索引越界"),
        ):
            self.models.resolve_runtime_device()

    def test_required_gpu_rejects_inconsistent_ngpu(self) -> None:
        self.config["ngpu"] = 0

        with (
            patch.dict("os.environ", {"REQUIRE_GPU": "true"}, clear=False),
            self._settings_config_patch(),
            patch.object(self.models.torch.cuda, "is_available", return_value=True),
            patch.object(self.models.torch.cuda, "device_count", return_value=1),
            self.assertRaisesRegex(RuntimeError, "ngpu 必须为 1"),
        ):
            self.models.resolve_runtime_device()

    def test_local_cpu_mode_is_preserved_without_deployment_switch(self) -> None:
        self.config.update(device="cpu", ngpu=0)

        with (
            patch.dict("os.environ", {}, clear=True),
            self._settings_config_patch(),
        ):
            resolved = self.models.resolve_runtime_device()

        self.assertEqual(str(resolved), "cpu")


if __name__ == "__main__":
    unittest.main()
