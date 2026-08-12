from __future__ import annotations

from scripts.build_and_stage_operator_registry_wheel import (
    build_and_stage_registry_wheel,
)


def main() -> None:
    build_and_stage_registry_wheel()


if __name__ == "__main__":
    main()
