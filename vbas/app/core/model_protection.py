import base64
import hashlib
import hmac
import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

try:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ModuleNotFoundError:  # pragma: no cover - 依赖缺失时走标准库 fallback
    InvalidTag = ValueError
    AESGCM = None


logger = logging.getLogger(__name__)

_MAGIC = b"TIASMODEL1"
_NONCE_SIZE = 12
_FALLBACK_MAGIC = b"TIASMODEL2"
_FALLBACK_NONCE_SIZE = 16
_FALLBACK_TAG_SIZE = 32


class ModelProtectionError(RuntimeError):
    """模型保护处理失败。"""


@dataclass(frozen=True)
class ModelProtectionConfig:
    enabled: bool = False
    encrypted_model_root: Path | None = None
    decrypted_temp_root: Path = Path("/dev/shm/tias-models")
    key_file: Path | None = None
    cleanup_after_load: bool = True

    @classmethod
    def from_mapping(cls, mapping) -> "ModelProtectionConfig":
        if not isinstance(mapping, dict):
            mapping = {}
        enabled = _to_bool(mapping.get("Enabled", False))
        encrypted_root = _optional_path(mapping.get("EncryptedModelRoot"))
        key_file = _optional_path(mapping.get("KeyFile"))
        temp_root = Path(str(mapping.get("DecryptedTempRoot", cls.decrypted_temp_root))).expanduser()
        return cls(
            enabled=enabled,
            encrypted_model_root=encrypted_root,
            decrypted_temp_root=temp_root,
            key_file=key_file,
            cleanup_after_load=_to_bool(mapping.get("CleanupAfterLoad", True)),
        )


class ModelPathResolver:
    def __init__(self, config: ModelProtectionConfig):
        self.config = config
        self._prepared_paths: list[Path] = []
        self._cached_key: str | None = None

    def prepare_model_path(self, plain_model_path: str | Path) -> Path:
        plain_path = Path(plain_model_path)
        if not self.config.enabled:
            return plain_path
        if self.config.encrypted_model_root is None:
            raise ModelProtectionError("模型保护已启用但未配置 EncryptedModelRoot")
        key = self._get_key()
        encrypted_path = self.config.encrypted_model_root / f"{plain_path.name}.enc"
        if not encrypted_path.exists():
            raise ModelProtectionError(f"加密模型不存在: {encrypted_path}")
        self.config.decrypted_temp_root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.config.decrypted_temp_root, 0o700)
        target_path = self.config.decrypted_temp_root / plain_path.name
        decrypt_model_file(encrypted_path, target_path, key)
        self._prepared_paths.append(target_path)
        logger.info("模型已临时解密 model=%s temp_path=%s", plain_path.name, target_path)
        return target_path

    def cleanup(self) -> None:
        for path in list(self._prepared_paths):
            try:
                if path.exists():
                    path.unlink()
                    logger.info("临时明文模型已清理 temp_path=%s", path)
            finally:
                self._prepared_paths.remove(path)

    def _get_key(self) -> str:
        if self._cached_key is None:
            self._cached_key = read_key_file(self.config.key_file)
            self._cleanup_runtime_key_copy(self.config.key_file)
        return self._cached_key

    @staticmethod
    def _cleanup_runtime_key_copy(key_file: str | Path | None) -> None:
        if key_file is None:
            return
        path = Path(key_file)
        if not _is_runtime_key_copy(path):
            return
        try:
            if path.exists():
                path.unlink()
                logger.info("运行期模型密钥副本已清理 key_path=%s", path)
        except OSError as exc:
            logger.warning("运行期模型密钥副本清理失败 key_path=%s reason=%s", path, exc)


def generate_key() -> str:
    if AESGCM is not None:
        return base64.urlsafe_b64encode(AESGCM.generate_key(bit_length=256)).decode("ascii")
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")


def encrypt_model_file(source_path: str | Path, target_path: str | Path, key: str | bytes) -> None:
    source = Path(source_path)
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    key_bytes = _decode_key(key)
    if AESGCM is None:
        nonce = os.urandom(_FALLBACK_NONCE_SIZE)
        ciphertext = _xor_stream(source.read_bytes(), key_bytes, nonce)
        tag = hmac.new(key_bytes, nonce + ciphertext, hashlib.sha256).digest()
        target.write_bytes(_FALLBACK_MAGIC + nonce + tag + ciphertext)
        return
    nonce = os.urandom(_NONCE_SIZE)
    ciphertext = AESGCM(key_bytes).encrypt(nonce, source.read_bytes(), None)
    target.write_bytes(_MAGIC + nonce + ciphertext)


def decrypt_model_file(source_path: str | Path, target_path: str | Path, key: str | bytes) -> None:
    source = Path(source_path)
    target = Path(target_path)
    payload = source.read_bytes()
    if payload.startswith(_FALLBACK_MAGIC):
        key_bytes = _decode_key(key)
        nonce_start = len(_FALLBACK_MAGIC)
        tag_start = nonce_start + _FALLBACK_NONCE_SIZE
        ciphertext_start = tag_start + _FALLBACK_TAG_SIZE
        nonce = payload[nonce_start:tag_start]
        expected_tag = payload[tag_start:ciphertext_start]
        ciphertext = payload[ciphertext_start:]
        actual_tag = hmac.new(key_bytes, nonce + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(expected_tag, actual_tag):
            raise ModelProtectionError(f"模型解密失败: {source}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_xor_stream(ciphertext, key_bytes, nonce))
        return
    if not payload.startswith(_MAGIC):
        raise ModelProtectionError(f"加密模型格式不正确: {source}")
    nonce_start = len(_MAGIC)
    nonce_end = nonce_start + _NONCE_SIZE
    nonce = payload[nonce_start:nonce_end]
    ciphertext = payload[nonce_end:]
    try:
        plaintext = AESGCM(_decode_key(key)).decrypt(nonce, ciphertext, None)
    except (InvalidTag, ValueError) as exc:
        raise ModelProtectionError(f"模型解密失败: {source}") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(plaintext)


def read_key_file(key_file: str | Path | None) -> str:
    if key_file is None:
        raise ModelProtectionError("模型保护已启用但未配置 KeyFile")
    path = Path(key_file)
    if not path.exists():
        raise ModelProtectionError(f"模型密钥文件不存在: {path}")
    return path.read_text(encoding="utf-8").strip()


def _decode_key(key: str | bytes) -> bytes:
    if isinstance(key, bytes):
        raw = key.strip()
    else:
        raw = key.strip().encode("ascii")
    decoded = base64.urlsafe_b64decode(raw)
    if len(decoded) != 32:
        raise ModelProtectionError("模型密钥长度不正确")
    return decoded


def _xor_stream(data: bytes, key: bytes, nonce: bytes) -> bytes:
    output = bytearray()
    counter = 0
    while len(output) < len(data):
        block = hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        output.extend(block)
        counter += 1
    return bytes(left ^ right for left, right in zip(data, output))


def _optional_path(value) -> Path | None:
    if value in (None, ""):
        return None
    return Path(str(value)).expanduser()


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _is_runtime_key_copy(path: Path) -> bool:
    runtime_root = Path(os.getenv("TIAS_RUNTIME_KEY_ROOT", "/dev/shm")).expanduser()
    try:
        resolved_path = path.resolve()
        resolved_root = runtime_root.resolve()
        return resolved_path == resolved_root or resolved_root in resolved_path.parents
    except OSError:
        path_text = path.as_posix()
        root_text = runtime_root.as_posix().rstrip("/")
        return path_text == root_text or path_text.startswith(f"{root_text}/")
