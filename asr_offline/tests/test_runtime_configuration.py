import os
import logging
import tomllib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from packages.operator_registry_client.logging import SizeAndAgeRotatingFileHandler


ASR_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = ASR_ROOT.parent


class RuntimeConfigurationTests(unittest.TestCase):
    def test_relative_config_path_is_resolved_from_project_root(self) -> None:
        from app.core.config import PROJECT_ROOT, resolve_config_path

        original_cwd = Path.cwd()
        with TemporaryDirectory() as temporary_directory:
            try:
                os.chdir(temporary_directory)
                with patch.dict(
                    "os.environ",
                    {"CONFIG_PATH": "configs/local.toml"},
                ):
                    self.assertEqual(
                        resolve_config_path(),
                        (PROJECT_ROOT / "configs/local.toml").resolve(),
                    )
            finally:
                os.chdir(original_cwd)

    def test_runtime_configs_omit_retired_fields(self) -> None:
        config_paths = (
            ASR_ROOT / "config.toml",
            WORKSPACE_ROOT
            / "algorithm-scheduling-platform"
            / "deploy"
            / "config"
            / "operators"
            / "asr_offline.gpu.toml",
        )

        for config_path in config_paths:
            with self.subTest(config_path=config_path), config_path.open("rb") as source:
                config = tomllib.load(source)
            for field in ("version", "ngpu", "ncpu", "log_path", "hotword_path"):
                self.assertNotIn(field, config)

    def test_version_is_a_fixed_runtime_constant(self) -> None:
        from app.core.config import settings

        original_config = dict(settings._cfg)
        try:
            settings._cfg["version"] = "legacy-version"
            self.assertEqual(settings.version, "asr:latest")
        finally:
            settings._cfg.clear()
            settings._cfg.update(original_config)

    def test_logging_keeps_at_most_seven_calendar_days(self) -> None:
        from app.core import logging as asr_logging

        root = logging.getLogger()
        original_handlers = root.handlers[:]
        original_level = root.level
        for handler in root.handlers[:]:
            root.removeHandler(handler)

        try:
            with TemporaryDirectory() as temporary_directory:
                log_dir = Path(temporary_directory) / "logs"
                with patch.object(asr_logging, "LOG_DIR", log_dir, create=True):
                    asr_logging.setup_logging()

                rotating_handlers = [
                    handler
                    for handler in root.handlers
                    if isinstance(handler, SizeAndAgeRotatingFileHandler)
                ]
                self.assertEqual(len(rotating_handlers), 1)
                self.assertEqual(rotating_handlers[0].settings.retention_days, 7)
                self.assertEqual(rotating_handlers[0]._max_bytes, 100 * 1024 * 1024)
                self.assertEqual(
                    rotating_handlers[0].settings.log_path,
                    (log_dir / "local" / "application.log").resolve(),
                )
        finally:
            for handler in root.handlers[:]:
                root.removeHandler(handler)
                handler.close()
            root.handlers = original_handlers
            root.setLevel(original_level)


if __name__ == "__main__":
    unittest.main()
