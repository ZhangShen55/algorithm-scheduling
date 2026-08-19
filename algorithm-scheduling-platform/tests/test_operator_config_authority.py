from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from deploy.scripts import verify_operator_config_authority as authority

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PLATFORM_ROOT.parent


def test_config_authority_entrypoint_is_executable() -> None:
    path = PLATFORM_ROOT / "deploy/scripts/verify-operator-config-authority"

    assert path.is_file()
    assert os.access(path, os.X_OK)
    completed = subprocess.run(
        [str(path), "--help"],
        cwd=PLATFORM_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def _git_sha() -> str:
    return subprocess.run(
        ["git", "-C", str(WORKSPACE_ROOT), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def test_profile_matrix_covers_exactly_eight_operators() -> None:
    assert [profile.operator_code for profile in authority.OPERATOR_PROFILES] == [
        "asr_offline",
        "asr_online",
        "facerec",
        "ocr",
        "screen_det",
        "ppt_slice",
        "vbas",
        "text_analysis",
    ]
    assert [profile.default_capacity for profile in authority.OPERATOR_PROFILES] == [
        4,
        10,
        128,
        256,
        128,
        10,
        128,
        256,
    ]
    assert [profile.deploy_require_gpu for profile in authority.OPERATOR_PROFILES] == [
        True,
        True,
        True,
        True,
        True,
        False,
        True,
        False,
    ]
    assert [profile.local_config_name for profile in authority.OPERATOR_PROFILES] == [
        "config.toml",
        "config.toml",
        "config.example.toml",
        "config.toml.example",
        "config.toml",
        "config.toml",
        "config.toml",
        "config.example.toml",
    ]


def test_local_profiles_are_version_control_eligible() -> None:
    for profile in authority.OPERATOR_PROFILES:
        relative_path = Path(profile.project_directory) / profile.local_config_name
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(WORKSPACE_ROOT),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "--",
                str(relative_path),
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        assert completed.returncode == 0
        assert completed.stdout.splitlines() == [relative_path.as_posix()]


def test_real_workspace_configs_are_loaded_by_sixteen_child_processes() -> None:
    payload = authority.run_authority_probe(WORKSPACE_ROOT, git_sha=_git_sha())

    assert payload["status"] == "PASS"
    assert payload["operator_count"] == 8
    assert payload["process_count"] == 16
    assert payload["legacy_environment_names"] == sorted(
        authority.LEGACY_ENVIRONMENT
    )
    assert len(payload["results"]) == 16
    for row in payload["results"]:
        assert row["child_pid"] != os.getpid()
        assert row["legacy_environment_injected"] is True
        assert row["legacy_environment_names"] == sorted(
            authority.LEGACY_ENVIRONMENT
        )
        profile = next(
            item
            for item in authority.OPERATOR_PROFILES
            if item.operator_code == row["operator_code"]
        )
        assert row["settings"] == authority._expected_settings(profile, row["mode"])

    rendered = json.dumps(payload, ensure_ascii=False)
    assert "legacy-environment.invalid" not in rendered
    assert "901.5" not in rendered
    assert '"997"' not in rendered


def test_process_output_rejects_toml_values_that_do_not_match_contract() -> None:
    profile = authority.OPERATOR_PROFILES[0]
    completed = subprocess.CompletedProcess(
        args=[sys.executable],
        returncode=0,
        stdout=json.dumps(
            {
                "child_pid": 123,
                "config_path": str(WORKSPACE_ROOT / "asr_offline/config.toml"),
                "legacy_environment_injected": True,
                "legacy_environment_names": sorted(authority.LEGACY_ENVIRONMENT),
                "settings": {
                    **authority._expected_settings(profile, "root"),
                    "max_concurrent_requests": 997,
                },
            }
        )
        + "\n",
        stderr="",
    )

    with pytest.raises(authority.AuthorityProbeError, match="覆盖了 TOML"):
        authority._parse_child_output(
            completed,
            config_path=WORKSPACE_ROOT / "asr_offline/config.toml",
            expected_settings=authority._expected_settings(profile, "root"),
        )


def test_cli_publishes_and_reuses_same_sha_evidence(tmp_path: Path) -> None:
    output = tmp_path / "operator-config-authority.json"
    command = [
        str(PLATFORM_ROOT / "deploy/scripts/verify-operator-config-authority"),
        "--workspace-root",
        str(WORKSPACE_ROOT),
        "--git-sha",
        _git_sha(),
        "--output",
        str(output),
    ]

    first = subprocess.run(command, text=True, capture_output=True, check=False)
    original = output.read_bytes()
    second = subprocess.run(command, text=True, capture_output=True, check=False)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert output.read_bytes() == original
    assert output.stat().st_mode & 0o777 == 0o600
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "PASS"
    assert payload["process_count"] == 16


def test_cli_rejects_tampered_existing_evidence(tmp_path: Path) -> None:
    output = tmp_path / "operator-config-authority.json"
    output.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "evidence_type": "operator_config_authority",
                "status": "PASS",
                "git_sha": _git_sha(),
            }
        ),
        encoding="utf-8",
    )
    output.chmod(0o600)

    completed = subprocess.run(
        [
            str(PLATFORM_ROOT / "deploy/scripts/verify-operator-config-authority"),
            "--workspace-root",
            str(WORKSPACE_ROOT),
            "--git-sha",
            _git_sha(),
            "--output",
            str(output),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "字段不符合合同" in completed.stderr
