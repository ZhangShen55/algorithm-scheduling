from __future__ import annotations

import base64
import io
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from app.core.config import get_settings
from app.main import app


def tiny_image_b64() -> str:
    image = Image.new("RGB", (16, 12), color=(128, 128, 128))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def fake_tilt_part(is_tilted: bool = False):
    return SimpleNamespace(
        is_tilted=is_tilted,
        angle=2.3 if is_tilted else 0.3,
        message="检测完成",
    )


def fake_screen_item(label: int | None = 3):
    primary = (
        None
        if label is None
        else SimpleNamespace(label=label, confidence=0.91, box=[1.0, 2.0, 3.0, 4.0])
    )
    detections = [] if primary is None else [primary]
    return SimpleNamespace(index=0, cost_ms=5.0, primary=primary, detections=detections)


def fake_quality(is_abnormal: bool = False):
    items = (
        [SimpleNamespace(type=1, score=0.76, message="疑似虚焦")]
        if is_abnormal
        else []
    )
    return SimpleNamespace(
        is_abnormal=is_abnormal,
        abnormal_types=[1] if is_abnormal else [],
        results=items,
        message="检测到画面异常：虚焦" if is_abnormal else "未检测到画面异常",
    )


def fake_occlusion(is_occluded: bool = False):
    return SimpleNamespace(
        is_occluded=is_occluded,
        occlusion_area_ratio=0.23 if is_occluded else 0.0,
        score=0.87 if is_occluded else 0.0,
        threshold=0.25,
        area_ratio=0.2,
        message="检测到镜头遮挡" if is_occluded else "未检测到镜头遮挡",
    )


class AggregateSchemaTests(unittest.TestCase):
    def test_request_defaults_are_optional_except_image(self) -> None:
        from app.schemas.aggregate import AggregateDetectRequest

        body = AggregateDetectRequest(image=tiny_image_b64())

        self.assertIsNone(body.tilt_threshold)
        self.assertIsNone(body.screen_conf)
        self.assertIsNone(body.screen_iou)
        self.assertIsNone(body.occlusion_threshold)
        self.assertIsNone(body.occlusion_area_ratio)
        self.assertIsNone(body.include)

    def test_request_validates_threshold_ranges(self) -> None:
        from app.schemas.aggregate import AggregateDetectRequest

        with self.assertRaises(ValidationError):
            AggregateDetectRequest(image=tiny_image_b64(), screen_conf=1.1)
        with self.assertRaises(ValidationError):
            AggregateDetectRequest(image=tiny_image_b64(), screen_iou=-0.1)
        with self.assertRaises(ValidationError):
            AggregateDetectRequest(image=tiny_image_b64(), occlusion_threshold=2.0)
        with self.assertRaises(ValidationError):
            AggregateDetectRequest(image=tiny_image_b64(), occlusion_area_ratio=-0.2)
        with self.assertRaises(ValidationError):
            AggregateDetectRequest(image=tiny_image_b64(), tilt_threshold=-0.1)

    def test_request_validates_include_enum(self) -> None:
        from app.schemas.aggregate import AggregateDetectRequest

        body = AggregateDetectRequest(image=tiny_image_b64(), include=["tilt", "occlusion"])
        self.assertEqual(["tilt", "occlusion"], body.include)

        with self.assertRaises(ValidationError):
            AggregateDetectRequest(image=tiny_image_b64(), include=["tilt", "bad"])


class AggregateServiceTests(unittest.TestCase):
    def _patch_all_modules(
        self,
        *,
        tilt=None,
        screen=None,
        quality=None,
        occlusion=None,
    ):
        return (
            patch(
                "app.services.aggregate_detector.detect_image_tilt_from_array",
                return_value=tilt if tilt is not None else fake_tilt_part(False),
            ),
            patch(
                "app.services.aggregate_detector.detect_screen_from_array",
                return_value=(
                    [screen if screen is not None else fake_screen_item(3)],
                    0.25,
                    0.45,
                ),
            ),
            patch(
                "app.services.aggregate_detector.detect_quality_abnormal_from_array",
                return_value=quality if quality is not None else fake_quality(False),
            ),
            patch(
                "app.services.aggregate_detector.detect_occlusion_from_array",
                return_value=occlusion if occlusion is not None else fake_occlusion(False),
            ),
        )

    def test_run_detect_all_uses_config_defaults_and_returns_all_blocks(self) -> None:
        from app.services.aggregate_detector import run_detect_all

        patches = self._patch_all_modules(
            tilt=fake_tilt_part(True),
            screen=fake_screen_item(1),
            quality=fake_quality(True),
            occlusion=fake_occlusion(False),
        )
        with patches[0] as tilt_mock, patches[1] as screen_mock, patches[2], patches[3]:
            response = run_detect_all(tiny_image_b64(), "1753791280207")

        self.assertEqual(200, response.code)
        self.assertEqual(["tilt", "screen", "quality_abnormal", "occlusion"], response.executed_modules)
        self.assertEqual([], response.failed_modules)
        self.assertEqual(["tilt", "screen", "quality_abnormal"], response.problem_types)
        self.assertEqual(1.5, response.effective_params.tilt_threshold)
        self.assertEqual(0.25, response.effective_params.screen_conf)
        self.assertEqual(0.45, response.effective_params.screen_iou)
        self.assertEqual(0.25, response.effective_params.occlusion_threshold)
        self.assertEqual(0.2, response.effective_params.occlusion_area_ratio)
        self.assertEqual("cpu", response.effective_params.device)
        self.assertTrue(response.tilt.result.is_tilted)
        self.assertEqual(1, response.screen.primary.label)
        self.assertTrue(response.quality_abnormal.is_abnormal)
        self.assertFalse(response.occlusion.is_occluded)
        self.assertEqual(2.3, response.tilt.result.angle)
        tilt_args = tilt_mock.call_args.args
        self.assertIsInstance(tilt_args[0], np.ndarray)
        self.assertEqual(1.5, tilt_args[1].tilt_threshold)
        self.assertEqual("cpu", screen_mock.call_args.kwargs["device"])

    def test_run_detect_all_request_overrides_and_partial_include(self) -> None:
        from app.services.aggregate_detector import run_detect_all

        patches = self._patch_all_modules(tilt=fake_tilt_part(True))
        with patches[0], patches[1] as screen_mock, patches[2], patches[3] as occ_mock:
            response = run_detect_all(
                tiny_image_b64(),
                "1753791280207",
                tilt_threshold=0.5,
                screen_conf=0.4,
                screen_iou=0.5,
                occlusion_threshold=0.3,
                occlusion_area_ratio=0.15,
                include=["tilt", "quality_abnormal"],
            )

        self.assertEqual(["tilt", "quality_abnormal"], response.executed_modules)
        self.assertEqual(["tilt"], response.problem_types)
        self.assertEqual(0.5, response.effective_params.tilt_threshold)
        self.assertEqual(0.4, response.effective_params.screen_conf)
        self.assertEqual(0.5, response.effective_params.screen_iou)
        self.assertEqual(0.3, response.effective_params.occlusion_threshold)
        self.assertEqual(0.15, response.effective_params.occlusion_area_ratio)
        self.assertIsNone(response.screen)
        self.assertIsNone(response.occlusion)
        screen_mock.assert_not_called()
        occ_mock.assert_not_called()

    def test_run_detect_all_isolates_child_module_failure(self) -> None:
        from app.services.aggregate_detector import run_detect_all

        patches = self._patch_all_modules(tilt=fake_tilt_part(False), quality=fake_quality(True))
        with self.assertLogs("app.services.aggregate_detector", level="ERROR"), patches[0], patches[1] as screen_mock, patches[2], patches[3]:
            screen_mock.side_effect = RuntimeError("screen exploded")
            response = run_detect_all(tiny_image_b64(), "1753791280207")

        self.assertEqual(200, response.code)
        self.assertIn("screen", response.failed_modules)
        self.assertEqual(500, response.screen.code)
        self.assertNotIn("screen", response.problem_types)
        self.assertEqual(["quality_abnormal"], response.problem_types)
        self.assertEqual(200, response.tilt.code)
        self.assertEqual(200, response.quality_abnormal.code)
        self.assertEqual(200, response.occlusion.code)

    def test_screen_missing_counts_as_screen_problem(self) -> None:
        from app.services.aggregate_detector import run_detect_all

        patches = self._patch_all_modules(screen=fake_screen_item(None))
        with patches[0], patches[1], patches[2], patches[3]:
            response = run_detect_all(tiny_image_b64(), "1753791280207")

        self.assertEqual(["screen"], response.problem_types)
        self.assertIsNone(response.screen.primary)

    def test_aggregate_config_device_is_config_only(self) -> None:
        from app.services.aggregate_detector import run_detect_all

        settings = get_settings()
        custom = replace(settings, yolo=replace(settings.yolo, device="cuda:0"))
        patches = self._patch_all_modules()
        with patch("app.services.aggregate_detector.get_settings", return_value=custom), patches[0], patches[1] as screen_mock, patches[2], patches[3] as occ_mock:
            response = run_detect_all(tiny_image_b64(), "1753791280207")

        self.assertEqual("cuda:0", response.effective_params.device)
        self.assertEqual("cuda:0", screen_mock.call_args.kwargs["device"])
        self.assertEqual("cuda:0", occ_mock.call_args.kwargs["device"])


class AggregateApiRouteTests(unittest.TestCase):
    def test_detect_all_endpoint_returns_aggregate_response(self) -> None:
        client = TestClient(app)
        fake_response = SimpleNamespace(
            code=200,
            msg="检测完成",
            start_time="1",
            end_time="2",
            cost_ms=1.2,
            executed_modules=["tilt"],
            failed_modules=[],
            effective_params=SimpleNamespace(
                tilt_threshold=1.5,
                screen_conf=0.25,
                screen_iou=0.45,
                occlusion_threshold=0.25,
                occlusion_area_ratio=0.2,
                include=["tilt"],
                device="cpu",
            ),
            problem_types=["tilt"],
            tilt=SimpleNamespace(code=200, msg="检测完成", cost_ms=1.0, result=SimpleNamespace(is_tilted=True, angle=2.3)),
            screen=None,
            quality_abnormal=None,
            occlusion=None,
        )

        with patch("app.api.v1.aggregate.run_detect_all", return_value=fake_response):
            response = client.post("/detect_all", json={"image": tiny_image_b64(), "include": ["tilt"]})

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual(["tilt"], body["problem_types"])
        self.assertEqual(["tilt"], body["executed_modules"])
        self.assertIsNone(body["screen"])

    def test_detect_all_api_v1_route_is_not_registered(self) -> None:
        client = TestClient(app)
        response = client.post("/api/v1/detect_all", json={})

        self.assertEqual(404, response.status_code)

    def test_detect_all_rejects_invalid_requests(self) -> None:
        client = TestClient(app)

        non_json = client.post("/detect_all", data=tiny_image_b64(), headers={"Content-Type": "text/plain"})
        self.assertEqual(400, non_json.status_code)

        missing = client.post("/detect_all", json={})
        self.assertEqual(400, missing.status_code)

        invalid = client.post("/detect_all", json={"image": "not-base64"})
        self.assertEqual(400, invalid.status_code)

    def test_detect_all_disabled_returns_503(self) -> None:
        client = TestClient(app)
        settings = get_settings()
        custom = replace(settings, aggregate_detection=replace(settings.aggregate_detection, enabled=False))

        with patch("app.api.v1.aggregate.get_settings", return_value=custom):
            response = client.post("/detect_all", json={"image": tiny_image_b64()})

        self.assertEqual(503, response.status_code)
        self.assertEqual(503, response.json()["code"])

    def test_config_response_contains_aggregate_detection(self) -> None:
        client = TestClient(app)
        response = client.get("/config")

        self.assertEqual(200, response.status_code)
        self.assertIn("aggregate_detection", response.json())
        self.assertIn("yolo", response.json())
        self.assertNotIn("gpu", response.json())
        self.assertEqual(["tilt", "screen", "quality_abnormal", "occlusion"], response.json()["aggregate_detection"]["default_modules"])
