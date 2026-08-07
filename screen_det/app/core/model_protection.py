from __future__ import annotations

import base64
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


MAGIC = b"SCREENDETMODEL1"
NONCE_SIZE = 12


class ModelProtectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelProtectionConfig:
    enabled: bool = False
    encrypted_model_root: str = "/run/screen-det/models-encrypted"
    key_file: str = "/run/screen-det/models-encrypted/model.key"
    decrypted_temp_root: str = "/dev/shm/screen-det-models"
    cleanup_after_load: bool = True


def generate_key() -> str:
    return base64.urlsafe_b64encode(AESGCM.generate_key(bit_length=256)).decode("ascii")


def encrypt_model_file(
    source_path: str | Path,
    target_path: str | Path,
    key: str | bytes,
) -> None:
    source = Path(source_path)
    target = Path(target_path)
    nonce = os.urandom(NONCE_SIZE)
    ciphertext = AESGCM(_decode_key(key)).encrypt(nonce, source.read_bytes(), None)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(MAGIC + nonce + ciphertext)


def decrypt_model_file(
    source_path: str | Path,
    target_path: str | Path,
    key: str | bytes,
) -> None:
    source = Path(source_path)
    target = Path(target_path)
    payload = source.read_bytes()
    if not payload.startswith(MAGIC) or len(payload) <= len(MAGIC) + NONCE_SIZE:
        raise ModelProtectionError(f"加密模型格式不正确: {source}")
    nonce_start = len(MAGIC)
    nonce_end = nonce_start + NONCE_SIZE
    try:
        plaintext = AESGCM(_decode_key(key)).decrypt(
            payload[nonce_start:nonce_end],
            payload[nonce_end:],
            None,
        )
    except (InvalidTag, ValueError) as exc:
        raise ModelProtectionError(f"模型解密失败: {source}") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(plaintext)
    target.chmod(0o600)


def read_key_file(key_file: str | Path) -> str:
    path = Path(key_file)
    if not path.is_file():
        raise ModelProtectionError(f"模型密钥文件不存在: {path}")
    key = path.read_text(encoding="utf-8").strip()
    _decode_key(key)
    return key


@contextmanager
def materialize_model_path(
    plain_model_path: str | Path,
    config: ModelProtectionConfig,
) -> Iterator[Path]:
    plain_path = Path(plain_model_path)
    if not config.enabled:
        if not plain_path.is_file():
            raise FileNotFoundError(f"YOLO weights not found: {plain_path}")
        yield plain_path
        return

    encrypted_path = Path(config.encrypted_model_root) / f"{plain_path.name}.enc"
    if not encrypted_path.is_file():
        raise ModelProtectionError(f"加密模型不存在: {encrypted_path}")
    key = read_key_file(config.key_file)
    temp_root = Path(config.decrypted_temp_root)
    temp_root.mkdir(parents=True, exist_ok=True)
    temp_root.chmod(0o700)
    materialized = temp_root / f"{uuid4().hex}-{plain_path.name}"
    try:
        decrypt_model_file(encrypted_path, materialized, key)
        yield materialized
    finally:
        key = ""
        if config.cleanup_after_load and materialized.exists():
            materialized.unlink()


def _decode_key(key: str | bytes) -> bytes:
    raw = key.strip().encode("ascii") if isinstance(key, str) else key.strip()
    try:
        decoded = base64.urlsafe_b64decode(raw)
    except Exception as exc:
        raise ModelProtectionError("模型密钥格式不正确") from exc
    if len(decoded) != 32:
        raise ModelProtectionError("模型密钥长度不正确")
    return decoded
