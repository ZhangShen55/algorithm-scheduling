from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
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
            "EXPECTED_GIT_SHA": "a" * 40,
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
        "#!/usr/bin/env bash\nprintf '%s' \"${SS_OUTPUT}\"\nexit \"${SS_EXIT:-0}\"\n",
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
from pathlib import Path

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
    if os.environ.get("BLOCK_PS") == "true":
        entered = Path(os.environ["PS_ENTERED_PATH"])
        release = Path(os.environ["PS_RELEASE_PATH"])
        entered.write_text("entered", encoding="utf-8")
        for _ in range(1000):
            if release.exists():
                break
            import time
            time.sleep(0.01)
        else:
            print("timed out waiting to release ps", file=sys.stderr)
            raise SystemExit(70)
    print(os.environ.get("DOCKER_PS_IDS", ""))
    raise SystemExit(int(os.environ.get("DOCKER_PS_EXIT", "0")))
if args[:1] == ["inspect"] and len(args) == 2:
    if args[1] == os.environ.get("BLOCK_INSPECT_ID"):
        entered = Path(os.environ["INSPECT_ENTERED_PATH"])
        release = Path(os.environ["INSPECT_RELEASE_PATH"])
        entered.write_text("entered", encoding="utf-8")
        for _ in range(1000):
            if release.exists():
                break
            import time
            time.sleep(0.01)
        else:
            print("timed out waiting to release inspect", file=sys.stderr)
            raise SystemExit(70)
    default_state_path = Path(os.environ["COMMAND_LOG"]).with_name("docker-state.json")
    state_path = Path(os.environ.get("DOCKER_STATE_PATH", default_state_path))
    if state_path.exists():
        fixtures = json.loads(state_path.read_text(encoding="utf-8"))
    else:
        fixtures = json.loads(os.environ.get("DOCKER_INSPECT_FIXTURES", "{}"))
    counter_path = state_path.with_suffix(".inspect-count")
    count = int(counter_path.read_text(encoding="utf-8")) + 1 if counter_path.exists() else 1
    counter_path.write_text(str(count), encoding="utf-8")
    if count == int(os.environ.get("EXTERNAL_STOP_BEFORE_INSPECT_NUMBER", "-1")):
        target = os.environ["EXTERNAL_STOP_ID"]
        for key, item in fixtures.items():
            if isinstance(item, dict) and item.get("Id") == target:
                item["State"]["Status"] = "exited"
        state_path.write_text(json.dumps(fixtures), encoding="utf-8")
    for action, target_state in (("stop", "exited"), ("start", "running")):
        marker = state_path.with_name(f"{state_path.name}.{action}-delay")
        if marker.exists():
            pending = json.loads(marker.read_text(encoding="utf-8"))
            pending["remaining"] -= 1
            if pending["remaining"] <= 0:
                for item in fixtures.values():
                    if isinstance(item, dict) and item.get("Id") == pending["container_id"]:
                        item["State"]["Status"] = target_state
                marker.unlink()
                state_path.write_text(json.dumps(fixtures), encoding="utf-8")
            else:
                marker.write_text(json.dumps(pending), encoding="utf-8")
    value = fixtures.get(args[1], "__FAIL__")
    if value == "__FAIL__":
        raise SystemExit(1)
    print(json.dumps(value if isinstance(value, list) else [value]))
    raise SystemExit(0)
if args[:1] == ["stop"]:
    if len(args) == 2 and args[1] == os.environ.get("STOP_FAIL_ID"):
        print("injected stop failure", file=sys.stderr)
        raise SystemExit(1)
    if len(args) == 2 and args[1] == os.environ.get("BLOCK_STOP_ID"):
        entered = Path(os.environ["STOP_ENTERED_PATH"])
        release = Path(os.environ["STOP_RELEASE_PATH"])
        entered.write_text("entered", encoding="utf-8")
        for _ in range(1000):
            if release.exists():
                break
            import time
            time.sleep(0.01)
        else:
            print("timed out waiting to release stop", file=sys.stderr)
            raise SystemExit(70)
    default_state_path = Path(os.environ["COMMAND_LOG"]).with_name("docker-state.json")
    state_path = Path(os.environ.get("DOCKER_STATE_PATH", default_state_path))
    fixtures = (
        json.loads(state_path.read_text(encoding="utf-8"))
        if state_path.exists()
        else json.loads(os.environ.get("DOCKER_INSPECT_FIXTURES", "{}"))
    )
    stop_delay = int(os.environ.get("STOP_TRANSITION_AFTER_INSPECTS", "0"))
    if stop_delay:
        state_path.with_name(f"{state_path.name}.stop-delay").write_text(
            json.dumps({"container_id": args[1], "remaining": stop_delay}), encoding="utf-8"
        )
    elif os.environ.get("STOP_PRESERVE_STATE") != "true":
        for item in fixtures.values():
            if isinstance(item, dict) and item.get("Id") == args[1]:
                item["State"]["Status"] = "exited"
    state_path.write_text(json.dumps(fixtures), encoding="utf-8")
    if len(args) == 2 and args[1] == os.environ.get("STOP_INTERRUPT_AFTER_STATE_ID"):
        raise SystemExit(75)
    raise SystemExit(0)
if args[:1] == ["start"]:
    if len(args) == 2 and args[1] == os.environ.get("START_FAIL_ID"):
        print("injected start failure", file=sys.stderr)
        raise SystemExit(1)
    default_state_path = Path(os.environ["COMMAND_LOG"]).with_name("docker-state.json")
    state_path = Path(os.environ.get("DOCKER_STATE_PATH", default_state_path))
    fixtures = (
        json.loads(state_path.read_text(encoding="utf-8"))
        if state_path.exists()
        else json.loads(os.environ.get("DOCKER_INSPECT_FIXTURES", "{}"))
    )
    start_delay = int(os.environ.get("START_TRANSITION_AFTER_INSPECTS", "0"))
    if start_delay:
        state_path.with_name(f"{state_path.name}.start-delay").write_text(
            json.dumps({"container_id": args[1], "remaining": start_delay}), encoding="utf-8"
        )
    else:
        final_state = os.environ.get("START_FINAL_STATE", "running")
        for item in fixtures.values():
            if isinstance(item, dict) and item.get("Id") == args[1]:
                item["State"]["Status"] = final_state
    state_path.write_text(json.dumps(fixtures), encoding="utf-8")
    if len(args) == 2 and args[1] == os.environ.get("START_INTERRUPT_AFTER_STATE_ID"):
        raise SystemExit(75)
    raise SystemExit(0)
if args[:1] == ["update"] and len(args) == 3 and args[1].startswith("--restart="):
    if args[2] == os.environ.get("UPDATE_FAIL_ID"):
        print("injected update failure", file=sys.stderr)
        raise SystemExit(1)
    default_state_path = Path(os.environ["COMMAND_LOG"]).with_name("docker-state.json")
    state_path = Path(os.environ.get("DOCKER_STATE_PATH", default_state_path))
    fixtures = (
        json.loads(state_path.read_text(encoding="utf-8"))
        if state_path.exists()
        else json.loads(os.environ.get("DOCKER_INSPECT_FIXTURES", "{}"))
    )
    value = args[1].removeprefix("--restart=")
    name, _, retries = value.partition(":")
    policy = {"Name": name, "MaximumRetryCount": int(retries or "0")}
    if os.environ.get("UPDATE_PRESERVE_POLICY") != "true":
        for item in fixtures.values():
            if isinstance(item, dict) and item.get("Id") == args[2]:
                item["HostConfig"]["RestartPolicy"] = policy
    state_path.write_text(json.dumps(fixtures), encoding="utf-8")
    if args[2] == os.environ.get("UPDATE_INTERRUPT_AFTER_STATE_ID"):
        raise SystemExit(75)
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


def _wait_for_path(path: Path, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if path.exists():
            return
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(f"process exited before marker: {stdout=} {stderr=}")
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {path}")


def _commands(environment: dict[str, str]) -> list[list[str]]:
    path = Path(environment["COMMAND_LOG"])
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or not path.read_text(encoding="utf-8"):
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _ledger_archives(path: Path) -> list[Path]:
    return sorted(path.parent.glob(f"{path.name}.audit.*.jsonl"))


def _ledger_metadata(path: Path) -> Path:
    return Path(f"{path}.archive.json")


def _assert_archived_ledger(path: Path, expected: list[dict[str, Any]]) -> Path:
    assert not path.exists()
    archives = _ledger_archives(path)
    assert len(archives) == 1
    assert _ledger(archives[0]) == expected
    assert archives[0].stat().st_mode & 0o222 == 0
    return archives[0]


def _inspect_record(
    container_id: str = "a" * 64,
    *,
    name: str = "existing-api",
    image_id: str = "sha256:image-1",
    state: str = "running",
    ports: dict[str, Any] | None = None,
    mounts: list[dict[str, Any]] | None = None,
    restart_policy: dict[str, Any] | None = None,
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
            "RestartPolicy": restart_policy
            if restart_policy is not None
            else {"Name": "unless-stopped", "MaximumRetryCount": 0},
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


def _pause_entry(
    inspect: dict[str, Any], status: str, *, policy_neutralized: bool | None = None
) -> dict[str, Any]:
    binding = _snapshot_record(inspect)
    canonical = json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()
    if policy_neutralized is None:
        policy_neutralized = (
            status not in {"restored", "not_stopped"}
            and binding["restart_policy"].get("Name") not in {"", "no"}
        )
    return {
        "version": 1,
        "status": status,
        "container_id": binding["container_id"],
        "name": binding["name"],
        "snapshot_sha256": hashlib.sha256(canonical).hexdigest(),
        "binding": binding,
        "policy_neutralized": policy_neutralized,
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
    unwritable.chmod(0o500)
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
        REQUIRED_PORTS="",
    )

    try:
        completed = _run("preflight", environment=environment)
    finally:
        unwritable.chmod(0o700)

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


def test_preflight_rejects_missing_or_failed_socket_inspection(
    fake_bin: Path, tmp_path: Path
) -> None:
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(tmp_path),
        RESULT_ROOT=str(tmp_path),
        REQUIRED_PORTS="18100",
        SS_EXIT="1",
    )

    completed = _run("preflight", environment=environment)

    assert completed.returncode != 0
    assert "socket" in completed.stderr.lower() or "ss" in completed.stderr.lower()


@pytest.mark.parametrize("expected_sha", [None, "short", "g" * 40])
def test_preflight_requires_a_full_hex_expected_git_sha(
    fake_bin: Path, tmp_path: Path, expected_sha: str | None
) -> None:
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(tmp_path),
        RESULT_ROOT=str(tmp_path),
        REQUIRED_PORTS="",
    )
    if expected_sha is None:
        environment.pop("EXPECTED_GIT_SHA")
    else:
        environment["EXPECTED_GIT_SHA"] = expected_sha

    completed = _run("preflight", environment=environment)

    assert completed.returncode != 0
    assert "EXPECTED_GIT_SHA" in completed.stderr


def test_preflight_allows_explicit_unpinned_local_mode_with_a_warning(
    fake_bin: Path, tmp_path: Path
) -> None:
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(tmp_path),
        RESULT_ROOT=str(tmp_path),
        REQUIRED_PORTS="",
        ALLOW_UNPINNED_GIT="true",
    )
    environment.pop("EXPECTED_GIT_SHA")

    completed = _run("preflight", environment=environment)

    assert completed.returncode == 0, completed.stderr
    assert "WARNING" in completed.stderr


def test_preflight_probes_required_directories_with_real_fsynced_io() -> None:
    source = (SCRIPTS / "preflight").read_text(encoding="utf-8")

    assert "PREFLIGHT_WRITABLE_CHECK_BIN" not in source
    assert "os.open" in source
    assert "O_EXCL" in source
    assert "os.fsync" in source
    assert "os.unlink" in source


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


def test_snapshot_rejects_a_symlink_output(fake_bin: Path, tmp_path: Path) -> None:
    target = tmp_path / "target.jsonl"
    target.write_text("do not replace\n", encoding="utf-8")
    output = tmp_path / "snapshot.jsonl"
    output.symlink_to(target)
    environment = _base_environment(fake_bin)

    completed = _run("snapshot-existing-containers", output, environment=environment)

    assert completed.returncode != 0
    assert target.read_text(encoding="utf-8") == "do not replace\n"
    assert _commands(environment) == []


def test_snapshot_refuses_to_replace_inventory_while_pause_ledger_is_active(
    fake_bin: Path, tmp_path: Path
) -> None:
    original = _inspect_record()
    replacement = _inspect_record(container_id="b" * 64, name="replacement")
    snapshot = tmp_path / "snapshot.jsonl"
    ledger = Path(f"{snapshot}.paused.jsonl")
    original_payload = json.dumps(_snapshot_record(original)) + "\n"
    snapshot.write_text(original_payload, encoding="utf-8")
    ledger.write_text(
        json.dumps(_pause_entry(original, "stopped")) + "\n", encoding="utf-8"
    )
    environment = _base_environment(
        fake_bin,
        DOCKER_PS_IDS=replacement["Id"],
        DOCKER_INSPECT_FIXTURES=json.dumps({replacement["Id"]: replacement}),
    )

    completed = _run("snapshot-existing-containers", snapshot, environment=environment)

    assert completed.returncode != 0
    assert "active" in completed.stderr
    assert snapshot.read_text(encoding="utf-8") == original_payload
    assert _commands(environment) == []


def test_container_protection_rejects_noncanonical_pause_record_override(
    fake_bin: Path, tmp_path: Path
) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    environment = _base_environment(
        fake_bin, PAUSE_RECORD_PATH=str(tmp_path / "custom-paused.jsonl")
    )

    completed = _run("snapshot-existing-containers", snapshot, environment=environment)

    assert completed.returncode != 0
    assert "PAUSE_RECORD_PATH" in completed.stderr
    assert _commands(environment) == []


def test_snapshot_and_pause_share_the_default_snapshot_derived_lock(
    fake_bin: Path, tmp_path: Path
) -> None:
    original = _inspect_record()
    snapshot = tmp_path / "snapshot.jsonl"
    ledger = Path(f"{snapshot}.paused.jsonl")
    entered = tmp_path / "ps-entered"
    release = tmp_path / "ps-release"
    snapshot.write_text(json.dumps(_snapshot_record(original)) + "\n", encoding="utf-8")
    environment = _base_environment(
        fake_bin,
        BLOCK_PS="true",
        PS_ENTERED_PATH=str(entered),
        PS_RELEASE_PATH=str(release),
        DOCKER_PS_IDS=original["Id"],
        DOCKER_INSPECT_FIXTURES=json.dumps(
            {original["Id"]: original, original["Name"].removeprefix("/"): original}
        ),
    )
    snapshot_process = subprocess.Popen(
        [str(SCRIPTS / "snapshot-existing-containers"), str(snapshot)],
        cwd=PLATFORM_ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for_path(entered, snapshot_process)
        pause_process = subprocess.Popen(
            [str(SCRIPTS / "pause-existing-containers"), str(snapshot), original["Id"]],
            cwd=PLATFORM_ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(0.2)
        assert pause_process.poll() is None, pause_process.communicate()
        assert not ledger.exists()
        assert not any(command[1] == "stop" for command in _commands(environment))
        release.write_text("release", encoding="utf-8")
        snapshot_stdout, snapshot_stderr = snapshot_process.communicate(timeout=10)
        pause_stdout, pause_stderr = pause_process.communicate(timeout=10)
    finally:
        if snapshot_process.poll() is None:
            snapshot_process.kill()
            snapshot_process.wait()

    assert snapshot_process.returncode == 0, (snapshot_stdout, snapshot_stderr)
    assert pause_process.returncode == 0, (pause_stdout, pause_stderr)


@pytest.mark.parametrize("payload", ["not-json\n", '{"container_id":"x"}\n'])
def test_pause_rejects_malformed_or_incomplete_snapshot_without_stopping(
    fake_bin: Path, tmp_path: Path, payload: str
) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    snapshot.write_text(payload, encoding="utf-8")
    environment = _base_environment(fake_bin)

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
    paused = Path(f"{snapshot}.paused.jsonl")
    snapshot.write_text(json.dumps(_snapshot_record(inspect)) + "\n", encoding="utf-8")
    environment = _base_environment(
        fake_bin,
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
    assert _ledger(paused) == [_pause_entry(inspect, "stopped")]


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
    paused = Path(f"{snapshot}.paused.jsonl")
    snapshot.write_text(json.dumps(_snapshot_record(original)) + "\n", encoding="utf-8")
    environment = _base_environment(
        fake_bin,
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


def test_pause_rechecks_immediately_before_stop_and_rejects_external_stop(
    fake_bin: Path, tmp_path: Path
) -> None:
    original = _inspect_record()
    snapshot = tmp_path / "snapshot.jsonl"
    paused = Path(f"{snapshot}.paused.jsonl")
    snapshot.write_text(json.dumps(_snapshot_record(original)) + "\n", encoding="utf-8")
    environment = _base_environment(
        fake_bin,
        EXTERNAL_STOP_BEFORE_INSPECT_NUMBER="3",
        EXTERNAL_STOP_ID=original["Id"],
        DOCKER_INSPECT_FIXTURES=json.dumps(
            {original["Id"]: original, original["Name"].removeprefix("/"): original}
        ),
    )

    completed = _run("pause-existing-containers", snapshot, original["Id"], environment=environment)

    assert completed.returncode != 0
    assert "state" in completed.stderr
    assert _ledger(paused) == []
    assert not any(command[1] == "stop" for command in _commands(environment))


def test_pause_leaves_fsynced_pending_intent_when_interrupted_after_stop(
    fake_bin: Path, tmp_path: Path
) -> None:
    original = _inspect_record()
    snapshot = tmp_path / "snapshot.jsonl"
    paused = Path(f"{snapshot}.paused.jsonl")
    snapshot.write_text(json.dumps(_snapshot_record(original)) + "\n", encoding="utf-8")
    environment = _base_environment(
        fake_bin,
        STOP_INTERRUPT_AFTER_STATE_ID=original["Id"],
        DOCKER_INSPECT_FIXTURES=json.dumps(
            {original["Id"]: original, original["Name"].removeprefix("/"): original}
        ),
    )

    completed = _run("pause-existing-containers", snapshot, original["Id"], environment=environment)

    assert completed.returncode != 0
    assert _ledger(paused) == [_pause_entry(original, "pending_stop")]


def test_pause_keeps_pending_stop_when_docker_stop_does_not_converge(
    fake_bin: Path, tmp_path: Path
) -> None:
    original = _inspect_record()
    snapshot = tmp_path / "snapshot.jsonl"
    paused = Path(f"{snapshot}.paused.jsonl")
    snapshot.write_text(json.dumps(_snapshot_record(original)) + "\n", encoding="utf-8")
    environment = _base_environment(
        fake_bin,
        STOP_PRESERVE_STATE="true",
        STOP_STATE_TIMEOUT_SECONDS="0",
        STATE_POLL_INTERVAL_SECONDS="0.01",
        DOCKER_INSPECT_FIXTURES=json.dumps(
            {original["Id"]: original, original["Name"].removeprefix("/"): original}
        ),
    )

    completed = _run("pause-existing-containers", snapshot, original["Id"], environment=environment)

    assert completed.returncode != 0
    assert "exited" in completed.stderr
    assert _ledger(paused) == [_pause_entry(original, "pending_stop")]


def test_pause_waits_for_delayed_exited_state_before_marking_stopped(
    fake_bin: Path, tmp_path: Path
) -> None:
    original = _inspect_record()
    snapshot = tmp_path / "snapshot.jsonl"
    paused = Path(f"{snapshot}.paused.jsonl")
    snapshot.write_text(json.dumps(_snapshot_record(original)) + "\n", encoding="utf-8")
    environment = _base_environment(
        fake_bin,
        STOP_TRANSITION_AFTER_INSPECTS="2",
        STOP_STATE_TIMEOUT_SECONDS="1",
        STATE_POLL_INTERVAL_SECONDS="0.01",
        DOCKER_INSPECT_FIXTURES=json.dumps(
            {original["Id"]: original, original["Name"].removeprefix("/"): original}
        ),
    )

    completed = _run("pause-existing-containers", snapshot, original["Id"], environment=environment)

    assert completed.returncode == 0, completed.stderr
    assert _ledger(paused) == [_pause_entry(original, "stopped")]


def test_pause_persists_intent_before_restart_policy_neutralization_failure(
    fake_bin: Path, tmp_path: Path
) -> None:
    original = _inspect_record()
    snapshot = tmp_path / "snapshot.jsonl"
    paused = Path(f"{snapshot}.paused.jsonl")
    snapshot.write_text(json.dumps(_snapshot_record(original)) + "\n", encoding="utf-8")
    environment = _base_environment(
        fake_bin,
        UPDATE_FAIL_ID=original["Id"],
        DOCKER_INSPECT_FIXTURES=json.dumps(
            {original["Id"]: original, original["Name"].removeprefix("/"): original}
        ),
    )

    completed = _run("pause-existing-containers", snapshot, original["Id"], environment=environment)

    assert completed.returncode != 0
    assert _ledger(paused) == [
        _pause_entry(original, "pending_stop", policy_neutralized=False)
    ]
    assert [command for command in _commands(environment) if command[1] in {"update", "stop"}] == [
        ["docker", "update", "--restart=no", original["Id"]]
    ]


def test_restore_repairs_interrupted_restart_policy_neutralization(
    fake_bin: Path, tmp_path: Path
) -> None:
    original = _inspect_record()
    snapshot = tmp_path / "snapshot.jsonl"
    paused = Path(f"{snapshot}.paused.jsonl")
    snapshot.write_text(json.dumps(_snapshot_record(original)) + "\n", encoding="utf-8")
    environment = _base_environment(
        fake_bin,
        UPDATE_INTERRUPT_AFTER_STATE_ID=original["Id"],
        DOCKER_INSPECT_FIXTURES=json.dumps(
            {original["Id"]: original, original["Name"].removeprefix("/"): original}
        ),
    )

    pause = _run("pause-existing-containers", snapshot, original["Id"], environment=environment)
    environment.pop("UPDATE_INTERRUPT_AFTER_STATE_ID")
    restore = _run("restore-existing-containers", snapshot, paused, environment=environment)

    assert pause.returncode != 0
    assert restore.returncode == 0, restore.stderr
    assert not paused.exists()
    archives = _ledger_archives(paused)
    assert len(archives) == 1
    assert _ledger(archives[0]) == [_pause_entry(original, "not_stopped")]
    updates = [command for command in _commands(environment) if command[1] == "update"]
    assert updates == [
        ["docker", "update", "--restart=no", original["Id"]],
        ["docker", "update", "--restart=unless-stopped", original["Id"]],
    ]


def test_pause_rejects_compose_project_mismatch_before_docker(
    fake_bin: Path, tmp_path: Path
) -> None:
    original = _inspect_record()
    binding = _snapshot_record(original)
    binding["compose_project"] = "forged-project"
    snapshot = tmp_path / "snapshot.jsonl"
    snapshot.write_text(json.dumps(binding) + "\n", encoding="utf-8")
    environment = _base_environment(fake_bin)

    completed = _run("pause-existing-containers", snapshot, original["Id"], environment=environment)

    assert completed.returncode != 0
    assert "compose_project" in completed.stderr
    assert _commands(environment) == []


def test_pause_rejects_a_symlink_ledger(fake_bin: Path, tmp_path: Path) -> None:
    original = _inspect_record()
    snapshot = tmp_path / "snapshot.jsonl"
    snapshot.write_text(json.dumps(_snapshot_record(original)) + "\n", encoding="utf-8")
    target = tmp_path / "target.jsonl"
    target.write_text("do not replace\n", encoding="utf-8")
    paused = Path(f"{snapshot}.paused.jsonl")
    paused.symlink_to(target)
    environment = _base_environment(fake_bin)

    completed = _run("pause-existing-containers", snapshot, original["Id"], environment=environment)

    assert completed.returncode != 0
    assert target.read_text(encoding="utf-8") == "do not replace\n"
    assert _commands(environment) == []


def test_two_concurrent_pauses_share_one_exclusive_ledger_and_stop_once(
    fake_bin: Path, tmp_path: Path
) -> None:
    original = _inspect_record()
    snapshot = tmp_path / "snapshot.jsonl"
    ledger = Path(f"{snapshot}.paused.jsonl")
    lock = tmp_path / "protection.lock"
    entered = tmp_path / "stop-entered"
    release = tmp_path / "stop-release"
    snapshot.write_text(json.dumps(_snapshot_record(original)) + "\n", encoding="utf-8")
    environment = _base_environment(
        fake_bin,
        DEPLOY_OPERATION_LOCK=str(lock),
        BLOCK_STOP_ID=original["Id"],
        STOP_ENTERED_PATH=str(entered),
        STOP_RELEASE_PATH=str(release),
        DOCKER_INSPECT_FIXTURES=json.dumps(
            {original["Id"]: original, original["Name"].removeprefix("/"): original}
        ),
    )
    command = [str(SCRIPTS / "pause-existing-containers"), str(snapshot), original["Id"]]

    first = subprocess.Popen(
        command,
        cwd=PLATFORM_ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for_path(entered, first)
        ledger.unlink()
        second = subprocess.Popen(
            command,
            cwd=PLATFORM_ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(0.2)
        assert second.poll() is None, second.communicate()
        release.write_text("release", encoding="utf-8")
        first_stdout, first_stderr = first.communicate(timeout=10)
        second_stdout, second_stderr = second.communicate(timeout=10)
    finally:
        if first.poll() is None:
            first.kill()
            first.wait()

    assert first.returncode == 0, (first_stdout, first_stderr)
    assert second.returncode != 0, (second_stdout, second_stderr)
    assert "existing" in second_stderr
    assert _ledger(ledger) == [_pause_entry(original, "stopped")]
    assert [command for command in _commands(environment) if command[1] == "stop"] == [
        ["docker", "stop", original["Id"]]
    ]


@pytest.mark.parametrize("script", ["pause-existing-containers", "restore-existing-containers"])
def test_container_protection_rejects_a_symlink_operation_lock(
    fake_bin: Path, tmp_path: Path, script: str
) -> None:
    original = _inspect_record()
    snapshot = tmp_path / "snapshot.jsonl"
    ledger = Path(f"{snapshot}.paused.jsonl")
    target = tmp_path / "lock-target"
    lock = tmp_path / "protection.lock"
    snapshot.write_text(json.dumps(_snapshot_record(original)) + "\n", encoding="utf-8")
    if script == "restore-existing-containers":
        ledger.write_text(json.dumps(_pause_entry(original, "stopped")) + "\n", encoding="utf-8")
        arguments: tuple[Path | str, ...] = (snapshot, ledger)
    else:
        arguments = (snapshot, original["Id"])
    target.write_text("do not lock\n", encoding="utf-8")
    lock.symlink_to(target)
    environment = _base_environment(
        fake_bin,
        DEPLOY_OPERATION_LOCK=str(lock),
    )

    completed = _run(script, *arguments, environment=environment)

    assert completed.returncode != 0
    assert "lock" in completed.stderr
    assert target.read_text(encoding="utf-8") == "do not lock\n"
    assert _commands(environment) == []


@pytest.mark.parametrize("script", ["pause-existing-containers", "restore-existing-containers"])
def test_container_protection_rejects_a_symlink_lock_directory(
    fake_bin: Path, tmp_path: Path, script: str
) -> None:
    original = _inspect_record()
    snapshot = tmp_path / "snapshot.jsonl"
    ledger = Path(f"{snapshot}.paused.jsonl")
    real_lock_directory = tmp_path / "real-locks"
    linked_lock_directory = tmp_path / "linked-locks"
    real_lock_directory.mkdir()
    linked_lock_directory.symlink_to(real_lock_directory, target_is_directory=True)
    snapshot.write_text(json.dumps(_snapshot_record(original)) + "\n", encoding="utf-8")
    if script == "restore-existing-containers":
        ledger.write_text(json.dumps(_pause_entry(original, "stopped")) + "\n", encoding="utf-8")
        arguments: tuple[Path | str, ...] = (snapshot, ledger)
    else:
        arguments = (snapshot, original["Id"])
    environment = _base_environment(
        fake_bin,
        DEPLOY_OPERATION_LOCK=str(linked_lock_directory / "protection.lock"),
        DOCKER_INSPECT_FIXTURES=json.dumps(
            {original["Id"]: original, original["Name"].removeprefix("/"): original}
        ),
    )

    completed = _run(script, *arguments, environment=environment)

    assert completed.returncode != 0
    assert "lock" in completed.stderr
    assert not (real_lock_directory / "protection.lock").exists()
    assert _commands(environment) == []


def test_restore_waits_for_pause_then_reads_and_restores_the_complete_ledger(
    fake_bin: Path, tmp_path: Path
) -> None:
    first = _inspect_record()
    second = _inspect_record(container_id="b" * 64, name="existing-worker")
    snapshot = tmp_path / "snapshot.jsonl"
    ledger = Path(f"{snapshot}.paused.jsonl")
    lock = tmp_path / "protection.lock"
    entered = tmp_path / "first-stop-entered"
    release = tmp_path / "first-stop-release"
    snapshot.write_text(
        "\n".join(json.dumps(_snapshot_record(item)) for item in (first, second)) + "\n",
        encoding="utf-8",
    )
    fixtures = {
        first["Id"]: first,
        first["Name"].removeprefix("/"): first,
        second["Id"]: second,
        second["Name"].removeprefix("/"): second,
    }
    pause_environment = _base_environment(
        fake_bin,
        DEPLOY_OPERATION_LOCK=str(lock),
        BLOCK_STOP_ID=first["Id"],
        STOP_ENTERED_PATH=str(entered),
        STOP_RELEASE_PATH=str(release),
        DOCKER_INSPECT_FIXTURES=json.dumps(fixtures),
    )
    restore_environment = pause_environment.copy()
    restore_environment.pop("BLOCK_STOP_ID")
    restore_environment.pop("STOP_ENTERED_PATH")
    restore_environment.pop("STOP_RELEASE_PATH")
    pause_command = [
        str(SCRIPTS / "pause-existing-containers"),
        str(snapshot),
        first["Id"],
        second["Id"],
    ]
    restore_command = [
        str(SCRIPTS / "restore-existing-containers"),
        str(snapshot),
        str(ledger),
    ]

    pause = subprocess.Popen(
        pause_command,
        cwd=PLATFORM_ROOT,
        env=pause_environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for_path(entered, pause)
        assert len(_ledger(ledger)) == 1
        restore = subprocess.Popen(
            restore_command,
            cwd=PLATFORM_ROOT,
            env=restore_environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(0.2)
        assert restore.poll() is None, restore.communicate()
        release.write_text("release", encoding="utf-8")
        pause_stdout, pause_stderr = pause.communicate(timeout=10)
        restore_stdout, restore_stderr = restore.communicate(timeout=10)
    finally:
        if pause.poll() is None:
            pause.kill()
            pause.wait()

    assert pause.returncode == 0, (pause_stdout, pause_stderr)
    assert restore.returncode == 0, (restore_stdout, restore_stderr)
    starts = [command for command in _commands(pause_environment) if command[1] == "start"]
    assert starts == [
        ["docker", "start", first["Id"]],
        ["docker", "start", second["Id"]],
    ]
    _assert_archived_ledger(
        ledger, [_pause_entry(first, "restored"), _pause_entry(second, "restored")]
    )


def test_restore_reads_snapshot_after_waiting_for_lock_and_rejects_changed_binding(
    fake_bin: Path, tmp_path: Path
) -> None:
    original = _inspect_record(state="exited")
    running_snapshot = _inspect_record()
    snapshot = tmp_path / "snapshot.jsonl"
    ledger = Path(f"{snapshot}.paused.jsonl")
    lock = tmp_path / "protection.lock"
    snapshot.write_text(json.dumps(_snapshot_record(running_snapshot)) + "\n", encoding="utf-8")
    ledger.write_text(
        json.dumps(_pause_entry(running_snapshot, "stopped")) + "\n", encoding="utf-8"
    )
    environment = _base_environment(
        fake_bin,
        DEPLOY_OPERATION_LOCK=str(lock),
        DOCKER_INSPECT_FIXTURES=json.dumps(
            {original["Id"]: original, original["Name"].removeprefix("/"): original}
        ),
    )
    lock_holder = subprocess.Popen(
        [
            os.environ.get("PYTHON", str(PLATFORM_ROOT / ".venv/bin/python")),
            "-c",
            (
                "import fcntl,os,sys,time; "
                "fd=os.open(sys.argv[1],os.O_RDWR|os.O_CREAT,0o600); "
                "fcntl.flock(fd,fcntl.LOCK_EX); open(sys.argv[2],'w').write('locked'); "
                "time.sleep(10)"
            ),
            str(lock),
            str(tmp_path / "lock-held"),
        ],
        text=True,
    )
    try:
        _wait_for_path(tmp_path / "lock-held", lock_holder)
        restore = subprocess.Popen(
            [str(SCRIPTS / "restore-existing-containers"), str(snapshot), str(ledger)],
            cwd=PLATFORM_ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(0.2)
        assert restore.poll() is None, restore.communicate()
        changed = _snapshot_record(running_snapshot)
        changed["image_id"] = "sha256:changed-while-waiting"
        snapshot.write_text(json.dumps(changed) + "\n", encoding="utf-8")
        lock_holder.terminate()
        lock_holder.wait(timeout=10)
        restore_stdout, restore_stderr = restore.communicate(timeout=10)
    finally:
        if lock_holder.poll() is None:
            lock_holder.kill()
            lock_holder.wait()

    assert restore.returncode != 0, restore_stdout
    assert "binding" in restore_stderr or "hash" in restore_stderr
    assert not any(command[1] == "start" for command in _commands(environment))


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
    paused = Path(f"{snapshot}.paused.jsonl")
    snapshot.write_text(json.dumps(_snapshot_record(inspect)) + "\n", encoding="utf-8")
    environment = _base_environment(
        fake_bin,
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
    paused = Path(f"{snapshot}.paused.jsonl")
    snapshot.write_text(
        "\n".join(json.dumps(_snapshot_record(item)) for item in (first, second)) + "\n",
        encoding="utf-8",
    )
    environment = _base_environment(
        fake_bin,
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
    assert _ledger(paused) == [_pause_entry(first, "stopped"), _pause_entry(second, "pending_stop")]


def test_restore_starts_only_the_exact_id_stopped_by_this_pause_run(
    fake_bin: Path, tmp_path: Path
) -> None:
    original = _inspect_record()
    current = _inspect_record(state="exited")
    snapshot = tmp_path / "snapshot.jsonl"
    paused = Path(f"{snapshot}.paused.jsonl")
    snapshot.write_text(json.dumps(_snapshot_record(original)) + "\n", encoding="utf-8")
    paused.write_text(json.dumps(_pause_entry(original, "stopped")) + "\n", encoding="utf-8")
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
    _assert_archived_ledger(paused, [_pause_entry(original, "restored")])


def test_restore_rejects_matching_alternate_ledger_before_lock_or_docker(
    fake_bin: Path, tmp_path: Path
) -> None:
    original = _inspect_record()
    snapshot = tmp_path / "snapshot.jsonl"
    canonical = Path(f"{snapshot}.paused.jsonl")
    alternate = tmp_path / "alternate.jsonl"
    payload = json.dumps(_pause_entry(original, "stopped")) + "\n"
    snapshot.write_text(json.dumps(_snapshot_record(original)) + "\n", encoding="utf-8")
    canonical.write_text(payload, encoding="utf-8")
    alternate.write_text(payload, encoding="utf-8")
    environment = _base_environment(fake_bin)

    completed = _run(
        "restore-existing-containers", snapshot, alternate, environment=environment
    )

    assert completed.returncode != 0
    assert "canonical" in completed.stderr
    assert canonical.read_text(encoding="utf-8") == payload
    assert _commands(environment) == []
    assert not Path(f"{snapshot}.operation.lock").exists()


def test_restore_accepts_relative_path_resolving_to_canonical_ledger(
    fake_bin: Path
) -> None:
    original = _inspect_record()
    relative_snapshot = Path(".pytest-relative-snapshot.jsonl")
    relative_ledger = Path(f"{relative_snapshot}.paused.jsonl")
    snapshot = PLATFORM_ROOT / relative_snapshot
    ledger = PLATFORM_ROOT / relative_ledger
    snapshot.write_text(json.dumps(_snapshot_record(original)) + "\n", encoding="utf-8")
    ledger.write_text(json.dumps(_pause_entry(original, "restored")) + "\n", encoding="utf-8")
    environment = _base_environment(
        fake_bin,
        DOCKER_INSPECT_FIXTURES=json.dumps(
            {original["Id"]: original, original["Name"].removeprefix("/"): original}
        ),
    )

    try:
        completed = _run(
            "restore-existing-containers",
            relative_snapshot,
            relative_ledger,
            environment=environment,
        )
    finally:
        snapshot.unlink(missing_ok=True)
        ledger.unlink(missing_ok=True)
        Path(f"{ledger}.archive.json").unlink(missing_ok=True)
        Path(f"{snapshot}.operation.lock").unlink(missing_ok=True)
        for archive in _ledger_archives(ledger):
            archive.unlink()

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("final_state", ["exited", "dead"])
def test_restore_keeps_restoring_when_start_does_not_reach_running(
    fake_bin: Path, tmp_path: Path, final_state: str
) -> None:
    original = _inspect_record()
    current = _inspect_record(
        state="exited", restart_policy={"Name": "no", "MaximumRetryCount": 0}
    )
    snapshot = tmp_path / "snapshot.jsonl"
    paused = Path(f"{snapshot}.paused.jsonl")
    snapshot.write_text(json.dumps(_snapshot_record(original)) + "\n", encoding="utf-8")
    paused.write_text(json.dumps(_pause_entry(original, "stopped")) + "\n", encoding="utf-8")
    environment = _base_environment(
        fake_bin,
        START_FINAL_STATE=final_state,
        START_STATE_TIMEOUT_SECONDS="0",
        STATE_POLL_INTERVAL_SECONDS="0.01",
        DOCKER_INSPECT_FIXTURES=json.dumps(
            {original["Id"]: current, original["Name"].removeprefix("/"): current}
        ),
    )

    completed = _run("restore-existing-containers", snapshot, paused, environment=environment)

    assert completed.returncode != 0
    assert "running" in completed.stderr
    assert _ledger(paused) == [_pause_entry(original, "restoring")]
    assert not any(command[1] == "update" for command in _commands(environment))


def test_restore_waits_for_delayed_running_then_restores_original_restart_policy(
    fake_bin: Path, tmp_path: Path
) -> None:
    original = _inspect_record(
        restart_policy={"Name": "on-failure", "MaximumRetryCount": 4}
    )
    current = _inspect_record(
        state="exited", restart_policy={"Name": "no", "MaximumRetryCount": 0}
    )
    snapshot = tmp_path / "snapshot.jsonl"
    paused = Path(f"{snapshot}.paused.jsonl")
    snapshot.write_text(json.dumps(_snapshot_record(original)) + "\n", encoding="utf-8")
    paused.write_text(json.dumps(_pause_entry(original, "stopped")) + "\n", encoding="utf-8")
    environment = _base_environment(
        fake_bin,
        START_TRANSITION_AFTER_INSPECTS="2",
        START_STATE_TIMEOUT_SECONDS="1",
        STATE_POLL_INTERVAL_SECONDS="0.01",
        DOCKER_INSPECT_FIXTURES=json.dumps(
            {original["Id"]: current, original["Name"].removeprefix("/"): current}
        ),
    )

    completed = _run("restore-existing-containers", snapshot, paused, environment=environment)

    assert completed.returncode == 0, completed.stderr
    _assert_archived_ledger(paused, [_pause_entry(original, "restored")])
    assert [command for command in _commands(environment) if command[1] == "update"] == [
        ["docker", "update", "--restart=on-failure:4", original["Id"]]
    ]


def test_restore_keeps_restoring_when_original_restart_policy_is_not_confirmed(
    fake_bin: Path, tmp_path: Path
) -> None:
    original = _inspect_record()
    current = _inspect_record(
        state="exited", restart_policy={"Name": "no", "MaximumRetryCount": 0}
    )
    snapshot = tmp_path / "snapshot.jsonl"
    paused = Path(f"{snapshot}.paused.jsonl")
    snapshot.write_text(json.dumps(_snapshot_record(original)) + "\n", encoding="utf-8")
    paused.write_text(json.dumps(_pause_entry(original, "stopped")) + "\n", encoding="utf-8")
    environment = _base_environment(
        fake_bin,
        UPDATE_PRESERVE_POLICY="true",
        DOCKER_INSPECT_FIXTURES=json.dumps(
            {original["Id"]: current, original["Name"].removeprefix("/"): current}
        ),
    )

    completed = _run("restore-existing-containers", snapshot, paused, environment=environment)

    assert completed.returncode != 0
    assert "restart policy" in completed.stderr
    assert _ledger(paused) == [_pause_entry(original, "restoring")]


@pytest.mark.parametrize(
    ("current_state", "expected_status", "expected_start_count", "message"),
    [
        ("running", "not_stopped", 0, "not_stopped"),
        ("exited", "restored", 1, "recovered_from_pending"),
    ],
)
def test_restore_reconciles_pending_stop_conservatively(
    fake_bin: Path,
    tmp_path: Path,
    current_state: str,
    expected_status: str,
    expected_start_count: int,
    message: str,
) -> None:
    original = _inspect_record()
    current = _inspect_record(state=current_state)
    snapshot = tmp_path / "snapshot.jsonl"
    paused = Path(f"{snapshot}.paused.jsonl")
    snapshot.write_text(json.dumps(_snapshot_record(original)) + "\n", encoding="utf-8")
    paused.write_text(json.dumps(_pause_entry(original, "pending_stop")) + "\n", encoding="utf-8")
    environment = _base_environment(
        fake_bin,
        DOCKER_INSPECT_FIXTURES=json.dumps(
            {original["Id"]: current, original["Name"].removeprefix("/"): current}
        ),
    )

    completed = _run("restore-existing-containers", snapshot, paused, environment=environment)

    assert completed.returncode == 0, completed.stderr
    starts = [command for command in _commands(environment) if command[1] == "start"]
    assert len(starts) == expected_start_count
    _assert_archived_ledger(paused, [_pause_entry(original, expected_status)])
    assert message in completed.stdout


def test_restore_recovers_when_start_succeeded_before_ledger_update(
    fake_bin: Path, tmp_path: Path
) -> None:
    original = _inspect_record()
    current = _inspect_record(state="running")
    snapshot = tmp_path / "snapshot.jsonl"
    paused = Path(f"{snapshot}.paused.jsonl")
    snapshot.write_text(json.dumps(_snapshot_record(original)) + "\n", encoding="utf-8")
    paused.write_text(json.dumps(_pause_entry(original, "restoring")) + "\n", encoding="utf-8")
    environment = _base_environment(
        fake_bin,
        DOCKER_INSPECT_FIXTURES=json.dumps(
            {original["Id"]: current, original["Name"].removeprefix("/"): current}
        ),
    )

    completed = _run("restore-existing-containers", snapshot, paused, environment=environment)

    assert completed.returncode == 0, completed.stderr
    assert not any(command[1] == "start" for command in _commands(environment))
    _assert_archived_ledger(paused, [_pause_entry(original, "restored")])
    assert "already restored" in completed.stdout


def test_restore_resume_does_not_restart_an_already_restored_first_container(
    fake_bin: Path, tmp_path: Path
) -> None:
    first = _inspect_record()
    second = _inspect_record(container_id="b" * 64, name="existing-worker")
    first_running = _inspect_record()
    second_exited = _inspect_record(container_id="b" * 64, name="existing-worker", state="exited")
    snapshot = tmp_path / "snapshot.jsonl"
    paused = Path(f"{snapshot}.paused.jsonl")
    snapshot.write_text(
        "\n".join(json.dumps(_snapshot_record(item)) for item in (first, second)) + "\n",
        encoding="utf-8",
    )
    paused.write_text(
        "\n".join(
            json.dumps(entry)
            for entry in (_pause_entry(first, "restored"), _pause_entry(second, "restoring"))
        )
        + "\n",
        encoding="utf-8",
    )
    environment = _base_environment(
        fake_bin,
        DOCKER_INSPECT_FIXTURES=json.dumps(
            {
                first["Id"]: first_running,
                first["Name"].removeprefix("/"): first_running,
                second["Id"]: second_exited,
                second["Name"].removeprefix("/"): second_exited,
            }
        ),
    )

    completed = _run("restore-existing-containers", snapshot, paused, environment=environment)

    assert completed.returncode == 0, completed.stderr
    starts = [command for command in _commands(environment) if command[1] == "start"]
    assert starts == [["docker", "start", second["Id"]]]
    _assert_archived_ledger(
        paused, [_pause_entry(first, "restored"), _pause_entry(second, "restored")]
    )


def test_restore_interrupted_after_start_resumes_without_duplicate_start(
    fake_bin: Path, tmp_path: Path
) -> None:
    original = _inspect_record()
    current = _inspect_record(state="exited")
    snapshot = tmp_path / "snapshot.jsonl"
    paused = Path(f"{snapshot}.paused.jsonl")
    snapshot.write_text(json.dumps(_snapshot_record(original)) + "\n", encoding="utf-8")
    paused.write_text(json.dumps(_pause_entry(original, "stopped")) + "\n", encoding="utf-8")
    environment = _base_environment(
        fake_bin,
        START_INTERRUPT_AFTER_STATE_ID=original["Id"],
        DOCKER_INSPECT_FIXTURES=json.dumps(
            {original["Id"]: current, original["Name"].removeprefix("/"): current}
        ),
    )

    first_run = _run("restore-existing-containers", snapshot, paused, environment=environment)
    environment.pop("START_INTERRUPT_AFTER_STATE_ID")
    second_run = _run("restore-existing-containers", snapshot, paused, environment=environment)

    assert first_run.returncode != 0
    assert second_run.returncode == 0, second_run.stderr
    starts = [command for command in _commands(environment) if command[1] == "start"]
    assert starts == [["docker", "start", original["Id"]]]
    _assert_archived_ledger(paused, [_pause_entry(original, "restored")])


def test_restore_retry_continues_after_second_start_failure_without_restarting_first(
    fake_bin: Path, tmp_path: Path
) -> None:
    first = _inspect_record()
    second = _inspect_record(container_id="b" * 64, name="existing-worker")
    first_exited = _inspect_record(state="exited")
    second_exited = _inspect_record(container_id="b" * 64, name="existing-worker", state="exited")
    snapshot = tmp_path / "snapshot.jsonl"
    paused = Path(f"{snapshot}.paused.jsonl")
    snapshot.write_text(
        "\n".join(json.dumps(_snapshot_record(item)) for item in (first, second)) + "\n",
        encoding="utf-8",
    )
    paused.write_text(
        "\n".join(
            json.dumps(entry)
            for entry in (_pause_entry(first, "stopped"), _pause_entry(second, "stopped"))
        )
        + "\n",
        encoding="utf-8",
    )
    environment = _base_environment(
        fake_bin,
        START_FAIL_ID=second["Id"],
        DOCKER_INSPECT_FIXTURES=json.dumps(
            {
                first["Id"]: first_exited,
                first["Name"].removeprefix("/"): first_exited,
                second["Id"]: second_exited,
                second["Name"].removeprefix("/"): second_exited,
            }
        ),
    )

    first_run = _run("restore-existing-containers", snapshot, paused, environment=environment)
    environment.pop("START_FAIL_ID")
    second_run = _run("restore-existing-containers", snapshot, paused, environment=environment)

    assert first_run.returncode != 0
    assert second_run.returncode == 0, second_run.stderr
    starts = [command for command in _commands(environment) if command[1] == "start"]
    assert starts == [
        ["docker", "start", first["Id"]],
        ["docker", "start", second["Id"]],
        ["docker", "start", second["Id"]],
    ]
    _assert_archived_ledger(
        paused, [_pause_entry(first, "restored"), _pause_entry(second, "restored")]
    )


def test_two_consecutive_pause_restore_rounds_create_unique_read_only_audits(
    fake_bin: Path, tmp_path: Path
) -> None:
    original = _inspect_record()
    snapshot = tmp_path / "snapshot.jsonl"
    paused = Path(f"{snapshot}.paused.jsonl")
    environment = _base_environment(
        fake_bin,
        DOCKER_PS_IDS=original["Id"],
        DOCKER_INSPECT_FIXTURES=json.dumps(
            {original["Id"]: original, original["Name"].removeprefix("/"): original}
        ),
    )

    for expected_archive_count in (1, 2):
        snapshot_run = _run("snapshot-existing-containers", snapshot, environment=environment)
        pause_run = _run(
            "pause-existing-containers", snapshot, original["Id"], environment=environment
        )
        restore_run = _run(
            "restore-existing-containers", snapshot, paused, environment=environment
        )

        assert snapshot_run.returncode == 0, snapshot_run.stderr
        assert pause_run.returncode == 0, pause_run.stderr
        assert restore_run.returncode == 0, restore_run.stderr
        assert not paused.exists()
        archives = _ledger_archives(paused)
        assert len(archives) == expected_archive_count
        assert len({archive.name for archive in archives}) == expected_archive_count
        assert all(archive.stat().st_mode & 0o222 == 0 for archive in archives)


@pytest.mark.parametrize("fault_stage", ["create", "chmod", "unlink"])
def test_restore_archive_is_reentrant_after_each_destructive_stage(
    fake_bin: Path, tmp_path: Path, fault_stage: str
) -> None:
    original = _inspect_record()
    snapshot = tmp_path / "snapshot.jsonl"
    paused = Path(f"{snapshot}.paused.jsonl")
    expected = [_pause_entry(original, "restored")]
    snapshot.write_text(json.dumps(_snapshot_record(original)) + "\n", encoding="utf-8")
    paused.write_text(json.dumps(expected[0]) + "\n", encoding="utf-8")
    environment = _base_environment(
        fake_bin,
        ARCHIVE_FAULT_STAGE=fault_stage,
        DOCKER_INSPECT_FIXTURES=json.dumps(
            {original["Id"]: original, original["Name"].removeprefix("/"): original}
        ),
    )

    interrupted = _run(
        "restore-existing-containers", snapshot, paused, environment=environment
    )
    environment.pop("ARCHIVE_FAULT_STAGE")
    resumed = _run("restore-existing-containers", snapshot, paused, environment=environment)

    assert interrupted.returncode != 0
    assert resumed.returncode == 0, resumed.stderr
    assert not paused.exists()
    assert not _ledger_metadata(paused).exists()
    archives = _ledger_archives(paused)
    assert len(archives) == 1
    assert archives[0].stat().st_mode & 0o777 == 0o400
    assert _ledger(archives[0]) == expected


@pytest.mark.parametrize("current_state", ["running", "created"])
def test_restore_refuses_to_start_a_container_that_is_not_in_exited_state(
    fake_bin: Path, tmp_path: Path, current_state: str
) -> None:
    original = _inspect_record()
    current = _inspect_record(state=current_state)
    snapshot = tmp_path / "snapshot.jsonl"
    paused = Path(f"{snapshot}.paused.jsonl")
    snapshot.write_text(json.dumps(_snapshot_record(original)) + "\n", encoding="utf-8")
    paused.write_text(json.dumps(_pause_entry(original, "stopped")) + "\n", encoding="utf-8")
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
    paused = Path(f"{snapshot}.paused.jsonl")
    snapshot.write_text(json.dumps(_snapshot_record(original)) + "\n", encoding="utf-8")
    paused.write_text(json.dumps(_pause_entry(original, "stopped")) + "\n", encoding="utf-8")
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
    paused = Path(f"{snapshot}.paused.jsonl")
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
