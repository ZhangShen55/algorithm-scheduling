from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PLATFORM_ROOT.parent
SCRIPTS = PLATFORM_ROOT / "deploy" / "scripts"
ASSET_DEFINITION = PLATFORM_ROOT / "deploy" / "model-assets.json"
ASSET_TARGETS = {
    "asr_offline/model": "speech_fsmn_vad_zh-cn-16k-common-pytorch/model.pt",
    "asr_online/model": "speech_paraformer_asr_nat-zh-cn-16k-common-vocab8404-online/model.pt.enc",
    "ocr/models": "PP-OCRv6_medium_det/inference.pdiparams",
    "vbas/models": "teacher.pt",
    "facerec/ai_models": "shape_predictor_68_face_landmarks.dat",
    "screen_det/model": "screen.pt",
}


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_asset_source(
    source: Path,
    *,
    omitted_target: str | None = None,
    extra_file: tuple[str, str] | None = None,
) -> dict[str, bytes]:
    payloads: dict[str, bytes] = {}
    assets: list[dict[str, Any]] = []
    for target, sentinel in ASSET_TARGETS.items():
        if target == omitted_target:
            continue
        payload = f"fixture:{target}".encode()
        path = source / target / sentinel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        payloads[f"{target}/{sentinel}"] = payload
        files = [{"path": sentinel, "bytes": len(payload), "sha256": _sha256(payload)}]
        definition = json.loads(ASSET_DEFINITION.read_text(encoding="utf-8"))
        required = next(
            item["required_sentinels"]
            for item in definition["assets"]
            if item["target"] == target
        )
        for required_path in required:
            if required_path == sentinel:
                continue
            required_payload = f"fixture:{target}:{required_path}".encode()
            required_file = source / target / required_path
            required_file.parent.mkdir(parents=True, exist_ok=True)
            required_file.write_bytes(required_payload)
            payloads[f"{target}/{required_path}"] = required_payload
            files.append(
                {
                    "path": required_path,
                    "bytes": len(required_payload),
                    "sha256": _sha256(required_payload),
                }
            )
        if extra_file is not None and extra_file[0] == target:
            extra_path = source / target / extra_file[1]
            extra_path.parent.mkdir(parents=True, exist_ok=True)
            extra_payload = b"unexpected"
            extra_path.write_bytes(extra_payload)
        assets.append({"target": target, "files": files})
    (source / "model-assets.manifest.json").write_text(
        json.dumps({"schema_version": 1, "assets": assets}, ensure_ascii=False),
        encoding="utf-8",
    )
    return payloads


def _run(
    script: str,
    source: Path,
    workspace: Path,
    *,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(SCRIPTS / script),
            "--source",
            str(source),
            "--workspace",
            str(workspace),
        ],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )


def _run_secret_verifier(
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPTS / "verify-runtime-secrets"), *arguments],
        text=True,
        capture_output=True,
        check=False,
    )


def _run_manifest_generator(
    source: Path, workspace: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(SCRIPTS / "generate-model-asset-manifest"),
            "--source",
            str(source),
            "--workspace",
            str(workspace),
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "destination"
    (workspace / "algorithm-scheduling-platform/deploy").mkdir(parents=True)
    (workspace / "algorithm-scheduling-platform/deploy/model-assets.json").write_bytes(
        ASSET_DEFINITION.read_bytes()
    )
    return workspace


def test_asset_definition_freezes_six_actual_plain_model_roots() -> None:
    definition = json.loads(ASSET_DEFINITION.read_text(encoding="utf-8"))

    assert definition["schema_version"] == 1
    assert {item["target"] for item in definition["assets"]} == set(ASSET_TARGETS)
    serialized = ASSET_DEFINITION.read_text(encoding="utf-8").lower()
    assert "models-encrypted" not in serialized
    assert "secret" not in serialized
    assert "sha256" not in serialized


def test_stager_copies_and_verifier_validates_exact_manifest(tmp_path: Path) -> None:
    source = tmp_path / "external-assets"
    expected = _write_asset_source(source)
    workspace = _make_workspace(tmp_path)

    staged = _run("stage-model-assets", source, workspace)
    verified = _run("verify-model-assets", source, workspace)

    assert staged.returncode == 0, staged.stderr
    assert verified.returncode == 0, verified.stderr
    assert "sha256" not in staged.stdout.lower()
    for relative_path, payload in expected.items():
        assert (workspace / relative_path).read_bytes() == payload


def test_manifest_generator_freezes_all_external_regular_files(tmp_path: Path) -> None:
    source = tmp_path / "external-assets"
    expected = _write_asset_source(source)
    (source / "model-assets.manifest.json").unlink()
    workspace = _make_workspace(tmp_path)

    completed = _run_manifest_generator(source, workspace)

    assert completed.returncode == 0, completed.stderr
    manifest = json.loads(
        (source / "model-assets.manifest.json").read_text(encoding="utf-8")
    )
    actual = {
        f"{asset['target']}/{entry['path']}": (entry["bytes"], entry["sha256"])
        for asset in manifest["assets"]
        for entry in asset["files"]
    }
    assert actual == {
        path: (len(payload), _sha256(payload)) for path, payload in expected.items()
    }
    assert "sha256" not in completed.stdout.lower()


@pytest.mark.parametrize("pollution", [".DS_Store", "__pycache__/model.pyc"])
def test_manifest_generator_rejects_model_source_pollution(
    tmp_path: Path, pollution: str
) -> None:
    source = tmp_path / "external-assets"
    _write_asset_source(source)
    (source / "model-assets.manifest.json").unlink()
    path = source / "vbas/models" / pollution
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"pollution")
    workspace = _make_workspace(tmp_path)

    completed = _run_manifest_generator(source, workspace)

    assert completed.returncode != 0
    assert "pollution" in completed.stderr.lower()
    assert not (source / "model-assets.manifest.json").exists()


def test_stager_is_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "external-assets"
    _write_asset_source(source)
    workspace = _make_workspace(tmp_path)

    first = _run("stage-model-assets", source, workspace)
    before = {
        path.relative_to(workspace).as_posix(): path.stat().st_ino
        for target in ASSET_TARGETS
        for path in (workspace / target).rglob("*")
        if path.is_file()
    }
    second = _run("stage-model-assets", source, workspace)
    after = {
        path.relative_to(workspace).as_posix(): path.stat().st_ino
        for target in ASSET_TARGETS
        for path in (workspace / target).rglob("*")
        if path.is_file()
    }

    assert first.returncode == second.returncode == 0
    assert before == after


@pytest.mark.parametrize("script", ["stage-model-assets", "verify-model-assets"])
def test_asset_commands_reject_source_inside_git_worktree(
    tmp_path: Path, script: str
) -> None:
    workspace = _make_workspace(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    source = workspace / "local-assets"
    _write_asset_source(source)

    completed = _run(script, source, workspace)

    assert completed.returncode != 0
    assert "worktree" in completed.stderr.lower() or "workspace" in completed.stderr.lower()


def test_stager_rejects_a_tampered_journal_without_touching_external_paths(
    tmp_path: Path,
) -> None:
    source = tmp_path / "external-assets"
    _write_asset_source(source)
    workspace = _make_workspace(tmp_path)
    external = tmp_path / "must-remain"
    external.mkdir()
    marker = external / "marker"
    marker.write_text("preserve", encoding="utf-8")
    (workspace / ".model-assets-transaction.json").write_text(
        json.dumps(
            {
                "phase": "prepared",
                "transaction_id": "a" * 32,
                "entries": [
                    {
                        "target": "asr_offline/model",
                        "stage": str(external),
                        "backup": str(external),
                        "had_original": True,
                        "switched": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    completed = _run("stage-model-assets", source, workspace)

    assert completed.returncode != 0
    assert "journal" in completed.stderr.lower()
    assert marker.read_text(encoding="utf-8") == "preserve"


@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_stager_rejects_symlink_and_special_file(tmp_path: Path, kind: str) -> None:
    source = tmp_path / "external-assets"
    _write_asset_source(source)
    target = source / "screen_det/model/screen.pt"
    target.unlink()
    if kind == "symlink":
        target.symlink_to(source / "asr_offline/model/model.bin")
    else:
        os.mkfifo(target)
    workspace = _make_workspace(tmp_path)

    completed = _run("stage-model-assets", source, workspace)

    assert completed.returncode != 0
    assert kind in completed.stderr.lower() or "regular" in completed.stderr.lower()


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [("missing_root", "missing"), ("extra_file", "extra"), ("hash", "hash")],
)
def test_stager_rejects_incomplete_extra_or_tampered_assets_without_changing_targets(
    tmp_path: Path, mutation: str, expected: str
) -> None:
    source = tmp_path / "external-assets"
    if mutation == "missing_root":
        _write_asset_source(source, omitted_target="ocr/models")
    elif mutation == "extra_file":
        _write_asset_source(source, extra_file=("ocr/models", "unlisted.bin"))
    else:
        payloads = _write_asset_source(source)
        original = payloads[
            "ocr/models/PP-OCRv6_medium_det/inference.pdiparams"
        ]
        (source / "ocr/models/PP-OCRv6_medium_det/inference.pdiparams").write_bytes(
            b"x" * len(original)
        )
    workspace = _make_workspace(tmp_path)
    for target in ASSET_TARGETS:
        old = workspace / target / "old.bin"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_bytes(b"old")

    completed = _run("stage-model-assets", source, workspace)

    assert completed.returncode != 0
    assert expected in completed.stderr.lower()
    for target in ASSET_TARGETS:
        assert (workspace / target / "old.bin").read_bytes() == b"old"


@pytest.mark.parametrize(
    "forbidden_path",
    ["private.key", "model.pem", "models-encrypted/screen.pt.enc"],
)
def test_manifest_rejects_secret_or_encrypted_asset_metadata(
    tmp_path: Path, forbidden_path: str
) -> None:
    source = tmp_path / "external-assets"
    _write_asset_source(source)
    manifest_path = source / "model-assets.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload = b"must-not-leak"
    path = source / "screen_det/model" / forbidden_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    manifest["assets"][-1]["files"].append(
        {"path": forbidden_path, "bytes": len(payload), "sha256": _sha256(payload)}
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    workspace = _make_workspace(tmp_path)

    completed = _run("stage-model-assets", source, workspace)

    assert completed.returncode != 0
    combined = completed.stdout + completed.stderr
    assert payload.decode() not in combined
    assert _sha256(payload) not in combined


@pytest.mark.parametrize("interrupt_stage", ["after_backup", "after_replace", "after_journal"])
def test_interrupted_switch_recovers_without_mixed_model_roots(
    tmp_path: Path, interrupt_stage: str
) -> None:
    source = tmp_path / "external-assets"
    expected = _write_asset_source(source)
    workspace = _make_workspace(tmp_path)
    for target in ASSET_TARGETS:
        old = workspace / target / "old.bin"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_bytes(f"old:{target}".encode())
    environment = os.environ.copy()
    environment["MODEL_ASSET_TEST_INTERRUPT_AT"] = f"{interrupt_stage}:2"

    interrupted = _run(
        "stage-model-assets", source, workspace, environment=environment
    )
    resumed = _run("stage-model-assets", source, workspace)

    assert interrupted.returncode != 0
    assert resumed.returncode == 0, resumed.stderr
    for target in ASSET_TARGETS:
        assert not (workspace / target / "old.bin").exists()
    for relative_path, payload in expected.items():
        assert (workspace / relative_path).read_bytes() == payload
    assert not (workspace / ".model-assets-transaction.json").exists()


def test_copy_phase_failure_leaves_old_roots_and_no_transaction_artifacts(
    tmp_path: Path,
) -> None:
    source = tmp_path / "external-assets"
    _write_asset_source(source)
    workspace = _make_workspace(tmp_path)
    for target in ASSET_TARGETS:
        old = workspace / target / "old.bin"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_bytes(f"old:{target}".encode())
    environment = os.environ.copy()
    environment["MODEL_ASSET_TEST_FAIL_AFTER_STAGES"] = "2"

    completed = _run(
        "stage-model-assets", source, workspace, environment=environment
    )

    assert completed.returncode != 0
    for target in ASSET_TARGETS:
        assert (workspace / target / "old.bin").read_bytes() == f"old:{target}".encode()
    assert not (workspace / ".model-assets-transaction.json").exists()
    assert not list(workspace.glob("**/.*.model-stage-*"))
    assert not list(workspace.glob("**/.*.model-backup-*"))


def test_interrupted_switch_restores_an_originally_missing_root(tmp_path: Path) -> None:
    source = tmp_path / "external-assets"
    expected = _write_asset_source(source)
    workspace = _make_workspace(tmp_path)
    environment = os.environ.copy()
    environment["MODEL_ASSET_TEST_INTERRUPT_AT"] = "after_replace:2"

    interrupted = _run(
        "stage-model-assets", source, workspace, environment=environment
    )
    resumed = _run("stage-model-assets", source, workspace)

    assert interrupted.returncode != 0
    assert resumed.returncode == 0, resumed.stderr
    for relative_path, payload in expected.items():
        assert (workspace / relative_path).read_bytes() == payload


def test_verifier_detects_destination_tampering(tmp_path: Path) -> None:
    source = tmp_path / "external-assets"
    payloads = _write_asset_source(source)
    workspace = _make_workspace(tmp_path)
    assert _run("stage-model-assets", source, workspace).returncode == 0
    original = payloads["ocr/models/PP-OCRv6_medium_det/inference.pdiparams"]
    (workspace / "ocr/models/PP-OCRv6_medium_det/inference.pdiparams").write_bytes(
        b"x" * len(original)
    )

    completed = _run("verify-model-assets", source, workspace)

    assert completed.returncode != 0
    assert "hash" in completed.stderr.lower()


def test_runtime_secret_verifier_checks_metadata_without_leaking_secret(
    tmp_path: Path,
) -> None:
    secret = tmp_path / "vbas-model.key"
    payload = b"sensitive-runtime-material"
    secret.write_bytes(payload)
    secret.chmod(0o600)

    completed = _run_secret_verifier(
        "--secret", f"vbas_model_key=/run/secrets/tias_model_key={secret}"
    )

    assert completed.returncode == 0, completed.stderr
    combined = completed.stdout + completed.stderr
    assert "vbas_model_key" in combined
    assert "/run/secrets/tias_model_key" in combined
    assert payload.decode() not in combined
    assert _sha256(payload) not in combined
    assert str(len(payload)) not in combined


@pytest.mark.parametrize("bad_mode", [0o644, 0o400])
def test_runtime_secret_verifier_requires_regular_0600_file(
    tmp_path: Path, bad_mode: int
) -> None:
    secret = tmp_path / "runtime.key"
    secret.write_bytes(b"not-printed")
    secret.chmod(bad_mode)

    completed = _run_secret_verifier(
        "--secret", f"model_key=/run/secrets/model_key={secret}"
    )

    assert completed.returncode != 0
    assert "0600" in completed.stderr


def test_runtime_secret_verifier_accepts_no_secrets_for_plain_model_mode() -> None:
    completed = _run_secret_verifier()

    assert completed.returncode == 0, completed.stderr
    assert "no runtime secrets requested" in completed.stdout.lower()
