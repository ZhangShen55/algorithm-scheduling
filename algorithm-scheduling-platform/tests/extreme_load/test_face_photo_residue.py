from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from scripts.extreme_load import face_photo_residue
from scripts.extreme_load.catalog import (
    CampaignCatalog,
    CampaignPhase,
    CaseSpec,
    FixtureDescriptor,
    FixtureKind,
    FixtureManifest,
)
from scripts.extreme_load.face_photo_residue import SshFacePhotoResidueAdapter
from scripts.extreme_load.media_download import CommandResult
from scripts.extreme_load.plan import CampaignPlan, build_campaign_plan
from scripts.extreme_load.production_adapters import (
    RUNTIME_CONFIG_ENV,
    face_photo_residue_factory,
)

_PHOTO_SHA256 = "f" * 64
_CONTAINER_IDS = {
    "facerec-gpu0": "a" * 64,
    "facerec-gpu1": "b" * 64,
    "facerec-gpu2": "c" * 64,
}
_MONGODB_ID = "d" * 64


def _remote_document(**updates: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": 1,
        "facerec_container_count": 3,
        "facerec_identity_verified_count": 3,
        "container_photo_paths_observed": 3,
        "container_photo_paths_existing": 0,
        "container_photo_regular_files": 0,
        "container_photo_symlinks": 0,
        "container_photo_forbidden_digest_matches": 0,
        "log_paths_observed": 3,
        "log_paths_existing": 3,
        "log_regular_files": 9,
        "log_symlinks": 0,
        "log_sensitive_marker_files": 0,
        "log_forbidden_digest_matches": 0,
        "mongodb_identity_verified": True,
        "person_document_count": 5000,
        "feature_document_count": 5000,
        "nonempty_photo_path_count": 0,
        "forbidden_photo_field_count": 0,
        "persistent_paths_observed": 1,
        "persistent_paths_existing": 1,
        "persistent_regular_files": 100,
        "persistent_symlinks": 0,
        "persistent_person_photo_named_files": 0,
        "persistent_forbidden_digest_matches": 0,
    }
    document.update(updates)
    return document


class RecordingRunner:
    def __init__(self, document: dict[str, object] | None = None) -> None:
        self.document = document or _remote_document()
        self.calls: list[tuple[tuple[str, ...], bytes, float]] = []

    async def run(
        self,
        argv: Sequence[str],
        *,
        stdin: bytes,
        timeout_seconds: float,
    ) -> CommandResult:
        self.calls.append((tuple(argv), stdin, timeout_seconds))
        return CommandResult(0, json.dumps(self.document).encode(), b"")


def _adapter(runner: RecordingRunner, *, enabled: bool = True) -> SshFacePhotoResidueAdapter:
    return SshFacePhotoResidueAdapter(
        target_hostname="192.168.29.11",
        ssh_user="root",
        ssh_port=22,
        facerec_compose_project="algorithm-operators",
        facerec_container_ids=_CONTAINER_IDS,
        mongodb_compose_project="algorithm-scheduling-platform",
        mongodb_compose_service="mongodb",
        mongodb_container_id=_MONGODB_ID,
        mongodb_database="facerecapi",
        mongodb_collection="persons",
        container_photo_paths=("/app/media/person_photos",),
        container_log_paths=("/app/logs",),
        persistent_paths=("/data/result",),
        fixture_id="person-photo",
        fixture_evidence_id="external-face-fixture-1",
        person_photo_sha256=_PHOTO_SHA256,
        person_photo_size_bytes=1024,
        enabled=enabled,
        command_runner=runner,
        probe_timeout_seconds=120,
    )


@pytest.mark.asyncio
async def test_ssh_probe_uses_strict_identity_and_publishes_only_aggregate_counts() -> None:
    runner = RecordingRunner()

    result = await _adapter(runner).run()

    assert result.status == "passed"
    argv, stdin, timeout = runner.calls[0]
    assert "BatchMode=yes" in argv
    assert "StrictHostKeyChecking=yes" in argv
    assert timeout == 120
    assert all(container_id not in " ".join(argv) for container_id in _CONTAINER_IDS.values())
    assert _MONGODB_ID not in " ".join(argv)
    payload = json.loads(stdin)
    assert payload["facerec_container_ids"] == _CONTAINER_IDS
    assert payload["mongodb_container_id"] == _MONGODB_ID
    assert payload["person_photo_sha256"] == _PHOTO_SHA256

    rendered = json.dumps(result.to_evidence(), sort_keys=True)
    assert result.to_evidence()["fixture_evidence_id"] == "external-face-fixture-1"
    assert all(container_id not in rendered for container_id in _CONTAINER_IDS.values())
    assert _MONGODB_ID not in rendered
    assert _PHOTO_SHA256 not in rendered
    assert "file_name" not in rendered
    assert "data:image" not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("updates", "reason"),
    (
        ({"log_paths_existing": 2}, "观察不完整"),
        ({"container_photo_regular_files": 1}, "发现人脸原图残留"),
        ({"log_sensitive_marker_files": 1}, "发现人脸原图残留"),
        ({"nonempty_photo_path_count": 1}, "发现人脸原图残留"),
        ({"persistent_forbidden_digest_matches": 1}, "发现人脸原图残留"),
        ({"feature_document_count": 4999}, "人物特征观察缺失"),
    ),
)
async def test_any_missing_observation_or_residue_fails_closed(
    updates: dict[str, object],
    reason: str,
) -> None:
    result = await _adapter(RecordingRunner(_remote_document(**updates))).run()

    assert result.status == "failed"
    assert reason in result.reason


@pytest.mark.asyncio
async def test_disabled_probe_never_constructs_ssh() -> None:
    runner = RecordingRunner()

    result = await _adapter(runner, enabled=False).run()

    assert result.status == "failed"
    assert "未显式启用" in result.reason
    assert runner.calls == []


def _case() -> CaseSpec:
    return CaseSpec(
        case_id="FACE-PHOTO-RESIDUE",
        phase=CampaignPhase.ONLINE,
        load={"kind": "face_photo_residue"},
        fixture_ids=("person-photo",),
        expected="no residue",
        timeout_seconds=900,
        guardrails=("evidence",),
        cleanup=("stop_new_load",),
        evidence_path="campaign/phase-3-online/face-photo-residue.json",
    )


def _plan(tmp_path: Path) -> CampaignPlan:
    fixture = FixtureDescriptor(
        fixture_id="person-photo",
        kind=FixtureKind.PERSON_PHOTO,
        path=str(tmp_path / "person-photo.png"),
        size_bytes=1024,
        sha256=_PHOTO_SHA256,
    )
    return build_campaign_plan(
        release_tag="release-1",
        git_sha="e" * 40,
        seed=5,
        control_origin="http://192.168.29.11:18100",
        gateway_origin="http://192.168.29.11:18103",
        fixture_manifest=FixtureManifest(schema_version=1, fixtures=(fixture,)),
        catalog=CampaignCatalog(schema_version=1, cases=(_case(),)),
    )


def _runtime_config(tmp_path: Path, *, enabled: bool = True, sha256: str = _PHOTO_SHA256) -> Path:
    path = tmp_path / "face-runtime.toml"
    path.write_text(
        "\n".join(
            (
                "schema_version = 1",
                "[face_photo_residue]",
                f"enabled = {'true' if enabled else 'false'}",
                'target_hostname = "192.168.29.11"',
                'ssh_user = "root"',
                "ssh_port = 22",
                "probe_timeout_seconds = 120",
                'fixture_evidence_id = "external-face-fixture-1"',
                f'person_photo_sha256 = "{sha256}"',
                "person_photo_size_bytes = 1024",
                'facerec_compose_project = "algorithm-operators"',
                'mongodb_compose_project = "algorithm-scheduling-platform"',
                'mongodb_compose_service = "mongodb"',
                f'mongodb_container_id = "{_MONGODB_ID}"',
                'mongodb_database = "facerecapi"',
                'mongodb_collection = "persons"',
                'container_photo_paths = ["/app/media/person_photos"]',
                'container_log_paths = ["/app/logs"]',
                'persistent_paths = ["/data/result"]',
                "[face_photo_residue.facerec_container_ids]",
                *(
                    f'{service} = "{container_id}"'
                    for service, container_id in _CONTAINER_IDS.items()
                ),
            )
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


@pytest.mark.asyncio
async def test_production_factory_requires_external_0600_opt_in_and_manifest_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path)
    runner = RecordingRunner()
    monkeypatch.setattr(face_photo_residue, "AsyncSubprocessRunner", lambda: runner)

    disabled = _runtime_config(tmp_path, enabled=False)
    monkeypatch.setenv(RUNTIME_CONFIG_ENV, str(disabled))
    blocked = await face_photo_residue_factory(plan, tmp_path / "release").execute(_case())
    assert blocked.status == "blocked"
    assert "enabled=true" in blocked.reason
    assert runner.calls == []

    enabled = _runtime_config(tmp_path, enabled=True)
    monkeypatch.setenv(RUNTIME_CONFIG_ENV, str(enabled))
    outcome = await face_photo_residue_factory(plan, tmp_path / "release").execute(_case())
    assert outcome.status == "passed"
    assert runner.calls

    mismatch = _runtime_config(tmp_path, enabled=True, sha256="0" * 64)
    monkeypatch.setenv(RUNTIME_CONFIG_ENV, str(mismatch))
    rejected = await face_photo_residue_factory(plan, tmp_path / "release").execute(_case())
    assert rejected.status == "blocked"
    assert rejected.evidence == {"configuration_state": "fixture_mismatch"}
