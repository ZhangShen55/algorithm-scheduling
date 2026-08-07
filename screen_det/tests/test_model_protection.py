from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


MODEL_PROTECTION_AVAILABLE = importlib.util.find_spec("app.core.model_protection") is not None

if MODEL_PROTECTION_AVAILABLE:
    from app.core.model_protection import (
        ModelProtectionConfig,
        ModelProtectionError,
        decrypt_model_file,
        encrypt_model_file,
        generate_key,
        materialize_model_path,
    )


class ModelProtectionAvailabilityTests(unittest.TestCase):
    def test_model_protection_module_exists(self) -> None:
        self.assertTrue(MODEL_PROTECTION_AVAILABLE)


@unittest.skipUnless(MODEL_PROTECTION_AVAILABLE, "model protection not implemented")
class ModelProtectionTests(unittest.TestCase):
    def test_encrypt_decrypt_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "screen.pt"
            encrypted = root / "screen.pt.enc"
            decrypted = root / "screen.pt"
            payload = b"fake-yolo-weights" * 128
            source.write_bytes(payload)
            key = generate_key()

            encrypt_model_file(source, encrypted, key)
            source.unlink()
            decrypt_model_file(encrypted, decrypted, key)

            self.assertEqual(payload, decrypted.read_bytes())
            self.assertNotIn(payload[:32], encrypted.read_bytes())

    def test_wrong_key_and_corrupted_ciphertext_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "occlusion.pt"
            encrypted = root / "occlusion.pt.enc"
            source.write_bytes(b"weights")
            encrypt_model_file(source, encrypted, generate_key())

            with self.assertRaises(ModelProtectionError):
                decrypt_model_file(encrypted, root / "wrong.pt", generate_key())

            payload = bytearray(encrypted.read_bytes())
            payload[-1] ^= 0x01
            encrypted.write_bytes(payload)
            with self.assertRaises(ModelProtectionError):
                decrypt_model_file(encrypted, root / "corrupt.pt", generate_key())

    def test_materialized_encrypted_model_is_cleaned_after_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plain_root = root / "plain"
            encrypted_root = root / "encrypted"
            runtime_root = root / "runtime"
            plain_root.mkdir()
            encrypted_root.mkdir()
            source = plain_root / "screen.pt"
            source.write_bytes(b"model-payload")
            key = generate_key()
            key_file = encrypted_root / "model.key"
            key_file.write_text(key + "\n", encoding="utf-8")
            encrypt_model_file(source, encrypted_root / "screen.pt.enc", key)
            config = ModelProtectionConfig(
                enabled=True,
                encrypted_model_root=str(encrypted_root),
                key_file=str(key_file),
                decrypted_temp_root=str(runtime_root),
                cleanup_after_load=True,
            )

            with materialize_model_path(source, config) as materialized:
                self.assertEqual(b"model-payload", materialized.read_bytes())
                self.assertEqual(runtime_root, materialized.parent)

            self.assertFalse(materialized.exists())
            self.assertTrue(key_file.exists())

    def test_plain_mode_returns_original_path_without_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "screen.pt"
            source.write_bytes(b"plain")
            config = ModelProtectionConfig(enabled=False)

            with materialize_model_path(source, config) as materialized:
                self.assertEqual(source, materialized)

            self.assertTrue(source.exists())

    def test_missing_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = ModelProtectionConfig(
                enabled=True,
                encrypted_model_root=str(root),
                key_file=str(root / "missing.key"),
                decrypted_temp_root=str(root / "runtime"),
            )

            with self.assertRaises(ModelProtectionError):
                with materialize_model_path(root / "screen.pt", config):
                    pass
