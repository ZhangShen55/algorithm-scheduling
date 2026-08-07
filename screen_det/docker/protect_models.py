#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.model_protection import encrypt_model_file, generate_key


DEFAULT_MODEL_NAMES = ["occlusion.pt", "screen.pt"]


def protect_models(
    source_dir: Path,
    target_dir: Path,
    key: str,
    model_names: list[str] | None = None,
) -> list[Path]:
    names = DEFAULT_MODEL_NAMES if model_names is None else model_names
    target_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for model_name in names:
        source_path = source_dir / model_name
        if not source_path.is_file():
            continue
        target_path = target_dir / f"{model_name}.enc"
        encrypt_model_file(source_path, target_path, key)
        target_path.chmod(0o600)
        outputs.append(target_path)
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description="加密screen-det的YOLO模型")
    parser.add_argument("--source-dir", default=str(PROJECT_ROOT / "model"))
    parser.add_argument(
        "--target-dir",
        default=str(PROJECT_ROOT / "docker" / "models-encrypted"),
    )
    parser.add_argument("--key-file", required=True)
    parser.add_argument("--generate-key", action="store_true")
    parser.add_argument("--model", action="append")
    args = parser.parse_args()

    key_path = Path(args.key_file).expanduser()
    if key_path.is_file():
        key = key_path.read_text(encoding="utf-8").strip()
    elif args.generate_key:
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key = generate_key()
        key_path.write_text(key + "\n", encoding="utf-8")
        key_path.chmod(0o600)
        print(f"模型密钥已生成: {key_path}")
    else:
        raise FileNotFoundError(f"模型密钥文件不存在: {key_path}")

    outputs = protect_models(
        source_dir=Path(args.source_dir).expanduser(),
        target_dir=Path(args.target_dir).expanduser(),
        key=key,
        model_names=args.model,
    )
    for output in outputs:
        print(f"已生成加密模型: {output}")
    if not outputs:
        print("未找到可加密模型，请检查--source-dir或--model参数", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
