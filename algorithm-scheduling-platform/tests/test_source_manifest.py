from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "scripts" / "source_manifest.py"
SPEC = importlib.util.spec_from_file_location("source_manifest", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
source_manifest = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = source_manifest
SPEC.loader.exec_module(source_manifest)

REVISION = "a" * 40


def _source(root: Path, logical: str):
    return source_manifest.SourceRoot(root.resolve(), source_manifest.PurePosixPath(logical))


def test_manifest_is_deterministic_and_verifies_runtime_files(tmp_path: Path) -> None:
    app = tmp_path / "app"
    packages = tmp_path / "packages"
    app.mkdir()
    packages.mkdir()
    (app / "main.py").write_text("APP = 1\n", encoding="utf-8")
    (packages / "contract.py").write_text("VERSION = 1\n", encoding="utf-8")
    sources = [_source(app, "app"), _source(packages, "packages")]

    first = source_manifest.build_manifest(sources, REVISION)
    second = source_manifest.build_manifest(list(reversed(sources)), REVISION)
    assert first == second

    manifest = tmp_path / "manifest.json"
    digest = tmp_path / "manifest.sha256"
    source_manifest.write_manifest(manifest, first)
    digest.write_text(hashlib.sha256(manifest.read_bytes()).hexdigest() + "\n", encoding="ascii")
    source_manifest.verify_manifest(
        sources=sources,
        revision=REVISION,
        manifest_path=manifest,
        digest_path=digest,
    )


@pytest.mark.parametrize("failure", ["tampered_source", "missing_manifest", "wrong_revision"])
def test_manifest_fails_closed_for_unverifiable_sources(tmp_path: Path, failure: str) -> None:
    app = tmp_path / "app"
    app.mkdir()
    managed = app / "main.py"
    managed.write_text("APP = 1\n", encoding="utf-8")
    sources = [_source(app, "app")]
    manifest = tmp_path / "manifest.json"
    digest = tmp_path / "manifest.sha256"
    source_manifest.write_manifest(
        manifest,
        source_manifest.build_manifest(sources, REVISION),
    )
    digest.write_text(hashlib.sha256(manifest.read_bytes()).hexdigest() + "\n", encoding="ascii")

    revision = REVISION
    if failure == "tampered_source":
        managed.write_text("APP = 2\n", encoding="utf-8")
    elif failure == "missing_manifest":
        manifest.unlink()
    else:
        revision = "b" * 40

    with pytest.raises(source_manifest.SourceManifestError):
        source_manifest.verify_manifest(
            sources=sources,
            revision=revision,
            manifest_path=manifest,
            digest_path=digest,
        )


def test_manifest_rejects_revision_label_with_old_checkout(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    (app / "main.py").write_text("old source\n", encoding="utf-8")
    embedded = tmp_path / "embedded.json"
    digest = tmp_path / "embedded.sha256"
    source_manifest.write_manifest(
        embedded,
        source_manifest.build_manifest([_source(app, "app")], REVISION),
    )
    digest.write_text(hashlib.sha256(embedded.read_bytes()).hexdigest() + "\n", encoding="ascii")

    expected_app = tmp_path / "expected-app"
    expected_app.mkdir()
    (expected_app / "main.py").write_text("new source\n", encoding="utf-8")
    expected = tmp_path / "expected.json"
    source_manifest.write_manifest(
        expected,
        source_manifest.build_manifest([_source(expected_app, "app")], REVISION),
    )

    with pytest.raises(source_manifest.SourceManifestError, match="目标 Git checkout"):
        source_manifest.verify_manifest(
            sources=[_source(app, "app")],
            revision=REVISION,
            manifest_path=embedded,
            digest_path=digest,
            expected_manifest_path=expected,
        )
