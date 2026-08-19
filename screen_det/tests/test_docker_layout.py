from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DockerLayoutTests(unittest.TestCase):
    def test_deployment_files_are_centralized_under_docker(self) -> None:
        for old_path in (
            "AGENT.md",
            "Dockerfile",
            "requirements-docker.txt",
            "start.sh",
            "scripts/run_deploy_verify.sh",
            "scripts/deploy_verify_http.py",
        ):
            with self.subTest(old_path=old_path):
                self.assertFalse((ROOT / old_path).exists())

        for path in (
            "docker/Dockerfile",
            "docker/requirements-docker.txt",
            "docker/start.sh",
            "docker/run_deploy_verify.sh",
            "docker/deploy_verify_http.py",
            "docker/build_cython_modules.py",
            "docker/protect_models.py",
            "docker/README.md",
        ):
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).is_file())

    def test_dockerfile_builds_cython_extensions_without_pyarmor_or_baked_config(self) -> None:
        path = ROOT / "docker" / "Dockerfile"
        if not path.is_file():
            self.fail("docker/Dockerfile does not exist")
        dockerfile = path.read_text(encoding="utf-8")
        lower = dockerfile.lower()

        self.assertIn("cython", lower)
        self.assertIn("build_cython_modules.py", dockerfile)
        self.assertIn("--remove-sources", dockerfile)
        self.assertNotIn("pyarmor", lower)
        self.assertIn("COPY model/ ./model/", dockerfile)
        self.assertNotIn("COPY config.toml", dockerfile)

    def test_runtime_image_never_copies_requirements_or_build_materials(self) -> None:
        path = ROOT / "docker" / "Dockerfile"
        if not path.is_file():
            self.fail("docker/Dockerfile does not exist")
        dockerfile = path.read_text(encoding="utf-8")

        self.assertNotIn("COPY docker/requirements-docker.txt", dockerfile)
        self.assertIn(
            "--mount=type=bind,source=docker/requirements-docker.txt,"
            "target=/tmp/requirements-docker.txt,ro",
            dockerfile,
        )
        self.assertNotIn("COPY docker/protect_models.py", dockerfile)
        self.assertNotIn("COPY docker/run_deploy_verify.sh", dockerfile)
