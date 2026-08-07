from __future__ import annotations

import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app


ROOT = Path(__file__).resolve().parents[1]


class RoutePrefixTests(unittest.TestCase):
    def test_root_advertises_only_unprefixed_routes(self) -> None:
        body = TestClient(app).get("/").json()

        self.assertEqual("/health", body["health"])
        self.assertEqual("/detect_all", body["detect_all"])
        route_values = (
            value
            for key, value in body.items()
            if key not in {"service", "version"}
        )
        self.assertTrue(all(not value.startswith("/api/v1") for value in route_values))

    def test_api_v1_routes_are_not_registered(self) -> None:
        client = TestClient(app)

        self.assertFalse(any(route.path.startswith("/api/v1") for route in app.routes))
        for path in ("/api/v1/health", "/api/v1/detect_all", "/api/v1/config"):
            with self.subTest(path=path):
                self.assertEqual(404, client.get(path).status_code)

    def test_api_prefix_configuration_is_removed(self) -> None:
        self.assertFalse(hasattr(get_settings().app, "api_prefix"))
        config_text = (ROOT / "config.toml").read_text(encoding="utf-8")
        self.assertNotIn("api_prefix", config_text)
