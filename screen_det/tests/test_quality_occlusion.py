from __future__ import annotations

import base64
import builtins
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import get_settings
from app.core.model_protection import ModelProtectionConfig
import app.services.occlusion_detector as occlusion_detector
from app.services.occlusion_detector import detect_occlusion_from_base64, reset_occlusion_yolo_model_cache
from app.services.quality_abnormal_detector import (
    ABNORMAL_BLUR,
    ABNORMAL_COLOR_CAST,
    ABNORMAL_GLITCH,
    ABNORMAL_SNOW_NOISE,
    detect_quality_abnormal_from_base64,
)


ROOT = Path(__file__).resolve().parents[1]
OPENCV_OCCLUSION_CONFIG_KEYS = {
    "backend",
    "occlusion_area_threshold",
    "large_area_min_ratio",
    "high_saturation_threshold",
    "red_dominance_threshold",
    "dark_line_threshold",
    "wire_min_line_length_ratio",
}


def image_b64(path: str) -> str:
    return base64.b64encode((ROOT / path).read_bytes()).decode()


class QualityAbnormalDetectionTests(unittest.TestCase):
    def test_detects_color_cast(self) -> None:
        result = detect_quality_abnormal_from_base64(
            image_b64("test/图像检测/画面异常/偏色/偏色1.png")
        )
        self.assertTrue(result.is_abnormal)
        self.assertIn(ABNORMAL_COLOR_CAST, result.abnormal_types)

    def test_detects_snow_noise(self) -> None:
        result = detect_quality_abnormal_from_base64(
            image_b64("test/图像检测/画面异常/雪花噪点/雪花噪点1.png")
        )
        self.assertTrue(result.is_abnormal)
        self.assertIn(ABNORMAL_SNOW_NOISE, result.abnormal_types)

    def test_detects_blur(self) -> None:
        result = detect_quality_abnormal_from_base64(
            image_b64("test/图像检测/画面异常/虚焦/重度虚焦/教室监控图重度虚焦处理.png")
        )
        self.assertTrue(result.is_abnormal)
        self.assertIn(ABNORMAL_BLUR, result.abnormal_types)

    def test_detects_glitch(self) -> None:
        result = detect_quality_abnormal_from_base64(
            image_b64("test/图像检测/画面异常/花屏/ChatGPT_Image_2026年7月13日_14_45_52_(1).png")
        )
        self.assertTrue(result.is_abnormal)
        self.assertIn(ABNORMAL_GLITCH, result.abnormal_types)

    def test_normal_image_has_no_quality_abnormal(self) -> None:
        result = detect_quality_abnormal_from_base64(
            image_b64("test/ok_img/snapshot_计算机科学导论.png")
        )
        self.assertFalse(result.is_abnormal)
        self.assertEqual([], result.abnormal_types)
        self.assertEqual([], result.results)


class OcclusionDetectionTests(unittest.TestCase):
    def test_detects_near_lens_occlusion(self) -> None:
        result = detect_occlusion_from_base64(
            image_b64("test/图像检测/遮挡/横幅遮挡.png")
        )
        self.assertTrue(result.is_occluded)
        self.assertGreater(result.occlusion_area_ratio, 0.05)

    def test_normal_image_has_no_occlusion(self) -> None:
        result = detect_occlusion_from_base64(
            image_b64("test/ok_img/snapshot_计算机科学导论.png")
        )
        self.assertFalse(result.is_occluded)

    def test_occlusion_config_exposes_only_yolo_fields(self) -> None:
        config = get_settings().occlusion_detection
        for key in OPENCV_OCCLUSION_CONFIG_KEYS:
            self.assertFalse(hasattr(config, key), f"OpenCV occlusion config leaked: {key}")

    def test_occlusion_config_toml_has_no_opencv_keys(self) -> None:
        text = (ROOT / "config.toml").read_text(encoding="utf-8")
        occlusion_section = text.split("[occlusion_detection]", 1)[1]
        for key in OPENCV_OCCLUSION_CONFIG_KEYS:
            self.assertNotIn(f"{key} =", occlusion_section)

    def test_occlusion_detector_has_no_opencv_backend(self) -> None:
        self.assertFalse(hasattr(occlusion_detector, "_detect_opencv"))
        self.assertFalse(hasattr(occlusion_detector, "_large_area_mask"))
        self.assertFalse(hasattr(occlusion_detector, "_wire_mask"))


class FakeBoxes:
    def __init__(self, confs: list[float]) -> None:
        self.conf = np.array(confs, dtype=np.float32)

    def __len__(self) -> int:
        return int(self.conf.shape[0])


class FakeYoloResult:
    def __init__(self, masks: list[np.ndarray], confs: list[float]) -> None:
        self.masks = SimpleNamespace(data=np.stack(masks).astype(np.float32)) if masks else None
        self.boxes = FakeBoxes(confs)


class FakeYoloModel:
    def __init__(self, result: FakeYoloResult) -> None:
        self.result = result
        self.calls: list[dict] = []

    def predict(self, source, **kwargs):
        self.calls.append(kwargs)
        return [self.result]


class FakeYoloHolder:
    def __init__(self, model: FakeYoloModel) -> None:
        self.model = model
        self.device = "cpu"
        self.load_calls = 0

    def load(self) -> None:
        self.load_calls += 1


def yolo_settings() -> SimpleNamespace:
    return SimpleNamespace(
        runtime=SimpleNamespace(max_image_bytes=10 * 1024 * 1024),
        operator_deployment=SimpleNamespace(
            runtime=SimpleNamespace(require_gpu=False),
        ),
        yolo=SimpleNamespace(device="cpu"),
        model_protection=ModelProtectionConfig(enabled=False),
        occlusion_detection=SimpleNamespace(
            enabled=True,
            analyze_max_side=960,
            threshold=0.25,
            area_ratio=0.2,
            yolo_imgsz=960,
            yolo_retina_masks=True,
            yolo_seg_weights_path="model/occlusion.pt",
        ),
    )


def yolo_settings_with(**overrides) -> SimpleNamespace:
    settings = yolo_settings()
    for key, value in overrides.items():
        setattr(settings.occlusion_detection, key, value)
    return settings


class YoloOcclusionDetectionTests(unittest.TestCase):
    def test_yolo_no_mask_returns_not_occluded(self) -> None:
        model = FakeYoloModel(FakeYoloResult([], []))
        holder = FakeYoloHolder(model)

        with patch("app.services.occlusion_detector.get_settings", return_value=yolo_settings()), patch(
            "app.services.occlusion_detector._yolo_holder", holder, create=True
        ):
            result = detect_occlusion_from_base64(
                image_b64("test/ok_img/snapshot_计算机科学导论.png"),
                threshold=0.25,
                area_ratio=0.2,
            )

        self.assertFalse(result.is_occluded)
        self.assertEqual(0.0, result.occlusion_area_ratio)
        self.assertEqual(0.0, result.score)

    def test_yolo_mask_union_area_drives_occlusion_result(self) -> None:
        mask_a = np.zeros((10, 10), dtype=np.float32)
        mask_b = np.zeros((10, 10), dtype=np.float32)
        mask_a[:4, :4] = 1.0
        mask_b[2:6, 2:6] = 1.0
        model = FakeYoloModel(FakeYoloResult([mask_a, mask_b], [0.9, 0.8]))
        holder = FakeYoloHolder(model)

        with patch("app.services.occlusion_detector.get_settings", return_value=yolo_settings()), patch(
            "app.services.occlusion_detector._yolo_holder", holder, create=True
        ):
            try:
                result = detect_occlusion_from_base64(
                    image_b64("test/ok_img/snapshot_计算机科学导论.png"),
                    threshold=0.25,
                    area_ratio=0.2,
                )
            except Exception as exc:  # pragma: no cover - RED phase must show failure cleanly
                self.fail(f"YOLO occlusion detection raised unexpectedly: {exc}")

        self.assertTrue(result.is_occluded)
        self.assertAlmostEqual(0.28, result.occlusion_area_ratio, places=4)
        self.assertAlmostEqual(0.9, result.score, places=4)
        self.assertEqual(0.25, result.threshold)
        self.assertEqual(0.2, result.area_ratio)
        self.assertEqual(1, holder.load_calls)
        self.assertEqual(0.25, model.calls[0]["conf"])
        self.assertEqual(960, model.calls[0]["imgsz"])

    def test_yolo_low_confidence_mask_is_ignored(self) -> None:
        mask = np.ones((10, 10), dtype=np.float32)
        model = FakeYoloModel(FakeYoloResult([mask], [0.24]))
        holder = FakeYoloHolder(model)

        with patch("app.services.occlusion_detector.get_settings", return_value=yolo_settings()), patch(
            "app.services.occlusion_detector._yolo_holder", holder, create=True
        ):
            try:
                result = detect_occlusion_from_base64(
                    image_b64("test/ok_img/snapshot_计算机科学导论.png"),
                    threshold=0.25,
                    area_ratio=0.2,
                )
            except Exception as exc:  # pragma: no cover - RED phase must show failure cleanly
                self.fail(f"YOLO occlusion detection raised unexpectedly: {exc}")

        self.assertFalse(result.is_occluded)
        self.assertEqual(0.0, result.occlusion_area_ratio)
        self.assertEqual(0.0, result.score)
        self.assertEqual(0.25, result.threshold)
        self.assertEqual(0.2, result.area_ratio)

    def test_yolo_valid_mask_below_area_threshold_is_not_exposed(self) -> None:
        mask = np.zeros((10, 10), dtype=np.float32)
        mask[:4, :4] = 1.0
        model = FakeYoloModel(FakeYoloResult([mask], [0.91]))
        holder = FakeYoloHolder(model)

        with patch("app.services.occlusion_detector.get_settings", return_value=yolo_settings()), patch(
            "app.services.occlusion_detector._yolo_holder", holder, create=True
        ):
            result = detect_occlusion_from_base64(
                image_b64("test/ok_img/snapshot_计算机科学导论.png"),
                threshold=0.25,
                area_ratio=0.2,
            )

        self.assertFalse(result.is_occluded)
        self.assertEqual(0.0, result.occlusion_area_ratio)
        self.assertEqual(0.0, result.score)

    def test_yolo_missing_weights_error_contains_resolved_path(self) -> None:
        missing_path = ROOT / "model/not-exists-occlusion.pt"
        settings = yolo_settings_with(yolo_seg_weights_path=str(missing_path))

        with patch("app.services.occlusion_detector.get_settings", return_value=settings):
            with self.assertRaises(FileNotFoundError) as raised:
                detect_occlusion_from_base64(
                    image_b64("test/ok_img/snapshot_计算机科学导论.png"),
                    threshold=0.25,
                    area_ratio=0.2,
                )

        self.assertIn(str(missing_path), str(raised.exception))

    def test_yolo_missing_dependency_error_is_actionable(self) -> None:
        reset_occlusion_yolo_model_cache()
        settings = yolo_settings()
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "ultralytics":
                raise ModuleNotFoundError("No module named 'ultralytics'")
            return real_import(name, *args, **kwargs)

        with patch("app.services.occlusion_detector.get_settings", return_value=settings), patch(
            "builtins.__import__", side_effect=fake_import
        ):
            with self.assertRaises(RuntimeError) as raised:
                detect_occlusion_from_base64(
                    image_b64("test/ok_img/snapshot_计算机科学导论.png"),
                    threshold=0.25,
                    area_ratio=0.2,
                )

        message = str(raised.exception)
        self.assertIn("YOLO occlusion dependency missing", message)
        self.assertIn("ultralytics", message)
        reset_occlusion_yolo_model_cache()

class ApiRouteTests(unittest.TestCase):
    def test_quality_abnormal_endpoint(self) -> None:
        client = TestClient(app)
        response = client.post(
            "/detect_quality_abnormal",
            json={"image": image_b64("test/图像检测/画面异常/偏色/偏色1.png")},
        )
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertTrue(body["is_abnormal"])
        self.assertIn(ABNORMAL_COLOR_CAST, body["abnormal_types"])

    def test_occlusion_endpoint(self) -> None:
        client = TestClient(app)
        response = client.post(
            "/detect_occlusion",
            json={"image": image_b64("test/图像检测/遮挡/横幅遮挡.png")},
        )
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertTrue(body["is_occluded"])
        self.assertGreater(body["occlusion_area_ratio"], 0.05)

    def test_occlusion_endpoint_uses_request_threshold_overrides(self) -> None:
        captured: dict = {}

        def fake_detect(image: str, threshold: float | None = None, area_ratio: float | None = None):
            captured["threshold"] = threshold
            captured["area_ratio"] = area_ratio
            return SimpleNamespace(
                is_occluded=True,
                occlusion_area_ratio=0.2367,
                score=0.87,
                threshold=threshold,
                area_ratio=area_ratio,
                message="检测到镜头遮挡",
            )

        client = TestClient(app)
        with patch("app.api.v1.occlusion.detect_occlusion_from_base64", side_effect=fake_detect):
            response = client.post(
                "/detect_occlusion",
                json={
                    "image": image_b64("test/图像检测/遮挡/横幅遮挡.png"),
                    "threshold": 0.5,
                    "area_ratio": 0.15,
                },
            )

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual(0.5, captured["threshold"])
        self.assertEqual(0.15, captured["area_ratio"])
        self.assertEqual(0.5, body["threshold"])
        self.assertEqual(0.15, body["area_ratio"])

    def test_occlusion_endpoint_returns_default_thresholds(self) -> None:
        settings = get_settings()

        def fake_detect(image: str, threshold: float | None = None, area_ratio: float | None = None):
            return SimpleNamespace(
                is_occluded=False,
                occlusion_area_ratio=0.0,
                score=0.0,
                threshold=settings.occlusion_detection.threshold,
                area_ratio=settings.occlusion_detection.area_ratio,
                message="未检测到镜头遮挡",
            )

        client = TestClient(app)
        with patch("app.api.v1.occlusion.detect_occlusion_from_base64", side_effect=fake_detect):
            response = client.post(
                "/detect_occlusion",
                json={"image": image_b64("test/ok_img/snapshot_计算机科学导论.png")},
            )

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual(settings.occlusion_detection.threshold, body["threshold"])
        self.assertEqual(settings.occlusion_detection.area_ratio, body["area_ratio"])

    def test_occlusion_invalid_threshold_returns_400(self) -> None:
        client = TestClient(app)
        with patch("app.api.v1.occlusion.detect_occlusion_from_base64") as fake_detect:
            response = client.post(
                "/detect_occlusion",
                json={
                    "image": image_b64("test/ok_img/snapshot_计算机科学导论.png"),
                    "threshold": 1.5,
                },
            )

        self.assertEqual(400, response.status_code)
        self.assertEqual(400, response.json()["code"])
        fake_detect.assert_not_called()

    def test_occlusion_invalid_area_ratio_returns_400(self) -> None:
        client = TestClient(app)
        with patch("app.api.v1.occlusion.detect_occlusion_from_base64") as fake_detect:
            response = client.post(
                "/detect_occlusion",
                json={
                    "image": image_b64("test/ok_img/snapshot_计算机科学导论.png"),
                    "area_ratio": -0.1,
                },
            )

        self.assertEqual(400, response.status_code)
        self.assertEqual(400, response.json()["code"])
        fake_detect.assert_not_called()

    def test_quality_abnormal_missing_image_returns_400(self) -> None:
        client = TestClient(app)
        response = client.post("/detect_quality_abnormal", json={})
        self.assertEqual(400, response.status_code)
        self.assertEqual(400, response.json()["code"])

    def test_occlusion_missing_image_returns_400(self) -> None:
        client = TestClient(app)
        response = client.post("/detect_occlusion", json={})
        self.assertEqual(400, response.status_code)
        self.assertEqual(400, response.json()["code"])


if __name__ == "__main__":
    unittest.main()
