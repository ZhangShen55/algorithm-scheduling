from __future__ import annotations

import shutil
from pathlib import Path


PLATFORM_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PLATFORM_ROOT.parent
WHEEL_NAME = "algorithm_operator_registry_client-0.1.0-py3-none-any.whl"
SOURCE = PLATFORM_ROOT / "packages" / "operator_registry_client" / "dist" / WHEEL_NAME
TARGET_PROJECTS = (
    "asr_offline",
    "asr_online",
    "ppt_slice",
    "ocr",
    "text_analysis",
    "vbas",
    "facerec",
    "screen_det",
)


def main() -> None:
    if not SOURCE.is_file():
        raise SystemExit(
            "注册客户端 wheel 不存在，请先在 packages/operator_registry_client 下构建"
        )
    for project in TARGET_PROJECTS:
        wheel_dir = WORKSPACE_ROOT / project / "wheel"
        wheel_dir.mkdir(parents=True, exist_ok=True)
        target = wheel_dir / WHEEL_NAME
        shutil.copy2(SOURCE, target)
        print(f"已暂存: {target}")


if __name__ == "__main__":
    main()
