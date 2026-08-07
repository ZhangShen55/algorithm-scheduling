import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from app.core.model_protection import (
    ModelProtectionConfig,
    ModelProtectionError,
    ModelPathResolver,
    decrypt_model_file,
    encrypt_model_file,
    generate_key,
)
from scripts.protect_tias_models import protect_models


class TiasModelProtectionTest(unittest.TestCase):
    def test_encrypt_decrypt_round_trip_and_wrong_key_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            plain = root / "student.pt"
            encrypted = root / "student.pt.enc"
            decrypted = root / "student.decrypted.pt"
            plain.write_bytes(b"fake-model-content")

            key = generate_key()
            wrong_key = generate_key()
            encrypt_model_file(plain, encrypted, key)
            decrypt_model_file(encrypted, decrypted, key)

            self.assertEqual(decrypted.read_bytes(), b"fake-model-content")
            with self.assertRaises(ModelProtectionError):
                decrypt_model_file(encrypted, root / "wrong.pt", wrong_key)

    def test_resolver_uses_encrypted_root_and_cleans_temp_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            encrypted_root = root / "models-encrypted"
            temp_root = root / "tmp"
            encrypted_root.mkdir()
            plain = root / "student.pt"
            plain.write_bytes(b"fake-student")
            key_file = root / "model.key"
            key = generate_key()
            key_file.write_text(key, encoding="utf-8")
            encrypt_model_file(plain, encrypted_root / "student.pt.enc", key)

            resolver = ModelPathResolver(ModelProtectionConfig(
                enabled=True,
                encrypted_model_root=encrypted_root,
                decrypted_temp_root=temp_root,
                key_file=key_file,
                cleanup_after_load=True,
            ))

            resolved = resolver.prepare_model_path(plain)

            self.assertEqual(resolved.read_bytes(), b"fake-student")
            self.assertEqual(oct(temp_root.stat().st_mode & 0o777), "0o700")
            resolver.cleanup()
            self.assertFalse(resolved.exists())

    def test_resolver_removes_runtime_key_copy_and_reuses_cached_key(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            encrypted_root = root / "models-encrypted"
            temp_root = root / "tmp"
            runtime_key_root = root / "dev-shm"
            key_file = runtime_key_root / "tias_model_key"
            encrypted_root.mkdir()
            runtime_key_root.mkdir()
            key = generate_key()
            key_file.write_text(key, encoding="utf-8")

            first_plain = root / "student.pt"
            second_plain = root / "teacher_behavior.pt"
            first_plain.write_bytes(b"student")
            second_plain.write_bytes(b"teacher")
            encrypt_model_file(first_plain, encrypted_root / "student.pt.enc", key)
            encrypt_model_file(second_plain, encrypted_root / "teacher_behavior.pt.enc", key)

            resolver = ModelPathResolver(ModelProtectionConfig(
                enabled=True,
                encrypted_model_root=encrypted_root,
                decrypted_temp_root=temp_root,
                key_file=key_file,
                cleanup_after_load=True,
            ))

            with patch.dict("os.environ", {"TIAS_RUNTIME_KEY_ROOT": str(runtime_key_root)}):
                first_resolved = resolver.prepare_model_path(first_plain)
                self.assertFalse(key_file.exists())
                second_resolved = resolver.prepare_model_path(second_plain)

            self.assertEqual(first_resolved.read_bytes(), b"student")
            self.assertEqual(second_resolved.read_bytes(), b"teacher")
            resolver.cleanup()
            self.assertFalse(first_resolved.exists())
            self.assertFalse(second_resolved.exists())

    def test_disabled_resolver_returns_original_path(self):
        path = Path("/workspace/models/student.pt")
        resolver = ModelPathResolver(ModelProtectionConfig(enabled=False))

        self.assertEqual(resolver.prepare_model_path(path), path)

    def test_protect_models_encrypts_existing_default_models(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_dir = root / "models"
            target_dir = root / "models-encrypted"
            source_dir.mkdir()
            (source_dir / "student.pt").write_bytes(b"student")

            outputs = protect_models(source_dir, target_dir, generate_key())

            self.assertEqual(outputs, [target_dir / "student.pt.enc"])
            self.assertTrue(outputs[0].exists())


if __name__ == "__main__":
    unittest.main()
