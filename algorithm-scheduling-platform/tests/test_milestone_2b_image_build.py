from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_ROOT = PLATFORM_ROOT / "deploy"
SCRIPTS_ROOT = DEPLOY_ROOT / "scripts"

EXPECTED_MATRIX = (
    ("asr_offline", "docker/Dockerfile", "seacraft-asr-offline"),
    ("asr_online", "docker/Dockerfile", "seacraft-asr-online"),
    ("ocr", "docker/Dockerfile", "algorithm-ocr"),
    ("vbas", "docker/Dockerfile", "algorithm-vbas"),
    ("facerec", "docker/Dockerfile", "algorithm-facerec"),
    ("screen_det", "docker/Dockerfile", "algorithm-screen-det"),
    ("ppt_slice", "Dockerfile", "algorithm-ppt-slice"),
    ("text_analysis", "Dockerfile", "algorithm-text-analysis"),
)

SAFE_DOCKERIGNORE = """\
.git
tests/
test/
__pycache__/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.codex/
harness/
openspec/
.env
*.pem
*.key
*.p12
*.pfx
"""


def _write_executable(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    platform = workspace / "algorithm-scheduling-platform"
    scripts = platform / "deploy" / "scripts"
    scripts.mkdir(parents=True)
    for name in ("build-images", "verify-operator-build-contexts"):
        shutil.copy2(SCRIPTS_ROOT / name, scripts / name)
    shutil.copy2(DEPLOY_ROOT / "operator-images.tsv", platform / "deploy/operator-images.tsv")
    wheel_script = platform / "scripts/build_and_stage_operator_registry_wheel.py"
    wheel_script.parent.mkdir(parents=True)
    wheel_script.write_text("raise SystemExit('test stub must not execute')\n", encoding="utf-8")
    for context, dockerfile, _ in EXPECTED_MATRIX:
        project = workspace / context
        (project / dockerfile).parent.mkdir(parents=True, exist_ok=True)
        (project / dockerfile).write_text(
            "FROM scratch\nCOPY app/ /app/\n", encoding="utf-8"
        )
        (project / "app").mkdir()
        (project / "app/main.py").write_text("app = object()\n", encoding="utf-8")
        (project / ".dockerignore").write_text(SAFE_DOCKERIGNORE, encoding="utf-8")
    return workspace


def _install_build_stubs(fake_bin: Path) -> None:
    _write_executable(
        fake_bin / "df",
        """#!/usr/bin/env bash
state="${DF_STATE}"
count=0
[[ ! -f "$state" ]] || count="$(cat "$state")"
count=$((count + 1))
printf '%s' "$count" > "$state"
available=$((200 * 1024 * 1024))
if [[ "${DF_FAIL_CALL:-0}" == "$count" ]]; then available=1; fi
printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\n'
printf '/dev/root 999999999 1 %s 1%% /\n' "$available"
""",
    )
    _write_executable(
        fake_bin / "git",
        """#!/usr/bin/env bash
if [[ "$1" == "-C" && "$3" == "rev-parse" && "$4" == "HEAD" ]]; then
  printf '%s\n' "${GIT_SHA}"
  exit 0
fi
exit 64
""",
    )
    _write_executable(
        fake_bin / "python3",
        f"""#!{sys.executable}
import json, os, sys
if sys.argv[1:2] and sys.argv[1].endswith("verify-operator-build-contexts"):
    os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])
with open(os.environ["COMMAND_LOG"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps(["python3", *sys.argv[1:]]) + "\\n")
raise SystemExit(int(os.environ.get("WHEEL_EXIT", "0")))
""",
    )
    _write_executable(
        fake_bin / "docker",
        f"""#!{sys.executable}
import json, os, sys
args = sys.argv[1:]
with open(os.environ["COMMAND_LOG"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps(["docker", *args]) + "\\n")
if args[:1] == ["build"]:
    image = args[args.index("--tag") + 1]
    raise SystemExit(1 if image == os.environ.get("FAIL_BUILD_REF") else 0)
if args[:2] == ["image", "inspect"]:
    image = args[-1]
    format_value = args[args.index("--format") + 1]
    if "RepoTags" in format_value:
        print(os.environ.get("INSPECT_REPO_TAG", image))
    elif "revision" in format_value:
        print(os.environ.get("INSPECT_REVISION", os.environ["GIT_SHA"]))
    else:
        raise SystemExit(64)
    raise SystemExit(0)
raise SystemExit(64)
""",
    )


def _environment(fake_bin: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "COMMAND_LOG": str(fake_bin / "commands.jsonl"),
            "DF_STATE": str(fake_bin / "df-count"),
            "GIT_SHA": "a" * 40,
            "MIN_ROOT_FREE_GIB": "100",
        }
    )
    return environment


def _commands(environment: dict[str, str]) -> list[list[str]]:
    path = Path(environment["COMMAND_LOG"])
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _run_build(
    workspace: Path,
    cwd: Path,
    environment: dict[str, str],
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(
                workspace
                / "algorithm-scheduling-platform/deploy/scripts/build-images"
            ),
            *arguments,
        ],
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def _run_gate(workspace: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(
                workspace
                / "algorithm-scheduling-platform/deploy/scripts/verify-operator-build-contexts"
            )
        ],
        cwd=workspace / "asr_offline",
        text=True,
        capture_output=True,
        check=False,
    )


def test_operator_image_manifest_matches_the_frozen_matrix() -> None:
    rows = tuple(
        tuple(line.split("\t"))
        for line in (DEPLOY_ROOT / "operator-images.tsv")
        .read_text(encoding="utf-8")
        .splitlines()
        if line and not line.startswith("#")
    )

    assert rows == EXPECTED_MATRIX


def test_build_images_runs_from_arbitrary_cwd_and_verifies_every_image(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    _install_build_stubs(fake_bin)
    environment = _environment(fake_bin)
    unrelated_cwd = tmp_path / "unrelated"
    unrelated_cwd.mkdir()

    completed = _run_build(workspace, unrelated_cwd, environment)

    assert completed.returncode == 0, completed.stderr
    commands = _commands(environment)
    assert commands[0] == ["python3", "scripts/build_and_stage_operator_registry_wheel.py"]
    builds = [command for command in commands if command[:2] == ["docker", "build"]]
    assert len(builds) == 8
    for build, (context, dockerfile, image) in zip(builds, EXPECTED_MATRIX, strict=True):
        assert build == [
            "docker",
            "build",
            "--file",
            str(workspace / context / dockerfile),
            "--tag",
            f"{image}:v1.0_260812",
            "--label",
            f"org.opencontainers.image.revision={'a' * 40}",
            str(workspace / context),
        ]
    assert sum(command[:3] == ["docker", "image", "inspect"] for command in commands) == 16
    assert (fake_bin / "df-count").read_text(encoding="utf-8") == "9"


def test_build_images_accepts_one_explicit_version_tag(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    _install_build_stubs(fake_bin)
    environment = _environment(fake_bin)

    completed = _run_build(workspace, tmp_path, environment, "v1.0_260813")

    assert completed.returncode == 0, completed.stderr
    builds = [
        command for command in _commands(environment) if command[:2] == ["docker", "build"]
    ]
    assert all(command[command.index("--tag") + 1].endswith(":v1.0_260813") for command in builds)


@pytest.mark.parametrize(
    "arguments",
    [("latest",), ("v1.0_260813", "extra")],
)
def test_build_images_rejects_invalid_or_multiple_tag_arguments(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    workspace = _make_workspace(tmp_path)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    _install_build_stubs(fake_bin)
    environment = _environment(fake_bin)

    completed = _run_build(workspace, tmp_path, environment, *arguments)

    assert completed.returncode != 0
    assert _commands(environment) == []


@pytest.mark.parametrize(
    ("environment_overrides", "expected_build_count"),
    [
        ({"DF_FAIL_CALL": "1"}, 0),
        ({"WHEEL_EXIT": "1"}, 0),
        ({"DF_FAIL_CALL": "3"}, 1),
        ({"FAIL_BUILD_REF": "algorithm-ocr:v1.0_260812"}, 3),
        ({"INSPECT_REVISION": "b" * 40}, 1),
        ({"INSPECT_REPO_TAG": "unexpected:v1"}, 1),
    ],
)
def test_build_images_stops_at_the_first_failed_gate_or_verification(
    tmp_path: Path,
    environment_overrides: dict[str, str],
    expected_build_count: int,
) -> None:
    workspace = _make_workspace(tmp_path)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    _install_build_stubs(fake_bin)
    environment = _environment(fake_bin)
    environment.update(environment_overrides)

    completed = _run_build(workspace, tmp_path, environment)

    assert completed.returncode != 0
    builds = [
        command for command in _commands(environment) if command[:2] == ["docker", "build"]
    ]
    assert len(builds) == expected_build_count
    assert not any(
        "prune" in argument
        for command in _commands(environment)
        for argument in command
    )


def test_build_context_gate_rejects_a_matrix_drift(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    manifest = workspace / "algorithm-scheduling-platform/deploy/operator-images.tsv"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "asr_offline\tdocker/Dockerfile", ".\tasr_offline/docker/Dockerfile"
        ),
        encoding="utf-8",
    )

    completed = _run_gate(workspace)

    assert completed.returncode != 0
    assert "matrix" in completed.stderr.lower()


@pytest.mark.parametrize(
    ("dockerfile_source", "dockerignore_source", "expected_error"),
    [
        ("FROM scratch\nCOPY ../shared /app\n", SAFE_DOCKERIGNORE, "outside"),
        ("FROM scratch\nCOPY /absolute /app\n", SAFE_DOCKERIGNORE, "outside"),
        ("FROM scratch\nCOPY app /app\n", ".git\ntests/\n", ".dockerignore"),
    ],
)
def test_build_context_gate_rejects_external_copy_or_pollution_gap(
    tmp_path: Path,
    dockerfile_source: str,
    dockerignore_source: str,
    expected_error: str,
) -> None:
    workspace = _make_workspace(tmp_path)
    (workspace / "asr_offline/docker/Dockerfile").write_text(
        dockerfile_source, encoding="utf-8"
    )
    (workspace / "asr_offline/.dockerignore").write_text(
        dockerignore_source, encoding="utf-8"
    )

    completed = _run_gate(workspace)

    assert completed.returncode != 0
    assert expected_error in completed.stderr.lower()


def test_build_context_gate_allows_stage_copy_and_multistage_copy(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    (workspace / "asr_offline/docker/Dockerfile").write_text(
        """FROM scratch AS builder
COPY wheel/${REGISTRY_WHEEL} /wheel/
FROM scratch
COPY --from=builder /wheel /wheel
""",
        encoding="utf-8",
    )

    completed = _run_gate(workspace)

    assert completed.returncode == 0, completed.stderr


def test_build_context_gate_rejects_a_secret_reincluded_by_negation(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    dockerignore = workspace / "asr_offline/.dockerignore"
    dockerignore.write_text(
        dockerignore.read_text(encoding="utf-8") + "!private/deploy.key\n",
        encoding="utf-8",
    )

    completed = _run_gate(workspace)

    assert completed.returncode != 0
    assert "re-include" in completed.stderr.lower()


@pytest.mark.parametrize(
    "negation",
    ("!*", "!**", "!**/*", "!private/**", "!**/*.key"),
)
def test_build_context_gate_rejects_broad_negations_that_can_restore_pollution(
    tmp_path: Path, negation: str
) -> None:
    workspace = _make_workspace(tmp_path)
    dockerignore = workspace / "asr_offline/.dockerignore"
    dockerignore.write_text(
        dockerignore.read_text(encoding="utf-8") + f"{negation}\n",
        encoding="utf-8",
    )

    completed = _run_gate(workspace)

    assert completed.returncode != 0
    assert "re-include" in completed.stderr.lower()


@pytest.mark.parametrize(
    "source",
    (
        "https://example.invalid/archive.tar.gz",
        "http://example.invalid/file.txt",
        "git@github.com:example/private.git",
        "git://example.invalid/repository.git",
    ),
)
def test_build_context_gate_rejects_remote_add_sources(
    tmp_path: Path, source: str
) -> None:
    workspace = _make_workspace(tmp_path)
    (workspace / "asr_offline/docker/Dockerfile").write_text(
        f"FROM scratch\nADD {source} /app/\n", encoding="utf-8"
    )

    completed = _run_gate(workspace)

    assert completed.returncode != 0
    assert "remote" in completed.stderr.lower()


def test_build_context_gate_rejects_an_included_test_media_file(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    (workspace / "asr_offline/fixture.mp4").write_bytes(b"not-a-real-video")

    completed = _run_gate(workspace)

    assert completed.returncode != 0
    assert "fixture.mp4" in completed.stderr


def test_build_context_gate_allows_a_test_media_file_when_ignored(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    (workspace / "asr_offline/fixture.mp4").write_bytes(b"not-a-real-video")
    dockerignore = workspace / "asr_offline/.dockerignore"
    dockerignore.write_text(
        dockerignore.read_text(encoding="utf-8") + "*.mp4\n", encoding="utf-8"
    )

    completed = _run_gate(workspace)

    assert completed.returncode == 0, completed.stderr


def test_build_context_gate_applies_negations_in_order_to_real_files(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    (workspace / "asr_offline/fixture.mp4").write_bytes(b"not-a-real-video")
    dockerignore = workspace / "asr_offline/.dockerignore"
    dockerignore.write_text(
        dockerignore.read_text(encoding="utf-8") + "*.mp4\n!fixture.mp4\n",
        encoding="utf-8",
    )

    completed = _run_gate(workspace)

    assert completed.returncode != 0
    assert "fixture.mp4" in completed.stderr


def test_real_facerec_and_ppt_contexts_exclude_private_or_large_local_inputs() -> None:
    workspace_root = PLATFORM_ROOT.parent
    facerec_ignore = (workspace_root / "facerec/.dockerignore").read_text(encoding="utf-8")
    ppt_ignore = (workspace_root / "ppt_slice/.dockerignore").read_text(encoding="utf-8")

    assert "media/" in facerec_ignore
    assert "config.toml" in facerec_ignore
    for entry in ("harness/", "openspec/", ".codex/"):
        assert entry in facerec_ignore
        assert entry in ppt_ignore


def test_asr_offline_keeps_model_hotword_wav_in_the_build_context() -> None:
    dockerignore = (PLATFORM_ROOT.parent / "asr_offline/.dockerignore").read_text(
        encoding="utf-8"
    )

    assert "*.wav" in dockerignore
    assert "!model/**/*.wav" in dockerignore
