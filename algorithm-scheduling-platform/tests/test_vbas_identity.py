from pathlib import Path

import pytest

from packages.platform_common.operator_registry import OperatorCode

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_production_platform_files_do_not_contain_legacy_identity() -> None:
    for directory in ("services", "packages", "migrations", "deploy"):
        for path in (PROJECT_ROOT / directory).rglob("*"):
            if path.is_file() and path.suffix in {".py", ".sql", ".yml", ".yaml"}:
                assert "tias" not in path.read_text(encoding="utf-8").lower(), path


def test_operator_code_accepts_vbas_and_rejects_legacy_identity() -> None:
    assert OperatorCode("vbas") is OperatorCode.VBAS
    with pytest.raises(ValueError):
        OperatorCode("tias")
