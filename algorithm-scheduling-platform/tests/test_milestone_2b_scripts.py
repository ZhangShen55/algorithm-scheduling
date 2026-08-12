from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLATFORM_ROOT / "deploy" / "scripts"
SCRIPT_NAMES = (
    "preflight",
    "snapshot-existing-containers",
    "pause-existing-containers",
    "restore-existing-containers",
)


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _base_environment(fake_bin: Path, **overrides: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "COMMAND_LOG": str(fake_bin / "commands.jsonl"),
            "DF_AVAILABLE_KIB": str(200 * 1024 * 1024),
            "GPU_OUTPUT": "0\n1\n2",
            "GIT_SHA": "a" * 40,
            "GIT_STATUS": "",
            "SS_OUTPUT": "",
            "DOCKER_PS_IDS": "",
            "DOCKER_INSPECT_FIXTURES": "{}",
        }
    )
    environment.update(overrides)
    return environment


def _install_preflight_stubs(fake_bin: Path) -> None:
    _write_executable(
        fake_bin / "uname",
        "#!/usr/bin/env bash\nprintf '%s\\n' \"${UNAME_VALUE:-x86_64}\"\n",
    )
    _write_executable(
        fake_bin / "df",
        """#!/usr/bin/env bash
printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\\n'
printf '/dev/root 999999999 1 %s 1%% /\\n' "${DF_AVAILABLE_KIB}"
""",
    )
    _write_executable(
        fake_bin / "git",
        """#!/usr/bin/env bash
case "$1 $2" in
  "status --porcelain") printf '%s' "${GIT_STATUS}" ;;
  "rev-parse HEAD") printf '%s\\n' "${GIT_SHA}" ;;
  *) exit 64 ;;
esac
""",
    )
    _write_executable(
        fake_bin / "ss",
        "#!/usr/bin/env bash\nprintf '%s' \"${SS_OUTPUT}\"\n",
    )
    _write_executable(
        fake_bin / "path-check",
        """#!/usr/bin/env bash
[[ "$1" != "${UNWRITABLE_PATH:-}" ]]
""",
    )
    _install_docker_stub(fake_bin)


def _install_docker_stub(fake_bin: Path) -> None:
    _write_executable(
        fake_bin / "docker",
        """#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
with open(os.environ["COMMAND_LOG"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps(["docker", *args]) + "\\n")

if args == ["version"]:
    raise SystemExit(int(os.environ.get("DOCKER_VERSION_EXIT", "0")))
if args == ["compose", "version"]:
    raise SystemExit(int(os.environ.get("COMPOSE_VERSION_EXIT", "0")))
if args[:1] == ["run"]:
    print(os.environ.get("GPU_OUTPUT", ""))
    raise SystemExit(int(os.environ.get("GPU_RUN_EXIT", "0")))
if args == ["ps", "-aq"]:
    print(os.environ.get("DOCKER_PS_IDS", ""))
    raise SystemExit(int(os.environ.get("DOCKER_PS_EXIT", "0")))
if args[:1] == ["inspect"] and len(args) == 2:
    fixtures = json.loads(os.environ.get("DOCKER_INSPECT_FIXTURES", "{}"))
    value = fixtures.get(args[1], "__FAIL__")
    if value == "__FAIL__":
        raise SystemExit(1)
    print(json.dumps(value if isinstance(value, list) else [value]))
    raise SystemExit(0)
if args[:1] == ["stop"]:
    if len(args) == 2 and args[1] == os.environ.get("STOP_FAIL_ID"):
        print("injected stop failure", file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(0)
if args[:1] == ["start"]:
    if len(args) == 2 and args[1] == os.environ.get("START_FAIL_ID"):
        print("injected start failure", file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(0)
raise SystemExit(64)
""",
    )


def _run(script: str, *arguments: Path | str, environment: dict[str, str]) -> Any:
    return subprocess.run(
        [str(SCRIPTS / script), *(str(argument) for argument in arguments)],
        cwd=PLATFORM_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def _commands(environment: dict[str, str]) -> list[list[str]]:
    path = Path(environment["COMMAND_LOG"])
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _inspect_record(
    container_id: str = "a" * 64,
    *,
    name: str = "existing-api",
    image_id: str = "sha256:image-1",
    state: str = "running",
    ports: dict[str, Any] | None = None,
    mounts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "Id": container_id,
        "Name": f"/{name}",
        "Image": image_id,
        "Config": {
            "Image": "registry.example/existing-api:v1",
            "Labels": {
                "com.docker.compose.project": "existing-project",
                "owner": "course-platform",
            },
        },
        "State": {"Status": state},
        "HostConfig": {
            "PortBindings": ports
            if ports is not None
            else {"8000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8000"}]},
            "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
        },
        "Mounts": mounts
        if mounts is not None
        else [
            {
                "Type": "bind",
                "Source": "/data/existing",
                "Destination": "/srv/data",
                "Mode": "rw",
                "RW": True,
                "Propagation": "rprivate",
            }
        ],
    }


def _snapshot_record(inspect: dict[str, Any]) -> dict[str, Any]:
    labels = inspect["Config"]["Labels"]
    mounts = [
        {
            "type": mount["Type"],
            "source": mount["Source"],
            "destination": mount["Destination"],
            "mode": mount["Mode"],
            "rw": mount["RW"],
            "propagation": mount["Propagation"],
        }
        for mount in inspect["Mounts"]
    ]
    return {
        "container_id": inspect["Id"],
        "name": inspect["Name"].removeprefix("/"),
        "image_ref": inspect["Config"]["Image"],
        "image_id": inspect["Image"],
        "state": inspect["State"]["Status"],
        "labels": labels,
        "ports": inspect["HostConfig"]["PortBindings"],
        "mounts": mounts,
        "restart_policy": inspect["HostConfig"]["RestartPolicy"],
        "compose_project": labels["com.docker.compose.project"],
    }


@pytest.fixture
def fake_bin(tmp_path: Path) -> Path:
    path = tmp_path / "bin"
    path.mkdir()
    _install_preflight_stubs(path)
    return path


def test_preflight_accepts_exactly_three_container_visible_gpus(
    fake_bin: Path, tmp_path: Path
) -> None:
    course = tmp_path / "course"
    result = tmp_path / "result"
    course.mkdir()
    result.mkdir()
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
        PREFLIGHT_WRITABLE_CHECK_BIN=str(fake_bin / "path-check"),
        REQUIRED_PORTS="18100 18101",
        EXPECTED_GIT_SHA="a" * 40,
    )

    completed = _run("preflight", environment=environment)

    assert completed.returncode == 0, completed.stderr
    assert "preflight: PASS" in completed.stdout
    assert any(command[1] == "run" and "--gpus" in command for command in _commands(environment))


def test_preflight_stops_before_docker_when_root_disk_is_below_threshold(
    fake_bin: Path,
) -> None:
    environment = _base_environment(fake_bin, DF_AVAILABLE_KIB=str(99 * 1024 * 1024))

    completed = _run("preflight", environment=environment)

    assert completed.returncode != 0
    assert "root disk" in completed.stderr
    assert _commands(environment) == []


def test_preflight_rejects_non_x86_64_before_using_docker(
    fake_bin: Path,
) -> None:
    environment = _base_environment(fake_bin, UNAME_VALUE="aarch64")

    completed = _run("preflight", environment=environment)

    assert completed.returncode != 0
    assert "x86_64" in completed.stderr
    assert _commands(environment) == []


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"DOCKER_VERSION_EXIT": "1"}, "Docker daemon"),
        ({"COMPOSE_VERSION_EXIT": "1"}, "Docker Compose"),
        ({"GPU_RUN_EXIT": "1"}, "NVIDIA container runtime"),
    ],
)
def test_preflight_rejects_unavailable_container_prerequisites(
    fake_bin: Path, overrides: dict[str, str], message: str
) -> None:
    environment = _base_environment(fake_bin, **overrides)

    completed = _run("preflight", environment=environment)

    assert completed.returncode != 0
    assert message in completed.stderr


@pytest.mark.parametrize("gpu_output", ["0\n1", "0\n1\n2\n3"])
def test_preflight_rejects_any_gpu_count_other_than_three(
    fake_bin: Path, tmp_path: Path, gpu_output: str
) -> None:
    environment = _base_environment(
        fake_bin,
        GPU_OUTPUT=gpu_output,
        COURSE_ROOT=str(tmp_path),
        RESULT_ROOT=str(tmp_path),
        REQUIRED_PORTS="",
    )

    completed = _run("preflight", environment=environment)

    assert completed.returncode != 0
    assert "exactly 3 GPUs" in completed.stderr


@pytest.mark.parametrize("directory_name", ["course", "result"])
def test_preflight_rejects_an_unwritable_required_directory(
    fake_bin: Path, tmp_path: Path, directory_name: str
) -> None:
    course = tmp_path / "course"
    result = tmp_path / "result"
    course.mkdir()
    result.mkdir()
    unwritable = course if directory_name == "course" else result
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
        PREFLIGHT_WRITABLE_CHECK_BIN=str(fake_bin / "path-check"),
        UNWRITABLE_PATH=str(unwritable),
        REQUIRED_PORTS="",
    )

    completed = _run("preflight", environment=environment)

    assert completed.returncode != 0
    assert str(unwritable) in completed.stderr


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"GIT_STATUS": " M tracked.txt\n"}, "working tree"),
        ({"EXPECTED_GIT_SHA": "b" * 40}, "EXPECTED_GIT_SHA"),
    ],
)
def test_preflight_rejects_dirty_or_unexpected_git_state(
    fake_bin: Path, tmp_path: Path, overrides: dict[str, str], message: str
) -> None:
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(tmp_path),
        RESULT_ROOT=str(tmp_path),
        REQUIRED_PORTS="",
        **overrides,
    )

    completed = _run("preflight", environment=environment)

    assert completed.returncode != 0
    assert message in completed.stderr


def test_preflight_rejects_an_unauthorized_required_port_occupant(
    fake_bin: Path, tmp_path: Path
) -> None:
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(tmp_path),
        RESULT_ROOT=str(tmp_path),
        REQUIRED_PORTS="18100 18101",
        SS_OUTPUT="LISTEN 0 128 0.0.0.0:18100 0.0.0.0:*\n",
    )

    completed = _run("preflight", environment=environment)

    assert completed.returncode != 0
    assert "18100" in completed.stderr
    assert "unauthorized" in completed.stderr


def test_snapshot_writes_a_complete_read_only_jsonl_record(
    fake_bin: Path, tmp_path: Path
) -> None:
    output = tmp_path / "snapshot.jsonl"
    inspect = _inspect_record()
    environment = _base_environment(
        fake_bin,
        DOCKER_PS_IDS=inspect["Id"],
        DOCKER_INSPECT_FIXTURES=json.dumps({inspect["Id"]: inspect}),
    )

    completed = _run("snapshot-existing-containers", output, environment=environment)

    assert completed.returncode == 0, completed.stderr
    assert json.loads(output.read_text(encoding="utf-8")) == _snapshot_record(inspect)
    assert _commands(environment) == [
        ["docker", "ps", "-aq"],
        ["docker", "inspect", inspect["Id"]],
    ]


def test_empty_snapshot_does_not_inspect_or_change_any_container(
    fake_bin: Path, tmp_path: Path
) -> None:
    output = tmp_path / "snapshot.jsonl"
    environment = _base_environment(fake_bin)

    completed = _run("snapshot-existing-containers", output, environment=environment)

    assert completed.returncode == 0, completed.stderr
    assert output.read_text(encoding="utf-8") == ""
    assert _commands(environment) == [["docker", "ps", "-aq"]]


def test_snapshot_fails_atomically_when_container_listing_fails(
    fake_bin: Path, tmp_path: Path
) -> None:
    output = tmp_path / "snapshot.jsonl"
    environment = _base_environment(fake_bin, DOCKER_PS_EXIT="1")

    completed = _run("snapshot-existing-containers", output, environment=environment)

    assert completed.returncode != 0
    assert not output.exists()


@pytest.mark.parametrize("payload", ["not-json\n", '{"container_id":"x"}\n'])
def test_pause_rejects_malformed_or_incomplete_snapshot_without_stopping(
    fake_bin: Path, tmp_path: Path, payload: str
) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    paused = tmp_path / "paused.jsonl"
    snapshot.write_text(payload, encoding="utf-8")
    environment = _base_environment(fake_bin, PAUSE_RECORD_PATH=str(paused))

    completed = _run("pause-existing-containers", snapshot, "x", environment=environment)

    assert completed.returncode != 0
    assert not any(command[1] == "stop" for command in _commands(environment))


def test_pause_rejects_a_malicious_container_id_before_calling_docker(
    fake_bin: Path, tmp_path: Path
) -> None:
    malicious = _snapshot_record(_inspect_record())
    malicious["container_id"] = "--all"
    snapshot = tmp_path / "snapshot.jsonl"
    snapshot.write_text(json.dumps(malicious) + "\n", encoding="utf-8")
    environment = _base_environment(fake_bin)

    completed = _run("pause-existing-containers", snapshot, "--all", environment=environment)

    assert completed.returncode != 0
    assert _commands(environment) == []


def test_pause_stops_only_the_explicit_snapshot_verified_container_id(
    fake_bin: Path, tmp_path: Path
) -> None:
    inspect = _inspect_record()
    snapshot = tmp_path / "snapshot.jsonl"
    paused = tmp_path / "paused.jsonl"
    snapshot.write_text(json.dumps(_snapshot_record(inspect)) + "\n", encoding="utf-8")
    environment = _base_environment(
        fake_bin,
        PAUSE_RECORD_PATH=str(paused),
        DOCKER_INSPECT_FIXTURES=json.dumps(
            {inspect["Id"]: inspect, inspect["Name"].removeprefix("/"): inspect}
        ),
    )

    completed = _run(
        "pause-existing-containers", snapshot, inspect["Name"].removeprefix("/"),
        environment=environment,
    )

    assert completed.returncode == 0, completed.stderr
    assert [command for command in _commands(environment) if command[1] == "stop"] == [
        ["docker", "stop", inspect["Id"]]
    ]
    assert json.loads(paused.read_text(encoding="utf-8")) == {
        "container_id": inspect["Id"],
        "name": inspect["Name"].removeprefix("/"),
        "action": "stopped",
    }


def test_pause_rejects_name_reuse_without_stopping(fake_bin: Path, tmp_path: Path) -> None:
    original = _inspect_record()
    reused = _inspect_record(container_id="replacement-id")
    snapshot = tmp_path / "snapshot.jsonl"
    snapshot.write_text(json.dumps(_snapshot_record(original)) + "\n", encoding="utf-8")
    environment = _base_environment(
        fake_bin,
        DOCKER_INSPECT_FIXTURES=json.dumps(
            {original["Id"]: original, original["Name"].removeprefix("/"): reused}
        ),
    )

    completed = _run("pause-existing-containers", snapshot, "existing-api", environment=environment)

    assert completed.returncode != 0
    assert "name reuse" in completed.stderr
    assert not any(command[1] == "stop" for command in _commands(environment))


def test_pause_rejects_state_drift_without_claiming_it_stopped_the_container(
    fake_bin: Path, tmp_path: Path
) -> None:
    original = _inspect_record()
    externally_stopped = _inspect_record(state="exited")
    snapshot = tmp_path / "snapshot.jsonl"
    paused = tmp_path / "paused.jsonl"
    snapshot.write_text(json.dumps(_snapshot_record(original)) + "\n", encoding="utf-8")
    environment = _base_environment(
        fake_bin,
        PAUSE_RECORD_PATH=str(paused),
        DOCKER_INSPECT_FIXTURES=json.dumps(
            {
                original["Id"]: externally_stopped,
                original["Name"].removeprefix("/"): externally_stopped,
            }
        ),
    )

    completed = _run("pause-existing-containers", snapshot, original["Id"], environment=environment)

    assert completed.returncode != 0
    assert "state" in completed.stderr
    assert not paused.exists()
    assert not any(command[1] == "stop" for command in _commands(environment))


@pytest.mark.parametrize("changed_attribute", ["image", "ports", "mounts"])
def test_pause_rejects_critical_container_drift(
    fake_bin: Path, tmp_path: Path, changed_attribute: str
) -> None:
    original = _inspect_record()
    changed = json.loads(json.dumps(original))
    if changed_attribute == "image":
        changed["Image"] = "sha256:changed"
    elif changed_attribute == "ports":
        changed["HostConfig"]["PortBindings"] = {"9000/tcp": None}
    else:
        changed["Mounts"][0]["Source"] = "/data/changed"
    snapshot = tmp_path / "snapshot.jsonl"
    snapshot.write_text(json.dumps(_snapshot_record(original)) + "\n", encoding="utf-8")
    environment = _base_environment(
        fake_bin,
        DOCKER_INSPECT_FIXTURES=json.dumps(
            {original["Id"]: changed, original["Name"].removeprefix("/"): changed}
        ),
    )

    completed = _run("pause-existing-containers", snapshot, original["Id"], environment=environment)

    assert completed.returncode != 0
    assert changed_attribute in completed.stderr
    assert not any(command[1] == "stop" for command in _commands(environment))


def test_pause_does_not_stop_or_record_a_container_that_was_originally_stopped(
    fake_bin: Path, tmp_path: Path
) -> None:
    inspect = _inspect_record(state="exited")
    snapshot = tmp_path / "snapshot.jsonl"
    paused = tmp_path / "paused.jsonl"
    snapshot.write_text(json.dumps(_snapshot_record(inspect)) + "\n", encoding="utf-8")
    environment = _base_environment(
        fake_bin,
        PAUSE_RECORD_PATH=str(paused),
        DOCKER_INSPECT_FIXTURES=json.dumps(
            {inspect["Id"]: inspect, inspect["Name"].removeprefix("/"): inspect}
        ),
    )

    completed = _run("pause-existing-containers", snapshot, inspect["Id"], environment=environment)

    assert completed.returncode == 0, completed.stderr
    assert paused.read_text(encoding="utf-8") == ""
    assert not any(command[1] == "stop" for command in _commands(environment))


def test_pause_preserves_completed_stop_records_when_a_later_stop_fails(
    fake_bin: Path, tmp_path: Path
) -> None:
    first = _inspect_record()
    second = _inspect_record(container_id="b" * 64, name="existing-worker")
    snapshot = tmp_path / "snapshot.jsonl"
    paused = tmp_path / "paused.jsonl"
    snapshot.write_text(
        "\n".join(json.dumps(_snapshot_record(item)) for item in (first, second)) + "\n",
        encoding="utf-8",
    )
    environment = _base_environment(
        fake_bin,
        PAUSE_RECORD_PATH=str(paused),
        STOP_FAIL_ID=second["Id"],
        DOCKER_INSPECT_FIXTURES=json.dumps(
            {
                first["Id"]: first,
                first["Name"].removeprefix("/"): first,
                second["Id"]: second,
                second["Name"].removeprefix("/"): second,
            }
        ),
    )

    completed = _run(
        "pause-existing-containers", snapshot, first["Id"], second["Id"],
        environment=environment,
    )

    assert completed.returncode != 0
    assert "failure" in completed.stderr
    assert json.loads(paused.read_text(encoding="utf-8")) == {
        "container_id": first["Id"],
        "name": first["Name"].removeprefix("/"),
        "action": "stopped",
    }


def test_restore_starts_only_the_exact_id_stopped_by_this_pause_run(
    fake_bin: Path, tmp_path: Path
) -> None:
    original = _inspect_record()
    current = _inspect_record(state="exited")
    snapshot = tmp_path / "snapshot.jsonl"
    paused = tmp_path / "paused.jsonl"
    snapshot.write_text(json.dumps(_snapshot_record(original)) + "\n", encoding="utf-8")
    paused.write_text(
        json.dumps(
            {
                "container_id": original["Id"],
                "name": original["Name"].removeprefix("/"),
                "action": "stopped",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    environment = _base_environment(
        fake_bin,
        DOCKER_INSPECT_FIXTURES=json.dumps(
            {original["Id"]: current, original["Name"].removeprefix("/"): current}
        ),
    )

    completed = _run("restore-existing-containers", snapshot, paused, environment=environment)

    assert completed.returncode == 0, completed.stderr
    assert [command for command in _commands(environment) if command[1] == "start"] == [
        ["docker", "start", original["Id"]]
    ]


@pytest.mark.parametrize("current_state", ["running", "created"])
def test_restore_refuses_to_start_a_container_that_is_not_in_exited_state(
    fake_bin: Path, tmp_path: Path, current_state: str
) -> None:
    original = _inspect_record()
    current = _inspect_record(state=current_state)
    snapshot = tmp_path / "snapshot.jsonl"
    paused = tmp_path / "paused.jsonl"
    snapshot.write_text(json.dumps(_snapshot_record(original)) + "\n", encoding="utf-8")
    paused.write_text(
        json.dumps(
            {
                "container_id": original["Id"],
                "name": original["Name"].removeprefix("/"),
                "action": "stopped",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    environment = _base_environment(
        fake_bin,
        DOCKER_INSPECT_FIXTURES=json.dumps(
            {original["Id"]: current, original["Name"].removeprefix("/"): current}
        ),
    )

    completed = _run("restore-existing-containers", snapshot, paused, environment=environment)

    assert completed.returncode != 0
    assert "state" in completed.stderr
    assert not any(command[1] == "start" for command in _commands(environment))


@pytest.mark.parametrize("changed_attribute", ["name", "image", "ports", "mounts"])
def test_restore_rejects_identity_or_critical_attribute_drift(
    fake_bin: Path, tmp_path: Path, changed_attribute: str
) -> None:
    original = _inspect_record()
    changed = _inspect_record(state="exited")
    by_name = changed
    if changed_attribute == "name":
        by_name = _inspect_record(container_id="replacement-id", state="exited")
    elif changed_attribute == "image":
        changed["Image"] = "sha256:changed"
    elif changed_attribute == "ports":
        changed["HostConfig"]["PortBindings"] = {"9000/tcp": None}
    else:
        changed["Mounts"][0]["Source"] = "/data/changed"
    snapshot = tmp_path / "snapshot.jsonl"
    paused = tmp_path / "paused.jsonl"
    snapshot.write_text(json.dumps(_snapshot_record(original)) + "\n", encoding="utf-8")
    paused.write_text(
        json.dumps(
            {
                "container_id": original["Id"],
                "name": original["Name"].removeprefix("/"),
                "action": "stopped",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    environment = _base_environment(
        fake_bin,
        DOCKER_INSPECT_FIXTURES=json.dumps(
            {original["Id"]: changed, original["Name"].removeprefix("/"): by_name}
        ),
    )

    completed = _run("restore-existing-containers", snapshot, paused, environment=environment)

    assert completed.returncode != 0
    assert not any(command[1] == "start" for command in _commands(environment))


@pytest.mark.parametrize(
    ("snapshot_payload", "paused_payload"),
    [("", ""), ("", "not-json\n"), ('{"container_id":"x"}\n', "")],
)
def test_restore_empty_or_malformed_records_never_operate_on_containers(
    fake_bin: Path,
    tmp_path: Path,
    snapshot_payload: str,
    paused_payload: str,
) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    paused = tmp_path / "paused.jsonl"
    snapshot.write_text(snapshot_payload, encoding="utf-8")
    paused.write_text(paused_payload, encoding="utf-8")
    environment = _base_environment(fake_bin)

    completed = _run("restore-existing-containers", snapshot, paused, environment=environment)

    if snapshot_payload or paused_payload:
        assert completed.returncode != 0
    else:
        assert completed.returncode == 0, completed.stderr
    assert _commands(environment) == []


def test_container_protection_scripts_contain_no_destructive_global_operations() -> None:
    forbidden = (
        "docker rm",
        "docker container rm",
        "docker volume rm",
        "docker system prune",
        "docker container prune",
        "docker compose down",
        "down -v",
        "docker stop $(docker",
        "docker stop *",
        "rm -rf /data/result",
    )

    for name in SCRIPT_NAMES:
        source = (SCRIPTS / name).read_text(encoding="utf-8")
        assert "set -euo pipefail" in source
        assert not any(token in source for token in forbidden)
