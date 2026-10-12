import unittest
from unittest.mock import patch


class GpuRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        import app.core.models as models

        self.models = models
        self.config = {"device": "cuda:0", "ngpu": 1}
        models._model_online = None
        models._punct_pipeline = None
        models._model_vad = None
        models._model_correction = None
        models._model_sensevoice = None
        models._sensevoice_models = []
        models._correction_scheduler = None

    async def asyncTearDown(self) -> None:
        if self.models._correction_scheduler is not None:
            await self.models.shutdown_models()

    def _settings_config_patch(self):
        return patch.object(
            type(self.models.settings),
            "_cfg",
            new_callable=lambda: property(lambda _: self.config),
        )

    async def test_required_gpu_failure_precedes_funasr_and_punctuation_constructors(self) -> None:
        with (
            patch.object(self.models, "require_gpu_enabled", return_value=True),
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
            patch.object(self.models, "require_gpu_enabled", return_value=True),
            self._settings_config_patch(),
            patch.object(self.models.torch.cuda, "is_available", return_value=True),
            patch.object(self.models.torch.cuda, "device_count", return_value=1),
            self.assertRaisesRegex(RuntimeError, "cuda:1.*索引越界"),
        ):
            self.models.resolve_runtime_device()

    def test_required_gpu_rejects_inconsistent_ngpu(self) -> None:
        self.config["ngpu"] = 0

        with (
            patch.object(self.models, "require_gpu_enabled", return_value=True),
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
            patch.object(self.models, "require_gpu_enabled", return_value=False),
            self._settings_config_patch(),
        ):
            resolved = self.models.resolve_runtime_device()

        self.assertEqual(str(resolved), "cpu")

    def test_sensevoice_can_use_an_independent_gpu(self) -> None:
        self.config.update(
            device="cuda:0",
            ngpu=1,
            sensevoice_correction_device="cuda:1",
        )

        with (
            patch.object(self.models, "require_gpu_enabled", return_value=True),
            self._settings_config_patch(),
            patch.object(self.models.torch.cuda, "is_available", return_value=True),
            patch.object(self.models.torch.cuda, "device_count", return_value=3),
        ):
            resolved = self.models.resolve_sensevoice_device()

        self.assertEqual(str(resolved), "cuda:1")

    def test_required_gpu_rejects_cpu_sensevoice_device(self) -> None:
        self.config.update(sensevoice_correction_device="cpu")

        with (
            patch.object(self.models, "require_gpu_enabled", return_value=True),
            self._settings_config_patch(),
            self.assertRaisesRegex(RuntimeError, "SenseVoice 纠正配置不是 cuda"),
        ):
            self.models.resolve_sensevoice_device()

    async def test_each_correction_worker_loads_an_independent_model(self) -> None:
        self.config.update(
            device="cpu",
            ngpu=0,
            sensevoice_correction_workers=2,
            correction_queue_capacity=4,
            correction_task_timeout_ms=1000,
            model_paths={"sensevoice_model_dir": "model/SenseVoiceSmall"},
        )
        self.models._model_online = object()
        self.models._punct_pipeline = object()

        with (
            patch.object(self.models, "require_gpu_enabled", return_value=False),
            self._settings_config_patch(),
            patch.object(
                self.models, "prepare_decrypted_model_dir", return_value="/model/sensevoice"
            ),
            patch.object(self.models, "AutoModel", side_effect=[object(), object()]) as auto_model,
        ):
            await self.models.load_models_if_needed()

        self.assertEqual(auto_model.call_count, 2)
        self.assertEqual(self.models.get_correction_scheduler().worker_count, 2)
        self.assertIsNot(
            self.models._sensevoice_models[0], self.models._sensevoice_models[1]
        )

    async def test_correction_models_load_on_the_configured_device(self) -> None:
        self.config.update(
            device="cuda:0",
            ngpu=1,
            sensevoice_correction_device="cuda:1",
            sensevoice_correction_workers=1,
            correction_queue_capacity=4,
            correction_task_timeout_ms=1000,
            model_paths={"sensevoice_model_dir": "model/SenseVoiceSmall"},
        )
        self.models._model_online = object()
        self.models._punct_pipeline = object()

        with (
            patch.object(self.models, "require_gpu_enabled", return_value=True),
            self._settings_config_patch(),
            patch.object(self.models.torch.cuda, "is_available", return_value=True),
            patch.object(self.models.torch.cuda, "device_count", return_value=3),
            patch.object(
                self.models, "prepare_decrypted_model_dir", return_value="/model/sensevoice"
            ),
            patch.object(self.models, "AutoModel", return_value=object()) as auto_model,
        ):
            await self.models.load_models_if_needed()

        self.assertEqual(auto_model.call_args.kwargs["device"], "cuda:1")
        self.assertEqual(auto_model.call_args.kwargs["ngpu"], 1)

    async def test_correction_worker_load_failure_does_not_silently_reduce_workers(self) -> None:
        self.config.update(
            device="cpu",
            ngpu=0,
            sensevoice_correction_workers=2,
            correction_queue_capacity=4,
            correction_task_timeout_ms=1000,
            model_paths={"sensevoice_model_dir": "model/SenseVoiceSmall"},
        )
        self.models._model_online = object()
        self.models._punct_pipeline = object()

        with (
            patch.object(self.models, "require_gpu_enabled", return_value=False),
            self._settings_config_patch(),
            patch.object(
                self.models, "prepare_decrypted_model_dir", return_value="/model/sensevoice"
            ),
            patch.object(
                self.models,
                "AutoModel",
                side_effect=[object(), RuntimeError("CUDA out of memory")],
            ),
            self.assertRaisesRegex(RuntimeError, "out of memory"),
        ):
            await self.models.load_models_if_needed()

        self.assertIsNone(self.models.get_correction_scheduler())
        self.assertEqual(self.models._sensevoice_models, [])

    async def test_partial_startup_failure_releases_already_loaded_models(self) -> None:
        self.config.update(device="cpu", ngpu=0, model_paths={})
        online_model = object()

        with (
            patch.object(self.models, "require_gpu_enabled", return_value=False),
            self._settings_config_patch(),
            patch.object(
                self.models, "prepare_decrypted_model_dir", return_value="/model"
            ),
            patch.object(self.models, "AutoModel", return_value=online_model),
            patch.object(
                self.models,
                "pipeline",
                side_effect=RuntimeError("punctuation load failed"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "punctuation load failed"):
                await self.models.load_models_if_needed()

        self.assertIsNone(self.models.get_online_model())
        self.assertIsNone(self.models.get_punct_pipeline())
        self.assertIsNone(self.models.get_vad_model())
        self.assertIsNone(self.models.get_correction_model())


if __name__ == "__main__":
    unittest.main()
