from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest

DEFAULT_TEST_IMAGE = "alpine:3.20"
INTERNAL_INSTANCE_ID = "container-internal"
MOUNTED_INSTANCE_ID = "mounted-instance"


def _docker(
    *arguments: str,
    check: bool = True,
    timeout: float = 15,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *arguments],
        check=check,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture(scope="module")
def logging_container_image() -> str:
    try:
        daemon = _docker("info", check=False, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pytest.skip("Docker daemon 不可用")
    if daemon.returncode != 0:
        pytest.skip("Docker daemon 不可用")

    image = os.environ.get("LOGGING_CONTAINER_TEST_IMAGE", DEFAULT_TEST_IMAGE)
    if _docker("image", "inspect", image, check=False).returncode != 0:
        pytest.skip(f"本地未缓存轻量日志测试镜像: {image}")
    return image


def _container_name(suffix: str) -> str:
    return f"algorithm-logging-{suffix}-{uuid.uuid4().hex}"


def _remove_container(name: str) -> None:
    _docker("rm", "--force", name, check=False)


def _wait_for_file(name: str, path: str, expected: str) -> str:
    deadline = time.monotonic() + 10
    while True:
        completed = _docker("exec", name, "cat", path, check=False)
        if completed.returncode == 0 and expected in completed.stdout:
            return completed.stdout
        if time.monotonic() >= deadline:
            pytest.fail(
                f"容器日志未在期限内写入: container={name}, path={path}, "
                f"stderr={completed.stderr!r}"
            )
        time.sleep(0.1)


@pytest.mark.integration
def test_container_writes_instance_log_without_a_host_mount(
    logging_container_image: str,
) -> None:
    container = _container_name("internal")
    log_path = f"/workspace/logs/{INTERNAL_INSTANCE_ID}/application.log"
    event = '{"event":"container-internal-write","instance_id":"container-internal"}'
    command = (
        f"mkdir -p /workspace/logs/{INTERNAL_INSTANCE_ID}; "
        f"printf '%s\\n' '{event}' > {log_path}; "
        "exec tail -f /dev/null"
    )

    try:
        _docker(
            "run",
            "--detach",
            "--name",
            container,
            "--network",
            "none",
            logging_container_image,
            "sh",
            "-eu",
            "-c",
            command,
        )

        mounts = _docker("inspect", "--format", "{{json .Mounts}}", container)
        assert json.loads(mounts.stdout) == []
        container_log = _wait_for_file(container, log_path, event)
        writable = _docker("exec", container, "test", "-w", str(Path(log_path).parent))

        assert writable.returncode == 0
        assert container_log.splitlines() == [event]
    finally:
        _remove_container(container)


@pytest.mark.integration
def test_host_mount_preserves_logs_and_archives_across_container_replacement(
    tmp_path: Path,
    logging_container_image: str,
) -> None:
    host_instance_dir = tmp_path / MOUNTED_INSTANCE_ID
    host_instance_dir.mkdir()
    container_instance_dir = f"/workspace/logs/{MOUNTED_INSTANCE_ID}"
    container_log_path = f"{container_instance_dir}/application.log"
    archive_path = host_instance_dir / "application.log.20260821T000000Z.000001"
    archive_payload = b"archive-before-replacement\n"
    archive_path.write_bytes(archive_payload)
    archive_mtime_ns = archive_path.stat().st_mtime_ns

    first_event = '{"event":"before-replacement","instance_id":"mounted-instance"}'
    second_event = '{"event":"after-replacement","instance_id":"mounted-instance"}'
    first_container = _container_name("mounted-first")
    second_container = _container_name("mounted-second")
    mount = (
        f"type=bind,source={host_instance_dir.resolve()},destination={container_instance_dir}"
    )

    try:
        first_command = (
            f"printf '%s\\n' '{first_event}' > {container_log_path}; "
            "exec tail -f /dev/null"
        )
        _docker(
            "run",
            "--detach",
            "--name",
            first_container,
            "--network",
            "none",
            "--mount",
            mount,
            logging_container_image,
            "sh",
            "-eu",
            "-c",
            first_command,
        )
        _wait_for_file(first_container, container_log_path, first_event)
        _remove_container(first_container)

        second_command = (
            f"printf '%s\\n' '{second_event}' >> {container_log_path}; "
            "exec tail -f /dev/null"
        )
        _docker(
            "run",
            "--detach",
            "--name",
            second_container,
            "--network",
            "none",
            "--mount",
            mount,
            logging_container_image,
            "sh",
            "-eu",
            "-c",
            second_command,
        )

        container_log = _wait_for_file(second_container, container_log_path, second_event)
        host_log = (host_instance_dir / "application.log").read_text(encoding="utf-8")
        mounts = json.loads(
            _docker("inspect", "--format", "{{json .Mounts}}", second_container).stdout
        )

        assert container_log == host_log
        assert host_log.splitlines() == [first_event, second_event]
        assert len(mounts) == 1
        assert mounts[0]["Type"] == "bind"
        assert Path(mounts[0]["Source"]).resolve() == host_instance_dir.resolve()
        assert mounts[0]["Destination"] == container_instance_dir
        assert archive_path.read_bytes() == archive_payload
        assert archive_path.stat().st_mtime_ns == archive_mtime_ns
    finally:
        _remove_container(first_container)
        _remove_container(second_container)
