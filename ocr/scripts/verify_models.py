from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.model_verification import ModelVerificationError, verify_manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models-root", type=Path, default=PROJECT_ROOT / "models")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--exact", action="store_true")
    arguments = parser.parse_args()
    models_root = arguments.models_root
    manifest = arguments.manifest or models_root / "manifest.sha256"
    try:
        verified = verify_manifest(
            models_root,
            manifest,
            exact=arguments.exact,
        )
    except ModelVerificationError as error:
        print(f"模型验证失败：{error}", file=sys.stderr)
        return 1
    print(f"模型验证通过：{len(verified)} 个文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
