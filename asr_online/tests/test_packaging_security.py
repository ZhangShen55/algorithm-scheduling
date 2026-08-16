import os
import tempfile
import unittest
from pathlib import Path

from app.core.config import PROJECT_ROOT, Settings


class PackagingSecurityTests(unittest.TestCase):
    def test_model_paths_default_to_image_internal_model_directory(self):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as cfg:
            cfg.write(
                '\n'.join(
                    [
                        'id_engine = "test"',
                        'version = "test"',
                        'device = "cpu"',
                        'ngpu = 1',
                    ]
                )
            )
            cfg_path = cfg.name

        try:
            settings = Settings(config_path=cfg_path)

            self.assertEqual(
                settings.asr_online_model_dir,
                str(PROJECT_ROOT / "model/speech_paraformer_asr_nat-zh-cn-16k-common-vocab8404-online"),
            )
            self.assertEqual(
                settings.asr_online_punc_model_dir,
                str(PROJECT_ROOT / "model/punc_ct-transformer_zh-cn-common-vad_realtime-vocab272727"),
            )
            self.assertFalse(hasattr(Settings, "open_online"))
        finally:
            os.unlink(cfg_path)

    def test_encrypted_model_pt_is_decrypted_to_runtime_directory(self):
        from app.core.model_assets import encrypt_model_file, prepare_decrypted_model_dir

        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "asr"
            model_dir.mkdir()
            plain = model_dir / "model.pt"
            encrypted = model_dir / "model.pt.enc"
            plain.write_bytes(b"fake torch checkpoint bytes")
            (model_dir / "config.yaml").write_text("model: fake\n", encoding="utf-8")

            encrypt_model_file(plain, encrypted)
            plain.unlink()

            prepared = Path(prepare_decrypted_model_dir(str(model_dir)))

            self.assertNotEqual(prepared, model_dir)
            self.assertEqual((prepared / "model.pt").read_bytes(), b"fake torch checkpoint bytes")
            self.assertEqual((prepared / "config.yaml").read_text(encoding="utf-8"), "model: fake\n")
            self.assertFalse((model_dir / "model.pt").exists())

    def test_cython_build_collects_obfuscated_entry_module(self):
        import setup_cython

        sources = setup_cython.collect_sources()

        self.assertIn("app/main.py", sources)

    def test_cython_extensions_compile_as_c99(self):
        import setup_cython

        extensions = setup_cython.create_extensions(["app/main.py"])

        self.assertIn("-std=c99", extensions[0].extra_compile_args)

    def test_cython_dockerfile_installs_modelscope_runtime_dependencies_separately(self):
        dockerfile = Path("docker/Dockerfile.cython").read_text(encoding="utf-8")

        self.assertIn(
            "pip install --no-cache-dir setuptools==69.5.1 addict==2.4.0 datasets==2.18.0 pyarrow==15.0.2 pandas==2.2.2 Pillow torchaudio==2.6.0 sortedcontainers==2.4.0",
            dockerfile,
        )
        self.assertIn('"setuptools==69.5.1"', dockerfile)
        self.assertIn("conda install -n asr -y libsndfile", dockerfile)

    def test_release_requirements_pin_matching_torch_and_torchaudio(self):
        requirements = Path("requirements.txt").read_text(encoding="utf-8").splitlines()
        pytorch_requirements = [
            requirement
            for requirement in requirements
            if requirement.startswith(("torch<", "torch=", "torch>", "torchaudio"))
        ]

        self.assertEqual(
            ["torch==2.6.0", "torchaudio==2.6.0"],
            pytorch_requirements,
        )

    def test_release_dockerfile_checks_matching_torch_and_torchaudio(self):
        dockerfile = Path("docker/Dockerfile").read_text(encoding="utf-8")

        self.assertIn(
            "COPY docker/verify_torch_runtime.py /tmp/verify_torch_runtime.py",
            dockerfile,
        )
        self.assertIn("python /tmp/verify_torch_runtime.py", dockerfile)
        self.assertIn("rm -f /tmp/verify_torch_runtime.py", dockerfile)
        self.assertNotIn("RUN python - <<", dockerfile)

    def test_release_dockerfile_installs_modelscope_runtime_dependency_closure(self):
        dockerfile = Path("docker/Dockerfile").read_text(encoding="utf-8")

        self.assertIn(
            "pip install --no-cache-dir setuptools==69.5.1 addict==2.4.0 "
            "datasets==2.18.0 pyarrow==15.0.2 pandas==2.2.2 Pillow "
            "torchaudio==2.6.0 sortedcontainers==2.4.0",
            dockerfile,
        )
        self.assertIn("conda install -n asr -y libsndfile", dockerfile)

    def test_release_requirements_pin_modelscope_once(self):
        requirements = Path("requirements.txt").read_text(encoding="utf-8").splitlines()

        modelscope_requirements = [
            requirement
            for requirement in requirements
            if requirement.startswith("modelscope")
        ]
        self.assertEqual(["modelscope==1.16.0"], modelscope_requirements)

    def test_release_dockerfile_imports_application_during_build(self):
        dockerfile = Path("docker/Dockerfile").read_text(encoding="utf-8")

        self.assertIn('RUN python -c "from app.main import app"', dockerfile)

    def test_cython_dockerfile_removes_bytecode_caches(self):
        dockerfile = Path("docker/Dockerfile.cython").read_text(encoding="utf-8")

        self.assertIn('root.rglob("__pycache__")', dockerfile)
        self.assertIn('root.rglob("*.pyc")', dockerfile)
        self.assertIn('root.rglob("*.pyo")', dockerfile)

    def test_deployment_files_live_under_docker_directory(self):
        for file_name in ("Dockerfile", "Dockerfile.cython", "start.sh"):
            self.assertTrue(Path("docker", file_name).exists())
            self.assertFalse(Path(file_name).exists())

        self.assertFalse(Path("docker/nginx.conf").exists())

        dockerfile = Path("docker/Dockerfile.cython").read_text(encoding="utf-8")
        self.assertIn("docker/start.sh", dockerfile)
        self.assertNotIn("nginx", dockerfile.lower())
        self.assertIn("docker build -f docker/Dockerfile.cython", dockerfile)

    def test_runtime_entrypoint_uses_obfuscated_module_name(self):
        runtime = Path("app/main.py").read_text(encoding="utf-8")

        self.assertIn('"app.main:app"', runtime)


if __name__ == "__main__":
    unittest.main()
