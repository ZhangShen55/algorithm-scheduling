import unittest

from scripts.check_tias_runtime_image import evaluate_runtime_files


class TiasRuntimeImageCheckTest(unittest.TestCase):
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
        ]

        result = evaluate_runtime_files(files)

        self.assertTrue(result.ok, result.failures)
        self.assertEqual(result.extension_count, 4)


if __name__ == "__main__":
    unittest.main()
