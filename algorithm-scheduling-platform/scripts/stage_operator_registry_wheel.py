from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
if str(PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATFORM_ROOT))

from scripts.build_and_stage_operator_registry_wheel import (  # noqa: E402
    build_and_stage_registry_wheel,
)


def main(arguments: Sequence[str] = ()) -> None:
    parser = argparse.ArgumentParser(
        description="Rebuild and stage the operator registry wheel.",
    )
    parser.parse_args(arguments)
    build_and_stage_registry_wheel()


if __name__ == "__main__":
    main(sys.argv[1:])
