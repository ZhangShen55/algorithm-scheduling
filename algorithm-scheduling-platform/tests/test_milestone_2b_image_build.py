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
    ("facerec", "docker/Dockerfile", "algorithm-facerec"),
    ("ocr", "docker/Dockerfile", "algorithm-ocr"),
    ("ppt_slice", "Dockerfile", "algorithm-ppt-slice"),
    ("screen_det", "docker/Dockerfile", "algorithm-screen-det"),
    ("vbas", "docker/Dockerfile", "algorithm-vbas"),
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
wheel/*.whl
!wheel/algorithm_operator_registry_client-0.2.0-py3-none-any.whl
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
    for name in (
        "build-images",
        "deployment_contracts.py",
        "verify-operator-build-contexts",
        "verify-model-assets",
        "model_asset_transaction.py",
        "operator_topology.py",
    ):
        shutil.copy2(SCRIPTS_ROOT / name, scripts / name)
    shutil.copy2(DEPLOY_ROOT / "operator-images.tsv", platform / "deploy/operator-images.tsv")
    shutil.copy2(DEPLOY_ROOT / "model-assets.json", platform / "deploy/model-assets.json")
    shutil.copy2(DEPLOY_ROOT / "operator-topology.json", platform / "deploy/operator-topology.json")
    wheel_script = platform / "scripts/build_and_stage_operator_registry_wheel.py"
    wheel_script.parent.mkdir(parents=True)
    wheel_script.write_text("raise SystemExit('test stub must not execute')\n", encoding="utf-8")
    for context, dockerfile, _ in EXPECTED_MATRIX:
        project = workspace / context
        (project / dockerfile).parent.mkdir(parents=True, exist_ok=True)
        (project / dockerfile).write_text(
            "FROM scratch\n"
            "COPY app/ /app/\n"
            "COPY wheel/algorithm_operator_registry_client-0.2.0-py3-none-any.whl "
            "/tmp/client.whl\n",
            encoding="utf-8",
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
if [[ "$1" == "-C" && ("$3" == "diff" || "$3" == "ls-files") ]]; then
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
if sys.argv[1:2] and sys.argv[1].endswith("deployment_contracts.py"):
    os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])
if sys.argv[1:2] == ["-"]:
    target, payload = sys.argv[2:]
    tags = json.loads(payload)
    raise SystemExit(0 if isinstance(tags, list) and target in tags else 1)
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
        extra = json.loads(os.environ.get("INSPECT_EXTRA_REPO_TAGS", "[]"))
        print(os.environ.get("INSPECT_REPO_TAGS", json.dumps([*extra, image])))
    elif "revision" in format_value:
        print(os.environ.get("INSPECT_REVISION", os.environ["GIT_SHA"]))
    elif "Architecture" in format_value:
        print(os.environ.get("INSPECT_ARCHITECTURE", "amd64"))
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
            "EXPECTED_GIT_SHA": "a" * 40,
            "MIN_ROOT_FREE_GIB": "100",
            "MODEL_ASSET_SOURCE": str(fake_bin / "external-model-assets"),
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


def _run_gate(
    workspace: Path, *arguments: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(
                workspace
                / "algorithm-scheduling-platform/deploy/scripts/verify-operator-build-contexts"
            ),
            *arguments,
        ],
        cwd=workspace / "asr_offline",
        text=True,
        capture_output=True,
        check=False,
    )


def _initialize_git_workspace(workspace: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "config", "user.email", "tests@example.invalid"],
        cwd=workspace,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Build Gate Tests"],
        cwd=workspace,
        check=True,
    )
    subprocess.run(["git", "add", "-f", "."], cwd=workspace, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "test fixture"], cwd=workspace, check=True
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
    assert commands[0][0] == "python3"
    assert commands[0][1].endswith("model_asset_transaction.py")
    assert commands[0][2] == "verify"
    assert commands[1] == ["python3", "scripts/build_and_stage_operator_registry_wheel.py"]
    builds = [command for command in commands if command[:2] == ["docker", "build"]]
    assert len(builds) == 7
    for build, (context, dockerfile, image) in zip(builds, EXPECTED_MATRIX, strict=True):
        expected_build = [
            "docker",
            "build",
            "--file",
            str(workspace / context / dockerfile),
            "--tag",
            f"{image}:v1.0_260812",
            "--label",
            f"org.opencontainers.image.revision={'a' * 40}",
        ]
        if context == "ocr":
            assert build[len(expected_build)] == "--secret"
            secret = build[len(expected_build) + 1]
            assert secret.startswith("id=ocr_model_manifest,src=")
            secret_path = Path(secret.removeprefix("id=ocr_model_manifest,src="))
            assert not secret_path.exists()
            expected_build.extend(["--secret", secret])
        expected_build.append(str(workspace / context))
        assert build == expected_build
    assert sum("--secret" in build for build in builds) == 1
    assert sum(command[:3] == ["docker", "image", "inspect"] for command in commands) == 21
    assert (fake_bin / "df-count").read_text(encoding="utf-8") == "8"


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


def test_build_images_requires_expected_git_sha(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    _install_build_stubs(fake_bin)
    environment = _environment(fake_bin)
    environment.pop("EXPECTED_GIT_SHA")

    completed = _run_build(workspace, tmp_path, environment)

    assert completed.returncode != 0
    assert "EXPECTED_GIT_SHA" in completed.stderr
    assert _commands(environment) == []


def test_build_images_requires_an_external_model_asset_source(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    _install_build_stubs(fake_bin)
    environment = _environment(fake_bin)
    environment.pop("MODEL_ASSET_SOURCE")

    completed = _run_build(workspace, tmp_path, environment)

    assert completed.returncode != 0
    assert "MODEL_ASSET_SOURCE" in completed.stderr
    assert not any(command[:2] == ["docker", "build"] for command in _commands(environment))


def test_build_images_rejects_expected_git_sha_that_differs_from_head(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    _install_build_stubs(fake_bin)
    environment = _environment(fake_bin)
    environment["EXPECTED_GIT_SHA"] = "b" * 40

    completed = _run_build(workspace, tmp_path, environment)

    assert completed.returncode != 0
    assert "does not match" in completed.stderr
    assert not any(command[:2] == ["docker", "build"] for command in _commands(environment))


@pytest.mark.parametrize(
    ("environment_overrides", "expected_build_count"),
    [
        ({"DF_FAIL_CALL": "1"}, 0),
        ({"WHEEL_EXIT": "1"}, 0),
        ({"DF_FAIL_CALL": "3"}, 1),
        ({"FAIL_BUILD_REF": "algorithm-ocr:v1.0_260812"}, 4),
        ({"INSPECT_REVISION": "b" * 40}, 1),
        ({"INSPECT_REPO_TAGS": '["unexpected:v1"]'}, 1),
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


def test_build_images_accepts_target_tag_when_it_is_not_first_in_repo_tags(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    _install_build_stubs(fake_bin)
    environment = _environment(fake_bin)
    environment["INSPECT_EXTRA_REPO_TAGS"] = json.dumps(["unrelated:old"])

    completed = _run_build(workspace, tmp_path, environment)

    assert completed.returncode == 0, completed.stderr


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


def test_build_context_gate_allows_only_ocr_example_config_reinclude(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    dockerignore = workspace / "ocr/.dockerignore"
    dockerignore.write_text(
        dockerignore.read_text(encoding="utf-8")
        + "config.toml\n!config.toml.example\n",
        encoding="utf-8",
    )
    (workspace / "ocr/config.toml.example").write_text(
        '[ocr]\ndevice = "cpu"\n', encoding="utf-8"
    )
    (workspace / "ocr/docker/Dockerfile").write_text(
        "FROM scratch\nCOPY config.toml.example /app/.build/config.toml\n",
        encoding="utf-8",
    )

    completed = _run_gate(workspace)

    assert completed.returncode == 0, completed.stderr


def test_build_context_gate_rejects_ocr_runtime_config_reinclude(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    dockerignore = workspace / "ocr/.dockerignore"
    dockerignore.write_text(
        dockerignore.read_text(encoding="utf-8") + "config*.toml\n!config.toml\n",
        encoding="utf-8",
    )

    completed = _run_gate(workspace)

    assert completed.returncode != 0
    assert "re-include" in completed.stderr.lower()


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


@pytest.mark.parametrize(
    "dockerfile_source",
    (
        "FROM scratch\nCOPY\t../outside\t/app/\n",
        '# escape=`\nFROM scratch\nCOPY ../outside `\n  /app/\n',
        'FROM scratch\nCOPY --chown=1:1 ["../outside", "/app/"]\n',
        'FROM scratch\nADD --checksum=sha256:abc ["https://example.invalid/a", "/"]\n',
    ),
)
def test_build_context_gate_fails_closed_for_complex_external_sources(
    tmp_path: Path, dockerfile_source: str
) -> None:
    workspace = _make_workspace(tmp_path)
    (workspace / "asr_offline/docker/Dockerfile").write_text(
        dockerfile_source, encoding="utf-8"
    )

    completed = _run_gate(workspace)

    assert completed.returncode != 0


@pytest.mark.parametrize(
    "dockerfile_source",
    (
        "FROM scratch AS builder\nCOPY app /app\nFROM scratch\nCOPY --from=builder /app /app\n",
        "FROM scratch AS builder\nCOPY app /app\nFROM scratch\nCOPY --from builder /app /app\n",
    ),
)
def test_build_context_gate_accepts_both_multistage_from_flag_forms(
    tmp_path: Path, dockerfile_source: str
) -> None:
    workspace = _make_workspace(tmp_path)
    (workspace / "asr_offline/docker/Dockerfile").write_text(
        dockerfile_source, encoding="utf-8"
    )

    completed = _run_gate(workspace)

    assert completed.returncode == 0, completed.stderr


def test_git_input_gate_rejects_dirty_included_tracked_file(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _initialize_git_workspace(workspace)
    (workspace / "asr_offline/app/main.py").write_text("dirty = True\n", encoding="utf-8")

    completed = _run_gate(workspace, "--verify-git-clean-included")

    assert completed.returncode != 0
    assert "asr_offline/app/main.py" in completed.stderr


def test_git_input_gate_rejects_dirty_dockerfile_even_when_context_excludes_it(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    dockerfile = workspace / "asr_offline/docker/Dockerfile"
    dockerignore = workspace / "asr_offline/.dockerignore"
    dockerignore.write_text(
        dockerignore.read_text(encoding="utf-8") + "docker/Dockerfile\n",
        encoding="utf-8",
    )
    _initialize_git_workspace(workspace)
    dockerfile.write_text("FROM scratch\n# dirty\n", encoding="utf-8")

    completed = _run_gate(workspace, "--verify-git-clean-included")

    assert completed.returncode != 0
    assert "asr_offline/docker/Dockerfile" in completed.stderr


def test_git_input_gate_allows_dirty_tracked_file_excluded_from_context(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    excluded = workspace / "asr_offline/tests/test_user_work.py"
    excluded.parent.mkdir()
    excluded.write_text("original\n", encoding="utf-8")
    _initialize_git_workspace(workspace)
    excluded.write_text("dirty but ignored\n", encoding="utf-8")

    completed = _run_gate(workspace, "--verify-git-clean-included")

    assert completed.returncode == 0, completed.stderr


def test_git_input_gate_rejects_an_untracked_included_source(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _initialize_git_workspace(workspace)
    (workspace / "asr_offline/app/untracked.py").write_text("value = 1\n", encoding="utf-8")

    completed = _run_gate(workspace, "--verify-git-clean-included")

    assert completed.returncode != 0
    assert "asr_offline/app/untracked.py" in completed.stderr


def test_git_input_gate_rejects_gitignored_untracked_included_source(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    (workspace / ".gitignore").write_text("*.generated.py\n", encoding="utf-8")
    _initialize_git_workspace(workspace)
    generated = workspace / "asr_offline/app/local.generated.py"
    generated.write_text("value = 1\n", encoding="utf-8")

    completed = _run_gate(workspace, "--verify-git-clean-included")

    assert completed.returncode != 0
    assert "asr_offline/app/local.generated.py" in completed.stderr


def test_git_input_gate_rejects_deleted_tracked_included_operator_file(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    _initialize_git_workspace(workspace)
    deleted = workspace / "asr_offline/app/main.py"
    deleted.unlink()

    completed = _run_gate(workspace, "--verify-git-clean-included")

    assert completed.returncode != 0
    assert "asr_offline/app/main.py" in completed.stderr


def test_git_input_gate_allows_deleted_tracked_excluded_operator_file(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    excluded = workspace / "asr_offline/tests/test_deleted.py"
    excluded.parent.mkdir()
    excluded.write_text("original\n", encoding="utf-8")
    _initialize_git_workspace(workspace)
    excluded.unlink()

    completed = _run_gate(workspace, "--verify-git-clean-included")

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    "relative_path",
    (
        "algorithm-scheduling-platform/packages/operator_registry_client/runtime.py",
        "algorithm-scheduling-platform/scripts/build_and_stage_operator_registry_wheel.py",
    ),
)
def test_git_input_gate_rejects_dirty_registry_wheel_input(
    tmp_path: Path, relative_path: str
) -> None:
    workspace = _make_workspace(tmp_path)
    path = workspace / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("original\n", encoding="utf-8")
    _initialize_git_workspace(workspace)
    path.write_text("dirty\n", encoding="utf-8")

    completed = _run_gate(workspace, "--verify-git-clean-included")

    assert completed.returncode != 0
    assert relative_path in completed.stderr


@pytest.mark.parametrize(
    "relative_path",
    (
        "algorithm-scheduling-platform/packages/operator_registry_client/runtime.py",
        "algorithm-scheduling-platform/packages/operator_registry_client/pyproject.toml",
    ),
)
def test_git_input_gate_rejects_deleted_registry_wheel_source(
    tmp_path: Path, relative_path: str
) -> None:
    workspace = _make_workspace(tmp_path)
    path = workspace / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("original\n", encoding="utf-8")
    _initialize_git_workspace(workspace)
    path.unlink()

    completed = _run_gate(workspace, "--verify-git-clean-included")

    assert completed.returncode != 0
    assert relative_path in completed.stderr


def test_git_input_gate_allows_a_foreign_wheel_only_when_dockerignore_excludes_it(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    _initialize_git_workspace(workspace)
    wheel = workspace / "asr_offline/wheel/pyarrow-20.0.0-py3-none-any.whl"
    wheel.parent.mkdir(exist_ok=True)
    wheel.write_bytes(b"foreign-wheel")

    completed = _run_gate(workspace, "--verify-git-clean-included")

    assert completed.returncode == 0, completed.stderr


def test_git_input_gate_allows_rotated_asr_logs_excluded_by_real_context(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    real_dockerignore = PLATFORM_ROOT.parent / "asr_offline/.dockerignore"
    (workspace / "asr_offline/.dockerignore").write_text(
        real_dockerignore.read_text(encoding="utf-8"), encoding="utf-8"
    )
    _initialize_git_workspace(workspace)
    rotated_log = workspace / "asr_offline/logs/asr_service.log.2026-08-14"
    rotated_log.parent.mkdir()
    rotated_log.write_text("rotated log\n", encoding="utf-8")

    completed = _run_gate(workspace, "--verify-git-clean-included")

    assert completed.returncode == 0, completed.stderr


def test_git_input_gate_allows_the_exact_registry_wheel(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _initialize_git_workspace(workspace)
    wheel = (
        workspace
        / "asr_offline/wheel/algorithm_operator_registry_client-0.2.0-py3-none-any.whl"
    )
    wheel.parent.mkdir(exist_ok=True)
    wheel.write_bytes(b"registry-wheel")

    completed = _run_gate(workspace, "--verify-git-clean-included")

    assert completed.returncode == 0, completed.stderr


def test_real_facerec_and_ppt_contexts_exclude_private_or_large_local_inputs() -> None:
    workspace_root = PLATFORM_ROOT.parent
    facerec_ignore = (workspace_root / "facerec/.dockerignore").read_text(encoding="utf-8")
    ppt_ignore = (workspace_root / "ppt_slice/.dockerignore").read_text(encoding="utf-8")

    assert "media/" in facerec_ignore
    assert "config*.toml" in facerec_ignore
    for entry in ("harness/", "openspec/", ".codex/"):
        assert entry in facerec_ignore
        assert entry in ppt_ignore


def test_asr_offline_keeps_model_hotword_wav_in_the_build_context() -> None:
    dockerignore = (PLATFORM_ROOT.parent / "asr_offline/.dockerignore").read_text(
        encoding="utf-8"
    )

    assert "*.wav" in dockerignore
    assert "!model/**/*.wav" in dockerignore


@pytest.mark.parametrize("operator_name", ("asr_offline", "asr_online"))
def test_asr_images_use_the_reachable_configurable_miniconda_mirror(
    operator_name: str,
) -> None:
    dockerfile = (
        PLATFORM_ROOT.parent / operator_name / "docker/Dockerfile"
    ).read_text(encoding="utf-8")

    assert (
        "ARG MINICONDA_BASE_URL=https://mirror.nju.edu.cn/anaconda/miniconda"
        in dockerfile
    )
    assert '"${MINICONDA_BASE_URL}/${MINICONDA_INSTALLER}"' in dockerfile
    assert "https://repo.anaconda.com/miniconda/${MINICONDA_INSTALLER}" not in dockerfile


def test_asr_online_build_import_uses_an_ephemeral_config() -> None:
    dockerfile = (
        PLATFORM_ROOT.parent / "asr_online/docker/Dockerfile"
    ).read_text(encoding="utf-8")

    assert "RUN touch /tmp/asr-online-build-config.toml" in dockerfile
    assert (
        "CONFIG_PATH=/tmp/asr-online-build-config.toml "
        'python -c "from app.main import app"'
        in dockerfile
    )
    assert "rm -f /tmp/asr-online-build-config.toml" in dockerfile


def test_screen_det_build_import_uses_an_ephemeral_config() -> None:
    dockerfile = (
        PLATFORM_ROOT.parent / "screen_det/docker/Dockerfile"
    ).read_text(encoding="utf-8")

    assert "touch /tmp/screen-det-build-config.toml" in dockerfile
    assert "CONFIG_PATH=/tmp/screen-det-build-config.toml" in dockerfile
    assert "rm -f /tmp/screen-det-build-config.toml" in dockerfile


def test_facerec_image_uses_a_configurable_resilient_pypi_source() -> None:
    dockerfile = (
        PLATFORM_ROOT.parent / "facerec/docker/Dockerfile"
    ).read_text(encoding="utf-8")

    assert "ARG PYPI_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple" in dockerfile
    assert (
        "ARG FASTDEPLOY_FIND_LINKS=https://www.paddlepaddle.org.cn/whl/fastdeploy.html"
        in dockerfile
    )
    assert dockerfile.count('--index-url "$PYPI_INDEX_URL"') == 2
    assert dockerfile.count('--find-links "$FASTDEPLOY_FIND_LINKS"') == 1
    assert dockerfile.count("--timeout 300") == 2
    assert dockerfile.count("--retries 10") == 2
    assert dockerfile.count("--prefer-binary") == 2
    assert "files.pythonhosted.org" not in dockerfile


def test_all_real_contexts_only_reinclude_the_exact_registry_wheel() -> None:
    workspace_root = PLATFORM_ROOT.parent
    for context_name, _, _ in EXPECTED_MATRIX:
        dockerignore = (workspace_root / context_name / ".dockerignore").read_text(
            encoding="utf-8"
        )
        assert "wheel/*.whl" in dockerignore
        assert (
            "!wheel/algorithm_operator_registry_client-0.2.0-py3-none-any.whl"
            in dockerignore
        )


def test_screen_det_image_contains_plain_models_but_excludes_encrypted_assets() -> None:
    workspace_root = PLATFORM_ROOT.parent
    dockerfile = (workspace_root / "screen_det/docker/Dockerfile").read_text(
        encoding="utf-8"
    )
    dockerignore = (workspace_root / "screen_det/.dockerignore").read_text(
        encoding="utf-8"
    )

    assert "COPY model/ ./model/" in dockerfile
    assert "model/" not in {
        line.strip()
        for line in dockerignore.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert "docker/models-encrypted/" in dockerignore
    assert "*.key" in dockerignore


def test_vbas_image_uses_only_plain_models_in_this_release() -> None:
    workspace_root = PLATFORM_ROOT.parent
    dockerfile = (workspace_root / "vbas/docker/Dockerfile").read_text(encoding="utf-8")
    dockerignore = (workspace_root / "vbas/.dockerignore").read_text(encoding="utf-8")

    assert "COPY ./models ./models" in dockerfile
    assert "COPY ./models-encrypted" not in dockerfile
    assert "!models-encrypted" not in dockerignore
    assert "models-encrypted/" in dockerignore
    assert "*.key" in dockerignore


def test_all_operator_contexts_exclude_local_runtime_configs() -> None:
    workspace_root = PLATFORM_ROOT.parent
    for context_name, _, _ in EXPECTED_MATRIX:
        dockerignore = (workspace_root / context_name / ".dockerignore").read_text(
            encoding="utf-8"
        )
        lines = {
            line.strip()
            for line in dockerignore.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        config_reincludes = {line for line in lines if line.startswith("!config")}
        if context_name == "ocr":
            assert "config.toml" in lines
            assert config_reincludes == {"!config.toml.example"}
        else:
            assert "config*.toml" in lines, context_name
            assert not config_reincludes, context_name


def test_dockerfiles_do_not_copy_local_runtime_configs() -> None:
    workspace_root = PLATFORM_ROOT.parent
    for context_name, dockerfile_name, _ in EXPECTED_MATRIX:
        source = (workspace_root / context_name / dockerfile_name).read_text(
            encoding="utf-8"
        )
        assert "COPY config.toml " not in source, context_name
        assert "COPY ./config.toml " not in source, context_name
        assert "COPY . /" not in source, context_name


def test_sensitive_local_content_remains_outside_operator_images() -> None:
    workspace_root = PLATFORM_ROOT.parent
    facerec_ignore = (workspace_root / "facerec/.dockerignore").read_text(
        encoding="utf-8"
    )
    ppt_ignore = (workspace_root / "ppt_slice/.dockerignore").read_text(
        encoding="utf-8"
    )

    assert "media/" in facerec_ignore
    assert "harness/" in ppt_ignore
    assert "test/" in ppt_ignore
    assert "*.mp4" in ppt_ignore
