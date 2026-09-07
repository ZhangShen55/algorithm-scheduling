from __future__ import annotations

from pathlib import Path

import pytest

from scripts.vbas_benchmark.models import BenchmarkIdentity
from scripts.vbas_benchmark.report import atomic_write_once_json, attempt_root


def test_attempt_root_is_deterministic_and_write_once(tmp_path: Path) -> None:
    identity = BenchmarkIdentity(
        "campaign-001",
        2,
        7,
        "a" * 40,
        ("sha256:image",),
        "b" * 64,
    )
    root = attempt_root(tmp_path, identity, "teacher-c8")
    output = root / "summary.json"
    atomic_write_once_json(output, {"status": "failed"})

    assert output.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError, match="禁止覆盖"):
        atomic_write_once_json(output, {"status": "passed"})


def test_attempt_root_rejects_nested_case_name(tmp_path: Path) -> None:
    identity = BenchmarkIdentity(
        "campaign-001",
        1,
        7,
        "a" * 40,
        ("image",),
        "b" * 64,
    )
    with pytest.raises(ValueError, match="case_id"):
        attempt_root(tmp_path, identity, "../escape")
