from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.model_verification import ModelVerificationError, verify_manifest


def main() -> int:
    models_root = PROJECT_ROOT / "models"
    try:
        verified = verify_manifest(models_root, models_root / "manifest.sha256")
    except ModelVerificationError as error:
        print(f"模型验证失败：{error}", file=sys.stderr)
        return 1
    print(f"模型验证通过：{len(verified)} 个文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
