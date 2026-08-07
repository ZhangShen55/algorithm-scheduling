from __future__ import annotations

import base64
import hashlib
import shutil
import tempfile
from pathlib import Path

ENCRYPTION_KEY = base64.b64decode(
    "yldDq0ZHcN1vRYM3C1gDkFbOqD9uFuHN2aNnC2cGuX8="
)
CHUNK_SIZE = 1024 * 1024


def _keystream(length: int, counter: int) -> tuple[bytes, int]:
    blocks = []
    remaining = length
    while remaining > 0:
        digest = hashlib.sha256(
            ENCRYPTION_KEY + counter.to_bytes(8, "big")
        ).digest()
        blocks.append(digest[:remaining])
        remaining -= len(digest)
        counter += 1
    return b"".join(blocks)[:length], counter


def _xor_model_file(src: Path, dst: Path) -> None:
    counter = 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    with src.open("rb") as in_file, dst.open("wb") as out_file:
        while True:
            chunk = in_file.read(CHUNK_SIZE)
            if not chunk:
                break
            stream, counter = _keystream(len(chunk), counter)
            out_file.write(bytes(a ^ b for a, b in zip(chunk, stream)))


def encrypt_model_file(src: str | Path, dst: str | Path) -> None:
    _xor_model_file(Path(src), Path(dst))


def prepare_decrypted_model_dir(model_dir: str) -> str:
    source_dir = Path(model_dir)
    plain_model = source_dir / "model.pt"
    encrypted_model = source_dir / "model.pt.enc"

    if plain_model.exists():
        return str(source_dir)
    if not encrypted_model.exists():
        raise FileNotFoundError(f"missing model.pt or model.pt.enc in {source_dir}")

    runtime_dir = Path(tempfile.mkdtemp(prefix=f"{source_dir.name}_", dir="/tmp"))
    for item in source_dir.iterdir():
        if item.name in {"model.pt", "model.pt.enc"}:
            continue
        target = runtime_dir / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)
    _xor_model_file(encrypted_model, runtime_dir / "model.pt")
    return str(runtime_dir)
