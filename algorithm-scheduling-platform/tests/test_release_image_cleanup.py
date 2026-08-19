from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from deploy.scripts import release_image_cleanup as cleanup

TAG = "v1.0_260819"
SHA = "a" * 40
OLD_SHA = "b" * 40
OLD_IMAGE = "sha256:" + "1" * 64
CURRENT_IMAGE = "sha256:" + "2" * 64
REFERENCED_IMAGE = "sha256:" + "3" * 64
UNPROVEN_IMAGE = "sha256:" + "4" * 64


def _release_root(tmp_path: Path) -> Path:
    root = tmp_path / "reports" / "releases" / TAG / SHA
    (root / "registration").mkdir(parents=True)
    (root / "smoke").mkdir()
    (root / "preflight").mkdir()
    registration = {
        "evidence_type": "operator_registration",
        "status": "通过",
        "release_tag": TAG,
        "git_sha": SHA,
        "selection": {"mode": "full"},
        "summary": {"valid": 24},
        "validated_instances": [{"instance_id": str(index)} for index in range(24)],
    }
    (root / "registration/operator-registration.json").write_text(
        json.dumps(registration), encoding="utf-8"
    )
    for operator in cleanup.SMOKE_OPERATORS:
        (root / "smoke" / f"{operator}.json").write_text(
            json.dumps(
                {
                    "evidence_type": "operator_smoke",
                    "operator_code": operator,
                    "status": "PASS",
                    "mock": False,
                    "release_tag": TAG,
                    "git_sha": SHA,
                }
            ),
            encoding="utf-8",
        )
    snapshot = {
        "evidence_type": "release_image_inventory_before",
        "status": "PASS",
        "release_tag": TAG,
        "git_sha": SHA,
        "images": [
            {
                "image_id": OLD_IMAGE,
                "revision": OLD_SHA,
                "size_bytes": 100,
                "repo_tags": ["algorithm-old:v1"],
                "compose_slots": ["ocr-gpu0"],
                "container_references": [],
            },
            {
                "image_id": CURRENT_IMAGE,
                "revision": SHA,
                "size_bytes": 200,
                "repo_tags": ["algorithm-current:v1"],
                "compose_slots": ["ocr-gpu1"],
                "container_references": [],
            },
            {
                "image_id": REFERENCED_IMAGE,
                "revision": OLD_SHA,
                "size_bytes": 300,
                "repo_tags": ["algorithm-referenced:v1"],
                "compose_slots": ["ocr-gpu2"],
                "container_references": [],
            },
            {
                "image_id": UNPROVEN_IMAGE,
                "revision": None,
                "size_bytes": 400,
                "repo_tags": [],
                "compose_slots": ["vbas-gpu0"],
                "container_references": [],
            },
        ],
    }
    (root / "preflight/image-inventory-before.json").write_text(
        json.dumps(snapshot), encoding="utf-8"
    )
    return root


def _patch_runtime(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    commands: list[list[str]] = []
    references = {
        REFERENCED_IMAGE: ["c" * 64],
    }
    monkeypatch.setattr(cleanup, "_controlled_containers", lambda *_a, **_k: [])
    monkeypatch.setattr(
        cleanup,
        "_inventory",
        lambda *_a, **_k: [
            {
                "image_id": CURRENT_IMAGE,
                "revision": SHA,
                "size_bytes": 200,
                "repo_tags": ["algorithm-current:v1"],
                "compose_slots": ["ocr-gpu1"],
                "container_references": [],
            }
        ],
    )
    monkeypatch.setattr(cleanup, "_all_container_references", lambda: references)
    inspections = [
        [
            {
                "Id": OLD_IMAGE,
                "Size": 100,
                "Config": {"Labels": {cleanup.REVISION_LABEL: OLD_SHA}},
            }
        ],
        [],
    ]
    monkeypatch.setattr(
        cleanup,
        "_inspect_images",
        lambda *_a, **_k: inspections.pop(0),
    )

    def fake_run(command: list[str], *, check: bool = True) -> Any:
        del check
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(cleanup, "_run", fake_run)
    return commands


def test_cleanup_executes_only_exact_unreferenced_proven_old_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _release_root(tmp_path)
    commands = _patch_runtime(monkeypatch)

    result = cleanup._cleanup(root, tmp_path, TAG, SHA, execute=True)

    assert result["status"] == "PASS"
    assert result["candidate_image_ids"] == [OLD_IMAGE]
    assert result["deleted"] == [
        {"image_id": OLD_IMAGE, "revision": OLD_SHA, "size_bytes": 100}
    ]
    assert result["declared_reclaimed_bytes"] == 100
    assert ["docker", "image", "rm", OLD_IMAGE] in commands
    assert not any("-f" in command or "prune" in command for command in commands)
    reasons = {item["image_id"]: item["reason"] for item in result["skipped"]}
    assert reasons[CURRENT_IMAGE] == "current release still uses this image"
    assert reasons[REFERENCED_IMAGE] == "image is still referenced by a container"
    assert reasons[UNPROVEN_IMAGE] == "old release revision cannot be proven"


def test_cleanup_dry_run_never_deletes_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _release_root(tmp_path)
    commands = _patch_runtime(monkeypatch)

    result = cleanup._cleanup(root, tmp_path, TAG, SHA, execute=False)

    assert result["mode"] == "DRY_RUN"
    assert result["candidate_image_ids"] == [OLD_IMAGE]
    assert result["deleted"] == []
    assert commands == []


def test_cleanup_rejects_incomplete_smoke_before_runtime_or_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _release_root(tmp_path)
    (root / "smoke/ocr.json").unlink()
    called = False

    def controlled(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(cleanup, "_controlled_containers", controlled)

    with pytest.raises(cleanup.ImageCleanupError, match="smoke"):
        cleanup._cleanup(root, tmp_path, TAG, SHA, execute=True)

    assert called is False


def test_snapshot_uses_compose_image_slots_without_running_operator_containers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _release_root(tmp_path)
    platform_image = "sha256:" + "5" * 64
    operator_image = "sha256:" + "6" * 64
    monkeypatch.setattr(
        cleanup,
        "_controlled_image_slots",
        lambda _root: {
            "control-service": "algorithm-control:old",
            "ocr-gpu0": "algorithm-ocr:old",
            "ocr-gpu1": "algorithm-ocr:old",
        },
    )
    monkeypatch.setattr(
        cleanup,
        "_controlled_containers",
        lambda *_args, **_kwargs: pytest.fail(
            "构建前快照不得要求旧算子容器正在运行"
        ),
    )
    monkeypatch.setattr(
        cleanup,
        "_all_container_references",
        lambda: {platform_image: ["d" * 64]},
    )

    def inspect(images: list[str], *, check: bool = True) -> list[dict[str, Any]]:
        del check
        assert len(images) == 1
        image_id = platform_image if images[0].endswith("control:old") else operator_image
        return [
            {
                "Id": image_id,
                "Size": 100 if image_id == platform_image else 200,
                "RepoTags": [images[0]],
                "Config": {"Labels": {cleanup.REVISION_LABEL: OLD_SHA}},
            }
        ]

    monkeypatch.setattr(cleanup, "_inspect_images", inspect)

    result = cleanup._snapshot(root, tmp_path, TAG, SHA)

    assert result["status"] == "PASS"
    by_id = {item["image_id"]: item for item in result["images"]}
    assert by_id[platform_image]["compose_slots"] == ["control-service"]
    assert by_id[platform_image]["container_references"] == ["d" * 64]
    assert by_id[operator_image]["compose_slots"] == ["ocr-gpu0", "ocr-gpu1"]
    assert by_id[operator_image]["container_references"] == []


def test_release_image_cleanup_source_has_no_force_or_broad_prune() -> None:
    source = Path(cleanup.__file__).read_text(encoding="utf-8")

    assert '["docker", "image", "rm", image_id]' in source
    assert '"docker", "system", "prune"' not in source
    assert '"docker", "image", "rm", "-f"' not in source
