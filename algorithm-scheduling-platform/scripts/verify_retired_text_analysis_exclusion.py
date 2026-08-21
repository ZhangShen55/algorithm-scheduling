#!/usr/bin/env python3
"""Fail closed when retired Text Analysis identifiers return to active platform files."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PLATFORM_ROOT.parent
SCANNED_ROOTS = (
    WORKSPACE_ROOT / "control_service/app",
    WORKSPACE_ROOT / "orchestrator_service/app",
    WORKSPACE_ROOT / "vision_orchestrator_service/app",
    WORKSPACE_ROOT / "online_gateway_service/app",
    PLATFORM_ROOT / "packages",
    PLATFORM_ROOT / "deploy",
    PLATFORM_ROOT / "scripts",
)
SCANNED_SUFFIXES = frozenset({".json", ".py", ".sh", ".toml", ".yaml", ".yml"})
RETIRED_MARKERS = (
    "text_analysis",
    "extract_keywords",
    "course_overviews",
    "PPT_KEYWORDS",
    "COURSE_OVERVIEW",
)
ALLOWED_COMPATIBILITY_FILES = frozenset(
    {
        Path("control_service/app/infrastructure/retired_node_preflight.py"),
        Path(
            "algorithm-scheduling-platform/packages/platform_common/"
            "operator_audit_repository.py"
        ),
        Path("algorithm-scheduling-platform/deploy/milestone-2b-case-catalog.yaml"),
        Path(
            "algorithm-scheduling-platform/scripts/"
            "milestone_2b_case_runners/retirement.py"
        ),
        Path(
            "algorithm-scheduling-platform/scripts/"
            "verify_retired_text_analysis_exclusion.py"
        ),
        Path(
            "algorithm-scheduling-platform/scripts/"
            "run_milestone_2b_clean_clone_gate.py"
        ),
    }
)


def find_violations() -> list[dict[str, object]]:
    violations: list[dict[str, object]] = []
    for root in SCANNED_ROOTS:
        if root.is_symlink() or not root.is_dir():
            raise ValueError(f"active platform root is unavailable: {root}")
        for path in sorted(root.rglob("*")):
            if path.is_dir() or path.suffix not in SCANNED_SUFFIXES:
                continue
            if root == PLATFORM_ROOT / "deploy" and "reports" in path.parts:
                continue
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"active platform file is not regular: {path}")
            relative = path.relative_to(WORKSPACE_ROOT)
            if relative in ALLOWED_COMPATIBILITY_FILES or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="strict")
            for line_number, line in enumerate(text.splitlines(), 1):
                markers = [marker for marker in RETIRED_MARKERS if marker in line]
                if markers:
                    violations.append(
                        {
                            "path": relative.as_posix(),
                            "line": line_number,
                            "markers": markers,
                        }
                    )
    return violations


def _build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="验证退役文本分析能力没有回到当前平台运行边界",
        allow_abbrev=False,
    )


def main(argv: Sequence[str] | None = None) -> int:
    _build_parser().parse_args(argv)
    violations = find_violations()
    print(
        json.dumps(
            {
                "status": "PASS" if not violations else "FAIL",
                "scanned_roots": [
                    root.relative_to(WORKSPACE_ROOT).as_posix() for root in SCANNED_ROOTS
                ],
                "violation_count": len(violations),
                "violations": violations,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if not violations else 1


if __name__ == "__main__":
    raise SystemExit(main())
