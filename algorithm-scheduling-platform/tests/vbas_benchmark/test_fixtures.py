from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.vbas_benchmark.fixtures import (
    build_batch_payload,
    load_manifest,
    validate_manifest_files,
)
from scripts.vbas_benchmark.models import BenchmarkMode, FixtureEntry, FixtureManifest


def _manifest() -> FixtureManifest:
    return FixtureManifest(
        1,
        ({"url_sha256": "a" * 64},),
        (
            FixtureEntry("teacher/a.jpg", 10, 1920, 1080, "teacher", "a" * 64),
            FixtureEntry("student/b.jpg", 20, 1920, 1080, "student", "b" * 64),
            FixtureEntry("shared/empty.jpg", 5, 1920, 1080, "empty", "c" * 64),
        ),
    )


def test_mixed_payload_uses_deterministic_teacher_student_sequence() -> None:
    first_stream, first = build_batch_payload(
        manifest=_manifest(),
        fixture_root_in_container=Path("/data/course/benchmark"),
        mode=BenchmarkMode.MIXED,
        batch_size=2,
        sequence=0,
        seed=7,
    )
    second_stream, second = build_batch_payload(
        manifest=_manifest(),
        fixture_root_in_container=Path("/data/course/benchmark"),
        mode=BenchmarkMode.MIXED,
        batch_size=2,
        sequence=1,
        seed=7,
    )
    repeated_stream, repeated = build_batch_payload(
        manifest=_manifest(),
        fixture_root_in_container=Path("/data/course/benchmark"),
        mode=BenchmarkMode.MIXED,
        batch_size=2,
        sequence=0,
        seed=7,
    )

    assert first_stream == repeated_stream == "teacher"
    assert second_stream == "student"
    assert first == repeated
    assert first["ReturnHeadPose"] is False
    assert "ReturnHeadPose" not in second
    assert all(
        str(item["StoragePath"]).startswith("/data/course/benchmark/")
        for item in first["ImageList"]
    )


def test_manifest_file_validation_detects_content_change(tmp_path: Path) -> None:
    image = tmp_path / "frame.jpg"
    image.write_bytes(b"original")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_videos": [],
                "entries": [
                    {
                        "relative_path": "frame.jpg",
                        "byte_size": len(b"original"),
                        "width": 1,
                        "height": 1,
                        "category": "teacher",
                        "sha256": hashlib.sha256(b"original").hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = load_manifest(manifest_path)
    validate_manifest_files(manifest, tmp_path)
    image.write_bytes(b"changed!")

    with pytest.raises(ValueError, match="SHA-256"):
        validate_manifest_files(manifest, tmp_path)


def test_manifest_rejects_path_escape() -> None:
    with pytest.raises(ValueError, match="不逃逸"):
        FixtureEntry("../secret.jpg", 1, 1, 1, "student", "a" * 64)
