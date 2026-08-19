from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app.services.screen_detector as screen_detector
from app.core.config import StartupConfigChangedError, get_settings, reload_settings
from app.main import app
from fastapi.testclient import TestClient


class UnifiedYoloConfigTests(unittest.TestCase):
    def test_only_one_yolo_device_configuration_exists(self) -> None:
        settings = get_settings()

        self.assertTrue(hasattr(settings, "yolo"))
        self.assertEqual("cpu", settings.yolo.device)
        self.assertFalse(hasattr(settings, "gpu"))
        self.assertFalse(hasattr(settings.occlusion_detection, "yolo_device"))
        self.assertFalse(hasattr(settings.aggregate_detection, "device"))

    def test_device_resolver_exists(self) -> None:
        self.assertTrue(hasattr(screen_detector, "resolve_yolo_device"))

    def test_reload_rejects_yolo_device_change(self) -> None:
        current = get_settings()
        candidate = replace(current, yolo=replace(current.yolo, device="cuda:0"))

        with patch("app.core.config._load_settings", return_value=candidate):
            with self.assertRaises(StartupConfigChangedError):
                reload_settings()

        self.assertEqual(current, get_settings())

    def test_config_reload_returns_409_for_startup_change(self) -> None:
        with patch(
            "app.api.v1.config.reload_settings",
            side_effect=StartupConfigChangedError("启动级配置已变化"),
        ):
            response = TestClient(app).post("/config/reload")

        self.assertEqual(409, response.status_code)
        self.assertEqual(409, response.json()["code"])


@unittest.skipUnless(
    hasattr(screen_detector, "resolve_yolo_device"),
    "unified YOLO device resolver not implemented",
)
class YoloDeviceResolutionTests(unittest.TestCase):
    def test_cpu_is_always_valid(self) -> None:
        self.assertEqual("cpu", screen_detector.resolve_yolo_device("cpu"))

    def test_cuda_device_is_kept_when_available(self) -> None:
        with patch("torch.cuda.is_available", return_value=True), patch(
            "torch.cuda.device_count", return_value=2
        ):
            self.assertEqual("cuda:1", screen_detector.resolve_yolo_device("cuda:1"))

    def test_cuda_unavailable_raises_instead_of_falling_back(self) -> None:
        with patch("torch.cuda.is_available", return_value=False):
            with self.assertRaises(RuntimeError):
                screen_detector.resolve_yolo_device("cuda:0")

    def test_cuda_index_out_of_range_raises(self) -> None:
        with patch("torch.cuda.is_available", return_value=True), patch(
            "torch.cuda.device_count", return_value=1
        ):
            with self.assertRaises(RuntimeError):
                screen_detector.resolve_yolo_device("cuda:1")

    def test_invalid_device_format_raises(self) -> None:
        with self.assertRaises(ValueError):
            screen_detector.resolve_yolo_device("gpu")

    def test_required_gpu_rejects_cpu(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "部署要求使用 GPU.*cuda:<index>"):
            screen_detector.resolve_yolo_device("cpu", require_gpu=True)

    def test_required_gpu_rejects_mps(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "部署要求使用 GPU.*cuda:<index>"):
            screen_detector.resolve_yolo_device("mps", require_gpu=True)


class YoloHolderFailFastTests(unittest.TestCase):
    def test_screen_holder_does_not_materialize_or_construct_yolo_when_resolver_fails(
        self,
    ) -> None:
        holder = screen_detector.ScreenModelHolder()
        settings = SimpleNamespace(
            screen_detection=SimpleNamespace(weights_path="model/screen.pt"),
            yolo=SimpleNamespace(device="cpu"),
            model_protection=SimpleNamespace(),
            operator_deployment=SimpleNamespace(
                runtime=SimpleNamespace(require_gpu=True)
            ),
        )
        yolo = Mock()

        with (
            patch.object(screen_detector, "get_settings", return_value=settings),
            patch.object(
                screen_detector,
                "resolve_yolo_device",
                side_effect=RuntimeError("GPU required"),
            ),
            patch.object(screen_detector, "materialize_model_path") as materialize,
            patch.dict(
                "sys.modules",
                {"ultralytics": SimpleNamespace(YOLO=yolo)},
            ),
            self.assertRaisesRegex(RuntimeError, "GPU required"),
        ):
            holder.load()

        materialize.assert_not_called()
        yolo.assert_not_called()

    def test_occlusion_holder_fails_before_model_materialization(self) -> None:
        import app.services.occlusion_detector as occlusion_detector

        holder = occlusion_detector.YoloOcclusionModelHolder()
        settings = SimpleNamespace(
            occlusion_detection=SimpleNamespace(
                yolo_seg_weights_path="model/occlusion.pt"
            ),
            yolo=SimpleNamespace(device="cpu"),
            model_protection=SimpleNamespace(),
            operator_deployment=SimpleNamespace(
                runtime=SimpleNamespace(require_gpu=True)
            ),
        )
        yolo = Mock()

        with (
            patch.object(occlusion_detector, "get_settings", return_value=settings),
            patch.object(
                screen_detector,
                "resolve_yolo_device",
                side_effect=RuntimeError("GPU required"),
            ),
            patch.object(occlusion_detector, "materialize_model_path") as materialize,
            patch.dict("sys.modules", {"ultralytics": SimpleNamespace(YOLO=yolo)}),
            self.assertRaisesRegex(RuntimeError, "GPU required"),
        ):
            holder.load()

        materialize.assert_not_called()
        yolo.assert_not_called()
