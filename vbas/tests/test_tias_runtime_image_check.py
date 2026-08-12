import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.check_tias_runtime_image import (
    evaluate_runtime_config,
    evaluate_runtime_files,
    inspect_image_config,
    inspect_image_files,
)


class TiasRuntimeImageCheckTest(unittest.TestCase):
    def _install_docker_stub(self, directory: Path, source: str) -> None:
        stub = directory / "docker"
        stub.write_text(source, encoding="utf-8")
        stub.chmod(0o755)

    def test_evaluate_runtime_files_rejects_sensitive_runtime_content(self):
        files = [
            "/workspace/app/main.py",
            "/workspace/app/api/__init__.py",
            "/workspace/app/api/stu_tea_behavior.py",
            "/workspace/app/core/settings.cpython-311-x86_64-linux-gnu.so",
            "/workspace/models/student.pt",
            "/workspace/docker/Dockerfile",
            "/workspace/RUNNING.md",
            "/run/bootstrap-secrets/tias_model_key",
        ]

        result = evaluate_runtime_files(files)

        self.assertFalse(result.ok)
        self.assertTrue(any("明文模型" in item for item in result.failures))
        self.assertTrue(any("非运行目录" in item for item in result.failures))
        self.assertTrue(any("核心明文源码" in item for item in result.failures))
        self.assertTrue(any("密钥文件" in item for item in result.failures))

    def test_evaluate_runtime_files_accepts_minimal_runtime_content(self):
        files = [
            "/workspace/app/__init__.py",
            "/workspace/app/main.py",
            "/workspace/app/api/__init__.py",
            "/workspace/app/api/stu_tea_behavior.cpython-311-x86_64-linux-gnu.so",
            "/workspace/app/core/__init__.py",
            "/workspace/app/core/settings.cpython-311-x86_64-linux-gnu.so",
            "/workspace/app/services/__init__.py",
            "/workspace/app/services/registration.cpython-311-x86_64-linux-gnu.so",
            "/workspace/app/schemas/__init__.py",
            "/workspace/app/schemas/response.cpython-311-x86_64-linux-gnu.so",
            "/workspace/app/vendor/DirectMHP/models/experimental.py",
            "/usr/local/bin/tias-secure-entrypoint",
            "/usr/local/bin/vbas-start",
        ]

        result = evaluate_runtime_files(
            files,
            executable_files={
                "/usr/local/bin/tias-secure-entrypoint",
                "/usr/local/bin/vbas-start",
            },
        )

        self.assertTrue(result.ok, result.failures)
        self.assertEqual(result.extension_count, 4)

    def test_evaluate_runtime_files_rejects_missing_vbas_start(self):
        files = [
            "/workspace/app/main.py",
            "/workspace/app/core/settings.cpython-311-x86_64-linux-gnu.so",
            "/usr/local/bin/tias-secure-entrypoint",
        ]

        result = evaluate_runtime_files(
            files,
            executable_files={"/usr/local/bin/tias-secure-entrypoint"},
        )

        self.assertFalse(result.ok)
        self.assertIn(
            "缺少运行必需文件: /usr/local/bin/vbas-start",
            result.failures,
        )

    def test_evaluate_runtime_files_rejects_non_executable_entrypoints(self):
        files = [
            "/workspace/app/main.py",
            "/workspace/app/core/settings.cpython-311-x86_64-linux-gnu.so",
            "/usr/local/bin/tias-secure-entrypoint",
            "/usr/local/bin/vbas-start",
        ]

        result = evaluate_runtime_files(files, executable_files=set())

        self.assertFalse(result.ok)
        self.assertIn(
            "运行入口不可执行: /usr/local/bin/tias-secure-entrypoint",
            result.failures,
        )
        self.assertIn(
            "运行入口不可执行: /usr/local/bin/vbas-start",
            result.failures,
        )

    def test_evaluate_runtime_config_rejects_wrong_default_command(self):
        failures = evaluate_runtime_config(
            entrypoint=["/usr/local/bin/tias-secure-entrypoint"],
            command=["python", "-m", "uvicorn", "app.main:app"],
        )

        self.assertIn(
            "默认 CMD 必须为: /usr/local/bin/vbas-start",
            failures,
        )

    def test_evaluate_runtime_config_rejects_wrong_entrypoint(self):
        failures = evaluate_runtime_config(
            entrypoint=["/usr/local/bin/vbas-start"],
            command=["/usr/local/bin/vbas-start"],
        )

        self.assertIn(
            "默认 ENTRYPOINT 必须为: /usr/local/bin/tias-secure-entrypoint",
            failures,
        )

    def test_evaluate_runtime_config_accepts_secure_startup_chain(self):
        failures = evaluate_runtime_config(
            entrypoint=["/usr/local/bin/tias-secure-entrypoint"],
            command=["/usr/local/bin/vbas-start"],
        )

        self.assertEqual(failures, [])

    def test_inspect_image_files_enumerates_forbidden_workspace_content(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            bin_dir = Path(temporary_directory)
            self._install_docker_stub(
                bin_dir,
                """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' \
  /workspace/app/main.py \
  /workspace/app/core/settings.cpython-311-x86_64-linux-gnu.so \
  /usr/local/bin/tias-secure-entrypoint \
  /usr/local/bin/vbas-start
if [[ "$*" == *"find /workspace -xdev -type f -print"* ]]; then
  printf '%s\n' \
    /workspace/models/student.pt \
    /workspace/docker/Dockerfile \
    /workspace/RUNNING.md
fi
printf '%s\n' \
  __EXECUTABLE__/usr/local/bin/tias-secure-entrypoint \
  __EXECUTABLE__/usr/local/bin/vbas-start
""",
            )
            environment = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}

            with patch.dict(os.environ, environment):
                files, executable_files = inspect_image_files("vbas:test")
            result = evaluate_runtime_files(
                files,
                executable_files=executable_files,
            )

        self.assertFalse(result.ok)
        self.assertTrue(any("明文模型" in item for item in result.failures))
        self.assertTrue(any("非运行目录" in item for item in result.failures))
        self.assertTrue(any("非运行文件" in item for item in result.failures))

    def test_inspect_image_config_wraps_malformed_json(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            bin_dir = Path(temporary_directory)
            self._install_docker_stub(
                bin_dir,
                """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' '{not-json}'
""",
            )
            environment = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}

            with (
                patch.dict(os.environ, environment),
                self.assertRaisesRegex(
                    RuntimeError,
                    "镜像启动配置解析失败.*vbas:test",
                ),
            ):
                inspect_image_config("vbas:test")


if __name__ == "__main__":
    unittest.main()
