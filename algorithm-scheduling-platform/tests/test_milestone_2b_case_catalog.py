from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
LOADER_PATH = PLATFORM_ROOT / "scripts/milestone_2b_case_catalog.py"
CATALOG_PATH = PLATFORM_ROOT / "deploy/milestone-2b-case-catalog.yaml"

EXPECTED_PREFIX_COUNTS = {
    "DEP": 20,
    "GPU": 20,
    "REG": 20,
    "INF": 16,
    "JOB": 20,
    "FILE": 16,
    "PPT": 15,
    "OCR": 5,
    "KEY": 5,
    "ASR": 18,
    "VIS": 28,
    "ONL": 20,
    "FACE": 14,
    "LOAD": 26,
}


def _load_catalog_module() -> ModuleType:
    assert LOADER_PATH.is_file(), "2B 用例目录加载器必须存在"
    spec = importlib.util.spec_from_file_location("milestone_2b_case_catalog", LOADER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_catalog_contains_exact_authority() -> None:
    module = _load_catalog_module()

    catalog = module.load_case_catalog(CATALOG_PATH)

    assert len(catalog.cases) == 243
    assert catalog.count_by_category() == {"negative": 217, "load": 26}
    assert catalog.count_by_prefix() == EXPECTED_PREFIX_COUNTS
    assert len({case.case_id for case in catalog.cases}) == 243


def _catalog_document() -> dict[str, Any]:
    document = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _write_catalog(path: Path, document: dict[str, Any]) -> None:
    path.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda document: document["cases"].pop(), "精确包含 243"),
        (
            lambda document: document["cases"].append(dict(document["cases"][0])),
            "重复",
        ),
        (lambda document: document.update({"unknown": True}), "未知字段"),
        (
            lambda document: document["cases"][0].update({"unknown": True}),
            "未知字段",
        ),
        (
            lambda document: document["cases"][0].update({"category": "other"}),
            "category",
        ),
        (
            lambda document: document["cases"][0].update({"phase": "other"}),
            "phase",
        ),
        (
            lambda document: document["cases"][0].update({"safety": "other"}),
            "safety",
        ),
        (
            lambda document: document["cases"][0].update({"timeout_seconds": 0}),
            "timeout_seconds",
        ),
        (
            lambda document: document["cases"][0].update({"runner": "bad runner"}),
            "runner",
        ),
    ],
)
def test_catalog_rejects_invalid_authority(
    tmp_path: Path,
    mutate: Callable[[dict[str, Any]], object],
    message: str,
) -> None:
    module = _load_catalog_module()
    document = _catalog_document()
    mutate(document)
    path = tmp_path / "catalog.yaml"
    _write_catalog(path, document)

    with pytest.raises(ValueError, match=message):
        module.load_case_catalog(path)


def test_catalog_rejects_symlink(tmp_path: Path) -> None:
    module = _load_catalog_module()
    link = tmp_path / "catalog.yaml"
    link.symlink_to(CATALOG_PATH)

    with pytest.raises(ValueError, match="软链接"):
        module.load_case_catalog(link)


def test_catalog_rejects_invalid_utf8(tmp_path: Path) -> None:
    module = _load_catalog_module()
    path = tmp_path / "catalog.yaml"
    path.write_bytes(b"\xff\xfe")

    with pytest.raises(ValueError, match="UTF-8"):
        module.load_case_catalog(path)
