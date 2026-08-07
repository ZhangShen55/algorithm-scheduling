#!/usr/bin/env python3
"""读取 Ultralytics YOLO .pt 权重中的类别标签。"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path


def _names_from_checkpoint(ckpt: object) -> dict | list | None:
    if not isinstance(ckpt, dict):
        return None
    if "names" in ckpt:
        return ckpt["names"]
    model = ckpt.get("model")
    if model is not None and hasattr(model, "names"):
        return model.names
    if model is not None and hasattr(model, "yaml"):
        yaml_cfg = model.yaml
        if isinstance(yaml_cfg, dict) and "names" in yaml_cfg:
            return yaml_cfg["names"]
    return None


def load_with_torch(path: Path) -> dict | list | None:
    import torch

    ckpt = torch.load(path, map_location="cpu", weights_only=False)
    return _names_from_checkpoint(ckpt)


def load_with_ultralytics(path: Path) -> dict | list | None:
    from ultralytics import YOLO

    model = YOLO(str(path))
    return model.names


def format_names(names: dict | list) -> list[tuple[int, str]]:
    if isinstance(names, dict):
        return [(int(k), str(v)) for k, v in sorted(names.items(), key=lambda x: int(x[0]))]
    return [(i, str(n)) for i, n in enumerate(names)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect YOLO .pt class names")
    parser.add_argument(
        "--weights",
        default="model/screen.pt",
        help="Path to .pt weights",
    )
    args = parser.parse_args()
    path = Path(args.weights).resolve()
    if not path.exists():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    print(f"weights: {path}")
    print(f"size_mb: {path.stat().st_size / 1024 / 1024:.2f}")

    if zipfile.is_zipfile(path):
        print(f"format: zip (Ultralytics checkpoint)")

    names = None
    method = None
    try:
        names = load_with_ultralytics(path)
        method = "ultralytics.YOLO"
    except ImportError:
        pass
    except Exception as exc:
        print(f"ultralytics load failed: {exc}")

    if names is None:
        try:
            names = load_with_torch(path)
            method = "torch.load"
        except ImportError:
            print(
                "ERROR: need torch or ultralytics. "
                "Install: pip install ultralytics",
                file=sys.stderr,
            )
            sys.exit(2)
        except Exception as exc:
            print(f"torch load failed: {exc}", file=sys.stderr)
            sys.exit(3)

    if names is None:
        print("ERROR: no class names found in checkpoint", file=sys.stderr)
        sys.exit(4)

    print(f"method: {method}")
    print(f"num_classes: {len(names)}")
    print("--- classes ---")
    for idx, label in format_names(names):
        print(f"  {idx}: {label}")


if __name__ == "__main__":
    main()
