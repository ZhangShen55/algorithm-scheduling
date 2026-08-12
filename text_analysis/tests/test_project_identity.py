import unittest
from pathlib import Path

from app.main import app


class ProjectIdentityTests(unittest.TestCase):
    def test_application_uses_text_analysis_identity(self):
        self.assertEqual(app.title, "Text Analysis API")

    def test_runtime_entrypoint_remains_app_main(self):
        self.assertIn(
            "APP_MODULE=app.main:app",
            Path("Dockerfile").read_text(encoding="utf-8"),
        )
        self.assertIn(
            'APP_MODULE="${APP_MODULE:-app.main:app}"',
            Path("start.sh").read_text(encoding="utf-8"),
        )

    def test_scheduling_routes_remain_available(self):
        paths = app.openapi()["paths"]

        self.assertIn("post", paths["/v1/course_overviews"])
        self.assertIn("post", paths["/v1/extract_keywords"])


if __name__ == "__main__":
    unittest.main()
