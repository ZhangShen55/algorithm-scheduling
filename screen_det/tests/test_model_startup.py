from __future__ import annotations

import asyncio
import importlib.util
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.services.occlusion_detector as occlusion_detector


APPLICATION_AVAILABLE = importlib.util.find_spec("app.application") is not None

if APPLICATION_AVAILABLE:
    from app.application import app, preload_yolo_models


class ModelStartupAvailabilityTests(unittest.TestCase):
    def test_application_module_exists(self) -> None:
        self.assertTrue(APPLICATION_AVAILABLE)

    def test_occlusion_startup_loader_exists(self) -> None:
        self.assertTrue(hasattr(occlusion_detector, "ensure_occlusion_model_loaded"))


@unittest.skipUnless(APPLICATION_AVAILABLE, "application lifecycle not implemented")
class ModelStartupTests(unittest.TestCase):
    def test_startup_loads_both_yolo_models(self) -> None:
        ready = {"loaded": True, "warmed_up": True, "device": "cpu"}
        with patch("app.application.ensure_screen_model_loaded", return_value=ready) as screen, patch(
            "app.application.ensure_occlusion_model_loaded", return_value=ready
        ) as occlusion:
            asyncio.run(preload_yolo_models())

        screen.assert_called_once_with()
        occlusion.assert_called_once_with()

    def test_startup_fails_when_any_model_fails(self) -> None:
        ready = {"loaded": True, "warmed_up": True, "device": "cpu"}
        with patch("app.application.ensure_screen_model_loaded", return_value=ready), patch(
            "app.application.ensure_occlusion_model_loaded",
            side_effect=RuntimeError("occlusion failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "occlusion failed"):
                asyncio.run(preload_yolo_models())

    def test_health_requires_both_models_ready(self) -> None:
        with patch("app.api.v1.health.is_screen_model_ready", return_value=True), patch(
            "app.api.v1.health.is_occlusion_model_ready", return_value=False
        ), patch(
            "app.api.v1.health.screen_model_holder",
            SimpleNamespace(
                status={"loaded": True, "warmed_up": True, "device": "cpu"},
                device="cpu",
            ),
        ), patch(
            "app.api.v1.health.occlusion_model_holder",
            SimpleNamespace(
                status={"loaded": False, "warmed_up": False, "device": "cpu"},
            ),
        ):
            response = TestClient(app).get("/health")

        self.assertEqual(503, response.status_code)
        self.assertFalse(response.json()["ready"])

    def test_health_hides_model_weights(self) -> None:
        with patch("app.api.v1.health.is_screen_model_ready", return_value=True), patch(
            "app.api.v1.health.is_occlusion_model_ready", return_value=True
        ), patch(
            "app.api.v1.health.screen_model_holder",
            SimpleNamespace(
                status={
                    "loaded": True,
                    "warmed_up": True,
                    "weights": "/private/model/screen.pt",
                    "device": "cpu",
                },
                device="cpu",
            ),
        ), patch(
            "app.api.v1.health.occlusion_model_holder",
            SimpleNamespace(
                status={
                    "loaded": True,
                    "warmed_up": True,
                    "weights": "occlusion.pt",
                    "device": "cpu",
                },
            ),
        ):
            response = TestClient(app).get("/health")

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertNotIn("weights", body["screen_model"])
        self.assertNotIn("weights", body["occlusion_model"])
