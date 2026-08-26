from __future__ import annotations

import argparse
import json
import os
import runpy
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from deploy.scripts import verify_operator_registration as registration_producer
from scripts.milestone_2b_case_runners import gpu as gpu_cases

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
VERIFIER = PLATFORM_ROOT / "deploy/scripts/verify-gpu-instance"
RELEASE_SHA = "a" * 40
RELEASE_TAG = "v1.0_260812"
CONTAINER_ID = "b" * 64
OTHER_CONTAINER_ID = "b" * 63 + "c"


def _gpu_case_scenario(
    release_root: Path,
    case_id: str,
    *,
    passing: bool,
    registration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    container = gpu_cases._TARGET_CONTAINERS[case_id]
    inventory = gpu_cases.load_operator_inventory(
        PLATFORM_ROOT / gpu_cases._OPERATOR_COMPOSE
    )
    instance = next(
        item for item in inventory.gpu_instances if item.instance_id == container
    )
    target = {
        "container": instance.service_name,
        "instance_id": instance.instance_id,
        "physical_gpu": instance.physical_gpu,
        "process_name": instance.process_name,
    }
    running: dict[str, Any] = {
        "schema_version": 1,
        "timestamp": "2026-08-19T00:00:00+00:00",
        "commands": ["verify-gpu-instance --running"],
        "mode": "running-inference",
        "status": "PASS" if passing else "FAIL",
        "target": target,
    }
    stopped: dict[str, Any] = {
        "schema_version": 1,
        "timestamp": "2026-08-19T00:01:00+00:00",
        "commands": ["verify-gpu-instance --assert-stopped"],
        "mode": "assert-stopped",
        "status": "PASS" if passing else "FAIL",
        "target": target,
    }
    if passing:
        container_evidence = {
            "id": CONTAINER_ID,
            "name": instance.service_name,
            "instance_id": instance.instance_id,
            "init_host_pid": 1000,
        }
        gpu_evidence = {
            "physical_index": instance.physical_gpu,
            "physical_uuid": f"GPU-{instance.physical_gpu}",
            "container_visible": str(instance.physical_gpu),
        }
        process = {
            "process_name": instance.process_name,
            "host_pid": 2000,
            "container_pid": 42,
            "mapping": {
                "docker_top": True,
                "cgroup_full_container_id": True,
                "nspid": [2000, 42],
            },
        }
        running.update(
            {
                "release_sha": RELEASE_SHA,
                "container": container_evidence,
                "gpu": gpu_evidence,
                "activity": {
                    "instance_id": instance.instance_id,
                    "operator_code": instance.operator_code,
                    "run_id": f"{case_id.lower()}-run",
                },
                "synchronous_samples": [{"processes": [process]}],
            }
        )
        stopped.update(
            {
                "release_sha": RELEASE_SHA,
                "container": container_evidence,
                "gpu": gpu_evidence,
                "prior_cuda_pids": [2000],
                "remaining_cuda_pids": [],
            }
        )
    else:
        running["reason"] = "nvidia-smi GPU telemetry 数值字段格式异常"
        stopped["reason"] = "先前 GPU 证据不是 PASS"

    running_path = release_root / f"gpu-instances/{container}.json"
    stopped_path = release_root / f"recovery/{container}-stopped.json"
    running_path.parent.mkdir(parents=True, exist_ok=True)
    stopped_path.parent.mkdir(parents=True, exist_ok=True)
    running_path.write_text(json.dumps(running), encoding="utf-8")
    stopped_path.write_text(json.dumps(stopped), encoding="utf-8")
    if case_id == "GPU-018" and registration is not None:
        registration_path = (
            release_root
            / f"registration/operator-registration-instance-{container}.json"
        )
        registration_path.parent.mkdir(parents=True, exist_ok=True)
        registration_path.write_text(json.dumps(registration), encoding="utf-8")

    scenario: dict[str, Any] = {
        "schema_version": 1,
        "case_id": case_id,
        "mode": gpu_cases.CASE_SPECS[case_id].mode,
        "mutation": {"case": case_id},
        "container": container,
        "release_root": str(release_root),
        "git_sha": RELEASE_SHA,
        "operator_compose": gpu_cases._OPERATOR_COMPOSE.as_posix(),
        "running_evidence": f"gpu-instances/{container}.json",
        "stopped_evidence": f"recovery/{container}-stopped.json",
    }
    if case_id == "GPU-018":
        scenario["registration_evidence"] = (
            f"registration/operator-registration-instance-{container}.json"
        )
    return scenario


@pytest.mark.parametrize("case_id", tuple(f"GPU-{index:03d}" for index in range(3, 21)))
def test_gpu_checker_rejects_failed_canonical_pair_as_one_strict_json_object(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    case_id: str,
) -> None:
    release_root = tmp_path / RELEASE_SHA
    scenario = _gpu_case_scenario(
        release_root,
        case_id,
        passing=False,
        registration={} if case_id == "GPU-018" else None,
    )
    checker_input = tmp_path / f"{case_id}.json"
    checker_input.write_text(json.dumps(scenario), encoding="utf-8")

    return_code = gpu_cases.checker_main(
        ["--check", case_id, "--input", str(checker_input)]
    )

    captured = capsys.readouterr()
    output_lines = captured.out.splitlines()
    assert return_code == 1
    assert captured.err == ""
    assert len(output_lines) == 1
    result = json.loads(output_lines[0])
    assert result["status"] == "失败"
    assert "canonical running and stopped evidence must both be PASS" in result["reason"]


def test_gpu_checker_normalizes_pass_evidence_shape_error_to_strict_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    release_root = tmp_path / RELEASE_SHA
    scenario = _gpu_case_scenario(release_root, "GPU-008", passing=True)
    running_path = release_root / "gpu-instances/facerec-gpu0.json"
    running = json.loads(running_path.read_text(encoding="utf-8"))
    running["synchronous_samples"].insert(0, {"processes": []})
    running_path.write_text(json.dumps(running), encoding="utf-8")
    checker_input = tmp_path / "GPU-008.json"
    checker_input.write_text(json.dumps(scenario), encoding="utf-8")

    return_code = gpu_cases.checker_main(
        ["--check", "GPU-008", "--input", str(checker_input)]
    )

    captured = capsys.readouterr()
    output_lines = captured.out.splitlines()
    assert return_code == 1
    assert captured.err == ""
    assert len(output_lines) == 1
    result = json.loads(output_lines[0])
    assert result["status"] == "失败"
    assert "GPU checker 未观察到目标状态" in result["reason"]


@pytest.mark.parametrize(
    ("case_id", "expected_stage"),
    (("GPU-012", "startup"), ("GPU-013", "concurrent_inference")),
)
def test_gpu_oom_cases_validate_the_synthetic_failure_after_a_passing_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case_id: str,
    expected_stage: str,
) -> None:
    observed_failures: list[dict[str, Any] | None] = []
    original_validator = gpu_cases._no_oom_validator

    def recording_validator(*args: Any) -> None:
        running = args[1]
        observed_failures.append(running.get("failure"))
        original_validator(*args)

    monkeypatch.setattr(gpu_cases, "_no_oom_validator", recording_validator)
    scenario = _gpu_case_scenario(
        tmp_path / RELEASE_SHA,
        case_id,
        passing=True,
    )

    result = gpu_cases.evaluate_scenario(case_id, scenario)

    assert result["status"] == "通过"
    assert observed_failures[0] is None
    assert observed_failures[1]["stage"] == expected_stage
    assert "OOM rejected" in result["observed"]["rejection_detail"]


def test_gpu_018_rejects_flat_registration_fixture(tmp_path: Path) -> None:
    scenario = _gpu_case_scenario(
        tmp_path / RELEASE_SHA,
        "GPU-018",
        passing=True,
        registration={
            "instance_id": "facerec-gpu0",
            "labels": {"gpu": "0"},
        },
    )

    result = gpu_cases.evaluate_scenario("GPU-018", scenario)

    assert result["status"] == "失败"
    assert "production instance registration envelope" in result["reason"]


def test_gpu_018_consumes_the_instance_record_emitted_by_registration_producer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reports_root = tmp_path / "reports"
    expected = registration_producer.load_expected(
        registration_producer.COMPOSE_PATH
    )["facerec-gpu0"]
    observed_instance = {
        "instance_id": "facerec-gpu0",
        "operator_code": expected["operator_code"],
        "capabilities": sorted(expected["capabilities"]),
        "service_url": expected["service_url"],
        "declared_capacity": expected["declared_capacity"],
        "labels": {
            "gpu": expected["gpu"],
            "management_token": "must-not-be-persisted",
        },
        "lifecycle": "ONLINE",
        "inflight": 0,
        "model_ready": True,
        "last_heartbeat_at": "2026-08-19T00:00:00Z",
        "password": "must-not-be-persisted",
    }
    arguments = argparse.Namespace(
        control_url="http://127.0.0.1:18100",
        release_tag=RELEASE_TAG,
        git_sha=RELEASE_SHA,
        reports_root=reports_root,
        expected_compose=registration_producer.COMPOSE_PATH,
        full=False,
        profile=[],
        instance=["facerec-gpu0"],
        evidence_checkpoint=None,
        timeout_seconds=1.0,
        poll_seconds=0.01,
        request_timeout_seconds=0.2,
    )

    def fake_get_json(url: str, timeout: float) -> Any:
        del timeout
        if url.endswith("/ops/operator-instances"):
            return [observed_instance]
        if url.endswith("/events?limit=100"):
            return [
                {
                    "event_type": "HEARTBEAT_SUMMARY",
                    "event_payload": {"model_ready": True},
                }
            ]
        raise AssertionError(f"unexpected registration URL: {url}")

    monkeypatch.setattr(registration_producer, "parse_args", lambda: arguments)
    monkeypatch.setattr(registration_producer, "get_json", fake_get_json)

    assert registration_producer.main() == 0
    release_root = (
        reports_root
        / "milestone-2b"
        / "releases"
        / RELEASE_TAG
        / RELEASE_SHA
    )
    registration_path = (
        release_root
        / "registration/operator-registration-instance-facerec-gpu0.json"
    )
    registration = json.loads(registration_path.read_text(encoding="utf-8"))
    assert registration["validated_instances"] == [
        {
            "instance_id": "facerec-gpu0",
            "operator_code": expected["operator_code"],
            "capabilities": sorted(expected["capabilities"]),
            "service_url": expected["service_url"],
            "declared_capacity": expected["declared_capacity"],
            "labels": {"gpu": expected["gpu"]},
            "lifecycle": "ONLINE",
            "inflight": 0,
            "model_ready": True,
            "last_heartbeat_at": "2026-08-19T00:00:00Z",
        }
    ]
    assert "must-not-be-persisted" not in registration_path.read_text(encoding="utf-8")
    scenario = _gpu_case_scenario(
        release_root,
        "GPU-018",
        passing=True,
    )

    result = gpu_cases.evaluate_scenario("GPU-018", scenario)

    assert result["status"] == "通过"
    assert result["observed"]["registration_label_rejection"] is True


def _write_process_stat(proc_root: Path, pid: int, *, state: str, process_group: int) -> None:
    process = proc_root / str(pid)
    process.mkdir(parents=True, exist_ok=True)
    (process / "stat").write_text(
        f"{pid} (worker with spaces) {state} 1 {process_group} {process_group}\n",
        encoding="utf-8",
    )


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _write_proc_process(
    proc_root: Path,
    pid: int,
    *,
    container_id: str = CONTAINER_ID,
    container_pid: int = 42,
    comm: str = "python",
) -> None:
    process = proc_root / str(pid)
    process.mkdir(parents=True, exist_ok=True)
    (process / "cgroup").write_text(
        f"0::/system.slice/docker-{container_id}.scope\n", encoding="utf-8"
    )
    (process / "status").write_text(
        f"Name:\t{comm}\nNSpid:\t{pid}\t{container_pid}\n", encoding="utf-8"
    )
    (process / "comm").write_text(f"{comm}\n", encoding="utf-8")
    (process / "cmdline").write_bytes(b"asr_offline\x00-m\x00uvicorn\x00")


def _write_v1_proc_process(proc_root: Path, pid: int) -> None:
    _write_proc_process(proc_root, pid)
    (proc_root / str(pid) / "cgroup").write_text(
        f"11:devices:/docker/{CONTAINER_ID}\n"
        f"1:name=systemd:/docker/{CONTAINER_ID}\n",
        encoding="utf-8",
    )


def _base_inspect(
    *,
    running: bool = True,
    container_id: str = CONTAINER_ID,
    gpu_id: str = "0",
    device_ids: list[str] | None = None,
    visible_device: str | None = None,
    instance_id: str = "asr-offline-gpu0",
) -> dict[str, Any]:
    return {
        "Id": container_id,
        "Name": f"/{instance_id}",
        "State": {"Running": running, "Pid": 1000 if running else 0},
        "Config": {
            "Env": [
                f"PLATFORM_INSTANCE_ID={instance_id}",
                f"PLATFORM_GPU_ID={gpu_id}",
                f"NVIDIA_VISIBLE_DEVICES={visible_device or gpu_id}",
            ],
            "Labels": {"org.opencontainers.image.revision": RELEASE_SHA},
        },
        "HostConfig": {
            "PortBindings": {
                "8083/tcp": [
                    {"HostIp": "127.0.0.1", "HostPort": "18083"},
                    {"HostIp": "::1", "HostPort": "18083"},
                ]
            },
            "DeviceRequests": [
                {
                    "Driver": "nvidia",
                    "DeviceIDs": device_ids or [gpu_id],
                    "Capabilities": [["gpu"]],
                }
            ]
        },
    }


@pytest.fixture
def gpu_runtime(tmp_path: Path) -> dict[str, Any]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    proc_root = tmp_path / "proc"
    _write_proc_process(proc_root, 1000, container_pid=1, comm="python")
    _write_proc_process(proc_root, 2000)
    trigger_marker = tmp_path / "trigger-running"
    inspect_path = tmp_path / "inspect.json"
    inspect_path.write_text(json.dumps(_base_inspect()), encoding="utf-8")

    _write_executable(
        fake_bin / "docker",
        f"""#!{sys.executable}
import json, os, pathlib, sys
import time
args = sys.argv[1:]
inspect = json.loads(pathlib.Path(os.environ["FAKE_INSPECT"]).read_text())
target_container = os.environ.get("FAKE_TARGET_CONTAINER", "asr-offline-gpu0")
if len(args) >= 2 and args[0] == "inspect" and args[1] in {{target_container, "{CONTAINER_ID}"}}:
    marker = os.environ.get("FAKE_RESTART_MARKER")
    if marker and pathlib.Path(marker).exists():
        inspect[0 if isinstance(inspect, list) else "Id"] = os.environ["FAKE_RESTART_ID"]
    print(json.dumps(inspect if isinstance(inspect, list) else [inspect]))
    raise SystemExit(0)
if (
    len(args) >= 3
    and args[0] == "top"
    and args[1] in {{target_container, "{CONTAINER_ID}"}}
    and args[2] == "-eo"
):
    time.sleep(float(os.environ.get("FAKE_DOCKER_TOP_DELAY", "0")))
    print("PID")
    print("1000")
    print("2000")
    raise SystemExit(0)
if len(args) >= 2 and args[0] == "exec" and args[1] in {{target_container, "{CONTAINER_ID}"}}:
    time.sleep(float(os.environ.get("FAKE_DOCKER_EXEC_DELAY", "0")))
    if "nvidia-smi" in args:
        print(os.environ.get("FAKE_CONTAINER_GPU_ROWS", "0, GPU-A"))
        raise SystemExit(0)
    print(os.environ.get("FAKE_PROBE", json.dumps({{
        "framework_gpu_available": True,
        "device_count": 1,
        "current_device": 0,
        "container_cuda_runtime_version": "12.1",
    }})))
    raise SystemExit(int(os.environ.get("FAKE_PROBE_EXIT", "0")))
raise SystemExit(64)
""",
    )
    _write_executable(
        fake_bin / "nvidia-smi",
        f"""#!{sys.executable}
import os, pathlib, sys
import time
args = " ".join(sys.argv[1:])
time.sleep(float(os.environ.get("FAKE_NVIDIA_SMI_DELAY", "0")))
if "pmon" in args:
    print(os.environ.get(
        "FAKE_PMON_HEADER",
        "# gpu pid type sm mem enc dec command\\n# Idx # C/G % % % % name",
    ))
    print(os.environ.get(
        "FAKE_PMON_ROWS",
        "0 2000 C 30 10 0 0 asr_offline",
    ))
    raise SystemExit(0)
if "--query-gpu=" in args:
    if "name,compute_cap,driver_version" in args:
        print(os.environ.get(
            "FAKE_GPU_IDENTITY",
            "0, GPU-A, NVIDIA GeForce RTX 4090 D, 8.9, 570.172.08",
        ))
    elif "temperature.gpu" in args:
        print(os.environ.get(
            "FAKE_GPU_TELEMETRY",
            "0, GPU-A, 55, 90, 120, 350, 50, Not Active",
        ))
    else:
        print(os.environ.get("FAKE_GPU_ROWS", "0, GPU-A, 100"))
    raise SystemExit(0)
if "--query-compute-apps=" in args:
    marker = pathlib.Path(os.environ["TRIGGER_MARKER"])
    if marker.exists():
        rows = os.environ.get(
            "FAKE_PROCESS_ROWS_DURING", "GPU-A, 2000, asr_offline, 300"
        )
    else:
        rows = os.environ.get("FAKE_PROCESS_ROWS_BEFORE", "")
    if rows:
        print(rows)
    raise SystemExit(int(os.environ.get("FAKE_NVIDIA_PROCESS_EXIT", "0")))
if not args:
    print(os.environ.get(
        "FAKE_NVIDIA_SMI_OVERVIEW",
        "NVIDIA-SMI 570.172.08 Driver Version: 570.172.08 CUDA Version: 12.8",
    ))
    raise SystemExit(0)
raise SystemExit(64)
""",
    )
    _write_executable(
        fake_bin / "ps",
        f"""#!{sys.executable}
import os
print(os.environ.get("FAKE_CPU_ROWS", "2000 30.0"))
""",
    )
    trigger = tmp_path / "trigger.py"
    trigger.write_text(
        """import json, os, pathlib, time
marker = pathlib.Path(os.environ["TRIGGER_MARKER"])
mode = os.environ.get("ACTIVITY_MODE", "valid")
fd_value = os.environ.get("GPU_EVIDENCE_ACTIVITY_FD")
nonce = os.environ.get("GPU_EVIDENCE_ACTIVITY_NONCE")

def emit(event, **overrides):
    if fd_value is None or nonce is None:
        return
    payload = {
        "event": event,
        "nonce": nonce,
        "operator_code": os.environ.get("ACTIVITY_OPERATOR", "asr_offline"),
        "instance_id": os.environ.get("ACTIVITY_INSTANCE_ID", "asr-offline-gpu0"),
        "run_id": "gpu0-asr-run",
        "attempt": 1,
        "target_origin": os.environ.get(
            "ACTIVITY_TARGET_ORIGIN", "http://127.0.0.1:18083"
        ),
    }
    payload.update(overrides)
    os.write(int(fd_value), (json.dumps(payload) + "\\n").encode())

time.sleep(float(os.environ.get("ACTIVITY_PREP_SECONDS", "0")))
if mode == "valid":
    emit("start")
elif mode == "wrong_operator":
    emit("start", operator_code="ocr")
elif mode == "wrong_instance":
    emit("start", instance_id="asr-offline-gpu1")
elif mode == "wrong_nonce":
    emit("start", nonce="wrong-nonce")
elif mode == "malformed" and fd_value is not None:
    os.write(int(fd_value), b"not-json\\n")
elif mode == "finish_first":
    emit("finish")
elif mode == "start_only":
    emit("start")
elif mode == "finished_then_sleep":
    emit("start")
    emit("finish")
elif mode == "two_attempts":
    emit("start")
    emit("finish")
    time.sleep(float(os.environ.get("BETWEEN_ACTIVITY_SECONDS", "0")))
    emit("start", attempt=2)
elif mode == "origin_drift":
    emit("start")
elif mode == "attempt_origin_drift":
    emit("start")
    emit("finish")
    emit("start", attempt=2, target_origin="http://127.0.0.1:18084")
marker.write_text("running", encoding="utf-8")
time.sleep(float(os.environ.get("TRIGGER_SECONDS", "0.25")))
marker.unlink(missing_ok=True)
if mode == "valid":
    emit("finish")
elif mode == "two_attempts":
    emit("finish", attempt=2)
elif mode == "origin_drift":
    emit("finish", target_origin="http://127.0.0.1:28083")
elif mode == "attempt_origin_drift":
    emit("finish", attempt=2, target_origin="http://127.0.0.1:18084")
""",
        encoding="utf-8",
    )
    trigger_file = tmp_path / "trigger.json"
    trigger_file.write_text(json.dumps([sys.executable, str(trigger)]), encoding="utf-8")
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "GPU_EVIDENCE_PROC_ROOT": str(proc_root),
            "FAKE_INSPECT": str(inspect_path),
            "TRIGGER_MARKER": str(trigger_marker),
        }
    )
    output = (
        tmp_path
        / "reports/milestone-2b/releases/v1.0_260812"
        / RELEASE_SHA
        / "gpu-instances/asr-offline-gpu0.json"
    )
    output.parent.mkdir(parents=True)
    return {
        "env": environment,
        "output": output,
        "trigger_file": trigger_file,
        "inspect_path": inspect_path,
        "proc_root": proc_root,
        "tmp_path": tmp_path,
    }


def _wait_process_gone(pid: int) -> None:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.02)
    pytest.fail(f"process {pid} still exists")


def _run(runtime: dict[str, Any], *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(VERIFIER),
            "--container",
            "asr-offline-gpu0",
            "--physical-gpu",
            "0",
            "--process-name",
            "asr_offline",
            "--output",
            str(runtime["output"]),
            "--trigger-file",
            str(runtime["trigger_file"]),
            "--sample-window",
            "0.6",
            "--sample-interval",
            "0.02",
            *extra,
        ],
        env=runtime["env"],
        text=True,
        capture_output=True,
        check=False,
    )


def test_verifier_separates_compose_container_id_from_platform_instance_id(
    gpu_runtime: dict[str, Any],
) -> None:
    completed = subprocess.run(
        [
            str(VERIFIER),
            "--container",
            CONTAINER_ID,
            "--instance-id",
            "asr-offline-gpu0",
            "--physical-gpu",
            "0",
            "--process-name",
            "asr_offline",
            "--output",
            str(gpu_runtime["output"]),
            "--trigger-file",
            str(gpu_runtime["trigger_file"]),
            "--sample-window",
            "0.6",
            "--sample-interval",
            "0.02",
        ],
        env=gpu_runtime["env"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    report = _report(gpu_runtime)
    assert report["target"]["container"] == CONTAINER_ID
    assert report["target"]["instance_id"] == "asr-offline-gpu0"


def _report(runtime: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(runtime["output"].read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_verifier_records_synchronous_cuda_pid_and_exact_container_mapping(
    gpu_runtime: dict[str, Any],
) -> None:
    completed = _run(gpu_runtime)

    assert completed.returncode == 0, completed.stderr
    report = _report(gpu_runtime)
    assert report["status"] == "PASS"
    assert report["release_sha"] == RELEASE_SHA
    assert report["container"] == {
        "id": CONTAINER_ID,
        "name": "asr-offline-gpu0",
        "instance_id": "asr-offline-gpu0",
        "init_host_pid": 1000,
    }
    assert report["gpu"]["physical_index"] == 0
    assert report["gpu"]["physical_uuid"] == "GPU-A"
    assert report["cuda_probe"]["device_count"] == 1
    assert report["cuda_probe"]["current_device"] == 0
    assert report["cuda_probe"]["container_cuda_runtime_version"] == "12.1"
    assert report["container_gpu_inventory"] == [{"index": 0, "uuid": "GPU-A"}]
    assert report["framework_probe"]["framework"] == "torch"
    process = report["synchronous_samples"][0]["processes"][0]
    assert process["host_pid"] == 2000
    assert process["container_pid"] == 42
    assert process["process_name"] == "asr_offline"
    assert process["used_memory_mib"] == 300
    assert process["mapping"]["docker_top"] is True
    assert process["mapping"]["cgroup_full_container_id"] is True
    assert report["memory_mib"]["before"] == 100
    assert report["memory_mib"]["during"] == 100
    assert report["hardware"] == {
        "temperature_c": 55.0,
        "temperature_limit_c": 90.0,
        "power_watts": 120.0,
        "power_limit_watts": 350.0,
        "hardware_slowdown": False,
    }
    assert report["utilization"] == {
        "cpu_percent": 30.0,
        "gpu_percent": 50.0,
        "target_sm_percent": 30.0,
    }
    assert report["synchronous_samples"][0]["gpu_utilization_percent"] == 50.0
    assert process["cpu_percent"] == 30.0
    assert process["gpu_utilization"] == {
        "sm_percent": 30.0,
        "memory_percent": 10.0,
        "encoder_percent": 0.0,
        "decoder_percent": 0.0,
    }


    assert report["compatibility"] == {
        "gpu": {
            "physical_index": 0,
            "physical_uuid": "GPU-A",
            "product_name": "NVIDIA GeForce RTX 4090 D",
            "compute_capability": "8.9",
            "driver_version": "570.172.08",
            "driver_cuda_version": "12.8",
            "container_cuda_runtime_version": "12.1",
        },
        "trigger": {
            "instance_id": "asr-offline-gpu0",
            "operator_code": "asr_offline",
            "run_id": "gpu0-asr-run",
        },
        "result": {
            "status": "PASS",
            "real_trigger_completed": True,
            "sample_count": len(report["synchronous_samples"]),
            "target_sm_max_percent": 30.0,
        },
    }
    assert report["trigger"] == {"executable": Path(sys.executable).name, "argument_count": 1}
    assert report["activity"] == {
        "protocol": "inherited-fd-v1",
        "operator_code": "asr_offline",
        "instance_id": "asr-offline-gpu0",
        "target_origin": "http://127.0.0.1:18083",
        "run_id": "gpu0-asr-run",
        "attempts": [
            {
                "attempt": 1,
                "sample_count": len(report["synchronous_samples"]),
                "started_at": report["activity"]["attempts"][0]["started_at"],
                "finished_at": report["activity"]["attempts"][0]["finished_at"],
            }
        ],
    }
    assert report["commands"] == [
        "docker inspect <container>",
        "docker top <container> -eo pid",
        "docker exec <container> <cuda-probe-argv>",
        "nvidia-smi --query-gpu=<fields> --format=csv,noheader,nounits",
        "nvidia-smi <driver-cuda-overview>",
        "nvidia-smi --query-compute-apps=<fields> --format=csv,noheader,nounits",
        "nvidia-smi pmon -i <physical-gpu> -c 1 -s u",
        "ps -o pid=,%cpu= -p <mapped-pids>",
        "<trigger-executable> <redacted-arguments>",
    ]
    assert gpu_runtime["output"].stat().st_mode & 0o777 == 0o600


def test_verifier_accepts_image_default_process_name_without_legacy_environment(
    gpu_runtime: dict[str, Any],
) -> None:
    inspect = _base_inspect()
    assert all(
        not item.startswith("GPU_PROCESS_NAME=")
        for item in inspect["Config"]["Env"]
    )
    gpu_runtime["inspect_path"].write_text(json.dumps(inspect), encoding="utf-8")

    completed = _run(gpu_runtime)

    assert completed.returncode == 0, completed.stderr
    assert _report(gpu_runtime)["status"] == "PASS"


def test_verifier_rejects_legacy_gpu_process_name_environment(
    gpu_runtime: dict[str, Any],
) -> None:
    inspect = _base_inspect()
    inspect["Config"]["Env"].append("GPU_PROCESS_NAME=asr_offline")
    gpu_runtime["inspect_path"].write_text(json.dumps(inspect), encoding="utf-8")

    completed = _run(gpu_runtime)

    assert completed.returncode != 0
    assert "GPU_PROCESS_NAME" in _report(gpu_runtime)["reason"]


def test_verifier_rejects_process_name_that_does_not_match_instance(
    gpu_runtime: dict[str, Any],
) -> None:
    completed = _run_with_process(gpu_runtime, "asr-offline-gpu0", "ocr")

    assert completed.returncode != 0
    assert "默认值" in _report(gpu_runtime)["reason"]


def test_verifier_rejects_driver_cuda_without_container_runtime(
    gpu_runtime: dict[str, Any],
) -> None:
    gpu_runtime["env"]["FAKE_PROBE"] = json.dumps(
        {
            "framework_gpu_available": True,
            "device_count": 1,
            "current_device": 0,
        }
    )

    completed = _run(gpu_runtime)

    assert completed.returncode != 0
    assert "container CUDA runtime" in _report(gpu_runtime)["reason"]


def test_verifier_records_container_runtime_separately_from_driver_cuda(
    gpu_runtime: dict[str, Any],
) -> None:
    completed = _run(gpu_runtime)

    assert completed.returncode == 0, completed.stderr
    compatibility = _report(gpu_runtime)["compatibility"]["gpu"]
    assert compatibility["container_cuda_runtime_version"] == "12.1"
    assert compatibility["driver_cuda_version"] == "12.8"


def test_verifier_ignores_unavailable_telemetry_on_another_gpu(
    gpu_runtime: dict[str, Any],
) -> None:
    gpu_runtime["env"]["FAKE_GPU_TELEMETRY"] = (
        "0, GPU-A, 55, 90, 120, 350, 50, Not Active\n"
        "1, GPU-B, 56, 89, 130, 350, 40, Not Active\n"
        "2, GPU-C, 54, [N/A], 80, 350, 20, Not Active"
    )

    completed = _run(gpu_runtime)

    assert completed.returncode == 0, completed.stderr
    assert _report(gpu_runtime)["hardware"]["temperature_limit_c"] == 90.0


def test_gpu_telemetry_preserves_unavailable_temperature_limit_as_null(
    gpu_runtime: dict[str, Any],
) -> None:
    gpu_runtime["env"]["FAKE_GPU_TELEMETRY"] = (
        "0, GPU-A, 55, [N/A], 120, 350, 50, Not Active"
    )

    completed = _run(gpu_runtime)

    assert completed.returncode == 0, completed.stderr
    assert _report(gpu_runtime)["hardware"]["temperature_limit_c"] is None


def test_verifier_attributes_sm_to_target_pid_when_another_gpu_process_is_busy(
    gpu_runtime: dict[str, Any],
) -> None:
    gpu_runtime["env"].update(
        {
            "FAKE_CPU_ROWS": "2000 95.0",
            "FAKE_GPU_TELEMETRY": "0, GPU-A, 55, 90, 120, 350, 90, Not Active",
            "FAKE_PMON_ROWS": (
                "0 2000 C 0 0 0 0 asr_offline\n"
                "0 3000 C 90 40 0 0 neighboring_process"
            ),
        }
    )

    completed = _run(gpu_runtime)

    assert completed.returncode == 0, completed.stderr
    report = _report(gpu_runtime)
    process = report["synchronous_samples"][0]["processes"][0]
    assert report["utilization"] == {
        "cpu_percent": 95.0,
        "gpu_percent": 90.0,
        "target_sm_percent": 0.0,
    }
    assert process["host_pid"] == 2000
    assert process["gpu_utilization"]["sm_percent"] == 0.0


def test_verifier_preserves_unavailable_pmon_metrics_as_null(
    gpu_runtime: dict[str, Any],
) -> None:
    gpu_runtime["env"].update(
        {
            "FAKE_PMON_HEADER": (
                "# gpu pid type sm mem enc dec jpg ofa command\n"
                "# Idx # C/G % % % % % % name"
            ),
            "FAKE_PMON_ROWS": "0 2000 C - - - - - - asr_offline",
        }
    )

    completed = _run(gpu_runtime)

    assert completed.returncode == 0, completed.stderr
    report = _report(gpu_runtime)
    process = report["synchronous_samples"][0]["processes"][0]
    assert process["gpu_utilization"] == {
        "sm_percent": None,
        "memory_percent": None,
        "encoder_percent": None,
        "decoder_percent": None,
    }
    assert report["utilization"]["target_sm_percent"] is None
    assert report["compatibility"]["result"]["target_sm_max_percent"] is None


def test_sample_window_starts_after_first_valid_activity_start(
    gpu_runtime: dict[str, Any],
) -> None:
    gpu_runtime["env"]["ACTIVITY_PREP_SECONDS"] = "0.75"

    completed = _run(gpu_runtime, "--trigger-timeout", "2")

    assert completed.returncode == 0, completed.stderr
    report = _report(gpu_runtime)
    assert report["status"] == "PASS"
    assert report["activity"]["attempts"][0]["sample_count"] >= 1


def test_sample_window_does_not_reset_for_later_activity_attempt(
    gpu_runtime: dict[str, Any],
) -> None:
    gpu_runtime["env"].update(
        {
            "ACTIVITY_MODE": "two_attempts",
            "BETWEEN_ACTIVITY_SECONDS": "0.75",
        }
    )

    completed = _run(gpu_runtime, "--trigger-timeout", "2")

    assert completed.returncode != 0
    assert "同步采样" in _report(gpu_runtime)["reason"]


def test_verifier_rejects_activity_target_bound_to_another_instance(
    gpu_runtime: dict[str, Any],
) -> None:
    gpu_runtime["env"]["ACTIVITY_TARGET_ORIGIN"] = "http://127.0.0.1:28083"

    completed = _run(gpu_runtime)

    assert completed.returncode != 0
    assert "published port" in _report(gpu_runtime)["reason"]


def test_verifier_rejects_target_origin_drift_within_one_attempt(
    gpu_runtime: dict[str, Any],
) -> None:
    gpu_runtime["env"]["ACTIVITY_MODE"] = "origin_drift"

    completed = _run(gpu_runtime)

    assert completed.returncode != 0
    assert "target_origin" in _report(gpu_runtime)["reason"]


def test_verifier_rejects_target_origin_drift_between_attempts(
    gpu_runtime: dict[str, Any],
) -> None:
    inspect = _base_inspect()
    inspect["HostConfig"]["PortBindings"]["8084/tcp"] = [
        {"HostIp": "127.0.0.1", "HostPort": "18084"}
    ]
    gpu_runtime["inspect_path"].write_text(json.dumps(inspect), encoding="utf-8")
    gpu_runtime["env"]["ACTIVITY_MODE"] = "attempt_origin_drift"

    completed = _run(gpu_runtime)

    assert completed.returncode != 0
    assert "target_origin" in _report(gpu_runtime)["reason"]


@pytest.mark.parametrize(
    "target_origin",
    (
        "http://192.0.2.10:18083",
        "http://127.0.0.1",
        "http://127.0.0.1:18083/private/fixture.png",
        "http://127.0.0.1:18083?token=secret-query",
        "http://user:secret-password@127.0.0.1:18083",
    ),
)
def test_verifier_rejects_non_loopback_or_non_origin_activity_target(
    gpu_runtime: dict[str, Any], target_origin: str
) -> None:
    gpu_runtime["env"]["ACTIVITY_TARGET_ORIGIN"] = target_origin

    completed = _run(gpu_runtime)

    assert completed.returncode != 0
    report_bytes = gpu_runtime["output"].read_bytes()
    assert "target_origin" in _report(gpu_runtime)["reason"]
    assert b"private/fixture.png" not in report_bytes
    assert b"secret-query" not in report_bytes
    assert b"secret-password" not in report_bytes


@pytest.mark.parametrize(
    "port_bindings",
    (
        None,
        {},
        {"8083/tcp": None},
        {"invalid": [{"HostIp": "127.0.0.1", "HostPort": "18083"}]},
        {"8083/tcp": [{"HostIp": "192.0.2.10", "HostPort": "18083"}]},
        {"8083/tcp": [{"HostIp": "127.0.0.1", "HostPort": "invalid"}]},
        {"8083/tcp": [{"HostIp": "127.0.0.1"}]},
    ),
)
def test_verifier_rejects_missing_or_invalid_port_bindings(
    gpu_runtime: dict[str, Any], port_bindings: Any
) -> None:
    inspect = _base_inspect()
    if port_bindings is None:
        del inspect["HostConfig"]["PortBindings"]
    else:
        inspect["HostConfig"]["PortBindings"] = port_bindings
    gpu_runtime["inspect_path"].write_text(json.dumps(inspect), encoding="utf-8")

    completed = _run(gpu_runtime)

    assert completed.returncode != 0
    assert "PortBindings" in _report(gpu_runtime)["reason"]


@pytest.mark.parametrize(
    ("target_origin", "host_ip"),
    (
        ("http://127.0.0.1:18083", ""),
        ("http://127.0.0.1:18083", "0.0.0.0"),
        ("http://127.0.0.1:18083", "127.0.0.1"),
        ("http://[::1]:18083", "::"),
        ("http://[::1]:18083", "::1"),
    ),
)
def test_verifier_accepts_loopback_or_wildcard_docker_binding(
    gpu_runtime: dict[str, Any], target_origin: str, host_ip: str
) -> None:
    inspect = _base_inspect()
    inspect["HostConfig"]["PortBindings"] = {
        "8083/tcp": [{"HostIp": host_ip, "HostPort": "18083"}]
    }
    gpu_runtime["inspect_path"].write_text(json.dumps(inspect), encoding="utf-8")
    gpu_runtime["env"]["ACTIVITY_TARGET_ORIGIN"] = target_origin

    completed = _run(gpu_runtime)

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    ("target_origin", "host_ip"),
    (
        ("http://127.0.0.2:18083", "127.0.0.1"),
        ("http://127.0.0.1:18083", "::"),
        ("http://127.0.0.1:18083", "::1"),
        ("http://[::1]:18083", ""),
        ("http://[::1]:18083", "0.0.0.0"),
        ("http://[::1]:18083", "127.0.0.1"),
    ),
)
def test_verifier_rejects_other_loopback_or_cross_family_binding(
    gpu_runtime: dict[str, Any], target_origin: str, host_ip: str
) -> None:
    inspect = _base_inspect()
    inspect["HostConfig"]["PortBindings"] = {
        "8083/tcp": [{"HostIp": host_ip, "HostPort": "18083"}]
    }
    gpu_runtime["inspect_path"].write_text(json.dumps(inspect), encoding="utf-8")
    gpu_runtime["env"]["ACTIVITY_TARGET_ORIGIN"] = target_origin

    completed = _run(gpu_runtime)

    assert completed.returncode != 0
    assert "published binding" in _report(gpu_runtime)["reason"]


@pytest.mark.parametrize("value", ("0", "-1", "nan", "inf", "86400.01"))
def test_verifier_rejects_unbounded_trigger_timeout(
    gpu_runtime: dict[str, Any], value: str
) -> None:
    completed = _run(gpu_runtime, "--trigger-timeout", value)

    assert completed.returncode != 0
    assert "trigger timeout" in completed.stderr


@pytest.mark.parametrize(
    "activity_mode",
    (
        "no_activity",
        "finished_then_sleep",
        "wrong_operator",
        "wrong_instance",
        "wrong_nonce",
        "malformed",
        "finish_first",
        "start_only",
    ),
)
def test_verifier_rejects_non_request_activity_protocol(
    gpu_runtime: dict[str, Any], activity_mode: str
) -> None:
    gpu_runtime["env"]["ACTIVITY_MODE"] = activity_mode

    completed = _run(gpu_runtime)

    assert completed.returncode != 0
    report = _report(gpu_runtime)
    assert report["status"] == "FAIL"
    assert "activity" in report["reason"].lower() or "活动" in report["reason"]
    assert report["activity"] == {
        "protocol": "inherited-fd-v1",
        "operator_code": "asr_offline",
        "instance_id": "asr-offline-gpu0",
    }
    assert "wrong-nonce" not in json.dumps(report)


def test_verifier_accepts_exact_cgroup_v1_mapping(gpu_runtime: dict[str, Any]) -> None:
    _write_v1_proc_process(gpu_runtime["proc_root"], 2000)

    completed = _run(gpu_runtime)

    assert completed.returncode == 0, completed.stderr
    process = _report(gpu_runtime)["synchronous_samples"][0]["processes"][0]
    assert process["mapping"]["cgroup_full_container_id"] is True


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ({"gpu_id": "1"}, "PLATFORM_GPU_ID"),
        (
            {
                "probe": {
                    "framework_gpu_available": True,
                    "device_count": 2,
                    "current_device": 0,
                    "container_cuda_runtime_version": "12.1",
                }
            },
            "device_count",
        ),
        ({"process_rows": ""}, "同步采样"),
        ({"process_rows": "GPU-A, 2000, python, 300"}, "进程名"),
        ({"gpu_rows": "invalid"}, "nvidia-smi"),
    ],
)
def test_verifier_fails_closed_and_keeps_structured_failure_report(
    gpu_runtime: dict[str, Any], mutation: dict[str, Any], reason: str
) -> None:
    environment = gpu_runtime["env"]
    if "gpu_id" in mutation:
        gpu_runtime["inspect_path"].write_text(
            json.dumps(_base_inspect(gpu_id=mutation["gpu_id"])), encoding="utf-8"
        )
    if "probe" in mutation:
        environment["FAKE_PROBE"] = json.dumps(mutation["probe"])
    if "process_rows" in mutation:
        environment["FAKE_PROCESS_ROWS_DURING"] = mutation["process_rows"]
    if "gpu_rows" in mutation:
        environment["FAKE_GPU_ROWS"] = mutation["gpu_rows"]

    completed = _run(gpu_runtime)

    assert completed.returncode != 0
    report = _report(gpu_runtime)
    assert report["status"] == "FAIL"
    assert reason in report["reason"]
    assert reason in completed.stderr


@pytest.mark.parametrize("container_id", [OTHER_CONTAINER_ID, CONTAINER_ID + "0"])
def test_verifier_rejects_foreign_pid_and_container_id_prefix_collision(
    gpu_runtime: dict[str, Any], container_id: str
) -> None:
    _write_proc_process(gpu_runtime["proc_root"], 2000, container_id=container_id)

    completed = _run(gpu_runtime)

    assert completed.returncode != 0
    assert "cgroup" in _report(gpu_runtime)["reason"]


def test_verifier_rejects_trigger_that_finishes_before_a_synchronous_sample(
    gpu_runtime: dict[str, Any],
) -> None:
    gpu_runtime["env"]["TRIGGER_SECONDS"] = "0"

    completed = _run(gpu_runtime)

    assert completed.returncode != 0
    assert "同步采样" in _report(gpu_runtime)["reason"]


def test_verifier_discards_sample_when_trigger_finishes_during_collection(
    gpu_runtime: dict[str, Any],
) -> None:
    gpu_runtime["env"]["TRIGGER_SECONDS"] = "0.03"
    gpu_runtime["env"]["FAKE_DOCKER_TOP_DELAY"] = "0.08"

    completed = _run(gpu_runtime)

    assert completed.returncode != 0
    assert "同步采样" in _report(gpu_runtime)["reason"]


@pytest.mark.parametrize(
    ("instance_id", "process_name", "framework"),
    [
        ("ocr-gpu0", "ocr", "paddle"),
        ("facerec-gpu0", "facerec", "fastdeploy"),
    ],
)
def test_non_torch_operator_uses_framework_specific_probe(
    gpu_runtime: dict[str, Any],
    instance_id: str,
    process_name: str,
    framework: str,
) -> None:
    inspect = _base_inspect(instance_id=instance_id)
    gpu_runtime["inspect_path"].write_text(json.dumps(inspect), encoding="utf-8")
    gpu_runtime["env"]["FAKE_PROBE"] = json.dumps(
        {
            "framework_gpu_available": True,
            "device_count": 1,
            "current_device": 0,
            "container_cuda_runtime_version": "12.1",
        }
    )

    completed = _run_with_process(gpu_runtime, instance_id, process_name)

    assert completed.returncode == 0, completed.stderr
    report = _report(gpu_runtime)
    assert report["framework_probe"]["framework"] == framework
    assert "torch" not in report["framework_probe"]["command"]


def _run_with_process(
    runtime: dict[str, Any], instance_id: str, process_name: str, *extra: str
) -> subprocess.CompletedProcess[str]:
    command = [
        str(VERIFIER), "--container", instance_id, "--physical-gpu", "0",
        "--instance-id", instance_id, "--process-name", process_name,
        "--output", str(runtime["output"]),
        "--trigger-file", str(runtime["trigger_file"]), "--sample-window", "0.6",
        "--sample-interval", "0.02", *extra,
    ]
    runtime["env"]["FAKE_PROCESS_ROWS_DURING"] = f"GPU-A, 2000, {process_name}, 300"
    runtime["env"]["ACTIVITY_OPERATOR"] = process_name
    runtime["env"]["ACTIVITY_INSTANCE_ID"] = instance_id
    runtime["env"]["FAKE_TARGET_CONTAINER"] = instance_id
    return subprocess.run(command, env=runtime["env"], text=True, capture_output=True, check=False)


def test_device_requests_accept_gpu_uuid_when_it_resolves_to_physical_index(
    gpu_runtime: dict[str, Any],
) -> None:
    gpu_runtime["inspect_path"].write_text(
        json.dumps(_base_inspect(device_ids=["GPU-A"], visible_device="GPU-A")),
        encoding="utf-8",
    )

    completed = _run(gpu_runtime)

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    "device_ids", [["0", "1"], ["GPU-UNKNOWN"], ["GPU-B"]]
)
def test_device_requests_reject_multiple_unknown_or_mismatched_devices(
    gpu_runtime: dict[str, Any], device_ids: list[str]
) -> None:
    gpu_runtime["env"]["FAKE_GPU_ROWS"] = "0, GPU-A, 100\n1, GPU-B, 200"
    gpu_runtime["inspect_path"].write_text(
        json.dumps(_base_inspect(device_ids=device_ids)), encoding="utf-8"
    )

    completed = _run(gpu_runtime)

    assert completed.returncode != 0
    assert "device request" in _report(gpu_runtime)["reason"]


def test_verifier_rejects_container_restart_during_sampling(
    gpu_runtime: dict[str, Any],
) -> None:
    gpu_runtime["env"].update(
        {
            "FAKE_RESTART_MARKER": gpu_runtime["env"]["TRIGGER_MARKER"],
            "FAKE_RESTART_ID": "c" * 64,
        }
    )

    completed = _run(gpu_runtime)

    assert completed.returncode != 0
    assert "重启" in _report(gpu_runtime)["reason"]


def test_verifier_redacts_trigger_arguments_and_rejects_existing_conflict(
    gpu_runtime: dict[str, Any],
) -> None:
    secret = "token=super-secret-value"
    command = json.loads(gpu_runtime["trigger_file"].read_text(encoding="utf-8"))
    command.append(secret)
    gpu_runtime["trigger_file"].write_text(json.dumps(command), encoding="utf-8")
    first = _run(gpu_runtime)
    before = gpu_runtime["output"].read_bytes()

    second = _run(gpu_runtime)

    assert first.returncode == 0
    assert secret.encode() not in before
    assert second.returncode != 0
    assert gpu_runtime["output"].read_bytes() == before
    assert "已存在" in second.stderr


def test_failure_report_identifies_target_without_leaking_secret_trigger_arguments(
    gpu_runtime: dict[str, Any],
) -> None:
    secret = "password=should-not-appear"
    command = json.loads(gpu_runtime["trigger_file"].read_text(encoding="utf-8"))
    command.append(secret)
    gpu_runtime["trigger_file"].write_text(json.dumps(command), encoding="utf-8")
    gpu_runtime["env"]["FAKE_PROCESS_ROWS_DURING"] = ""

    completed = _run(gpu_runtime)

    report_bytes = gpu_runtime["output"].read_bytes()
    report = json.loads(report_bytes)
    assert completed.returncode != 0
    assert report["target"] == {
        "container": "asr-offline-gpu0",
        "instance_id": "asr-offline-gpu0",
        "physical_gpu": 0,
        "process_name": "asr_offline",
    }
    assert secret.encode() not in report_bytes


def test_verifier_rejects_output_symlink(gpu_runtime: dict[str, Any]) -> None:
    target = gpu_runtime["tmp_path"] / "outside.json"
    gpu_runtime["output"].symlink_to(target)

    completed = _run(gpu_runtime)

    assert completed.returncode != 0
    assert not target.exists()
    assert "软链接" in completed.stderr


def test_concurrent_writers_never_overwrite_a_published_report(
    gpu_runtime: dict[str, Any],
) -> None:
    first = subprocess.Popen(
        [
            str(VERIFIER),
            "--container",
            "asr-offline-gpu0",
            "--physical-gpu",
            "0",
            "--process-name",
            "asr_offline",
            "--output",
            str(gpu_runtime["output"]),
            "--trigger-file",
            str(gpu_runtime["trigger_file"]),
            "--sample-window",
            "0.6",
            "--sample-interval",
            "0.02",
        ],
        env=gpu_runtime["env"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    second = _run(gpu_runtime)
    first_stdout, first_stderr = first.communicate(timeout=5)

    assert sorted([first.returncode, second.returncode]) == [0, 2], (
        first_stdout,
        first_stderr,
        second.stdout,
        second.stderr,
    )
    assert _report(gpu_runtime)["status"] == "PASS"
    leftovers = list(gpu_runtime["output"].parent.glob(f".{gpu_runtime['output'].name}.*.tmp"))
    assert leftovers == []


def test_stopped_mode_rejects_only_prior_container_cuda_pid_residue(
    gpu_runtime: dict[str, Any],
) -> None:
    assert _run(gpu_runtime).returncode == 0
    prior = gpu_runtime["output"]
    recovery_output = prior.parent.parent / "recovery/asr-offline-gpu0-stopped.json"
    recovery_output.parent.mkdir()
    gpu_runtime["output"] = recovery_output
    gpu_runtime["inspect_path"].write_text(
        json.dumps(_base_inspect(running=False)), encoding="utf-8"
    )
    gpu_runtime["env"]["FAKE_PROCESS_ROWS_BEFORE"] = (
        "GPU-A, 2000, asr_offline, 300\nGPU-A, 3000, ocr, 200"
    )
    _write_proc_process(gpu_runtime["proc_root"], 3000, container_id="d" * 64)

    residual = _run(
        gpu_runtime,
        "--assert-stopped",
        "--evidence",
        str(prior),
        "--stop-timeout",
        "0.05",
    )
    assert residual.returncode != 0
    assert "残留" in _report(gpu_runtime)["reason"]

    gpu_runtime["env"]["FAKE_PROCESS_ROWS_BEFORE"] = "GPU-A, 3000, ocr, 200"
    recovery_output.unlink()
    clean = _run(
        gpu_runtime,
        "--assert-stopped",
        "--evidence",
        str(prior),
        "--stop-timeout",
        "0.05",
    )
    assert clean.returncode == 0, clean.stderr
    assert _report(gpu_runtime)["status"] == "PASS"


def test_stopped_mode_ignores_recycled_prior_pid_owned_by_another_container(
    gpu_runtime: dict[str, Any],
) -> None:
    assert _run(gpu_runtime).returncode == 0
    prior = gpu_runtime["output"]
    recovery_output = prior.parent.parent / "recovery/recycled-pid.json"
    recovery_output.parent.mkdir()
    gpu_runtime["output"] = recovery_output
    gpu_runtime["inspect_path"].write_text(
        json.dumps(_base_inspect(running=False)), encoding="utf-8"
    )
    gpu_runtime["env"]["FAKE_PROCESS_ROWS_BEFORE"] = (
        "GPU-A, 2000, asr_offline, 300"
    )
    _write_proc_process(
        gpu_runtime["proc_root"], 2000, container_id=OTHER_CONTAINER_ID
    )

    completed = _run(
        gpu_runtime,
        "--assert-stopped",
        "--evidence",
        str(prior),
        "--stop-timeout",
        "0.05",
    )

    assert completed.returncode == 0, completed.stderr
    assert _report(gpu_runtime)["remaining_cuda_pids"] == []


def test_stopped_mode_rejects_legacy_gpu_process_name_environment(
    gpu_runtime: dict[str, Any],
) -> None:
    assert _run(gpu_runtime).returncode == 0
    prior = gpu_runtime["output"]
    recovery_output = prior.parent.parent / "recovery/legacy-process-name.json"
    recovery_output.parent.mkdir()
    gpu_runtime["output"] = recovery_output
    inspect = _base_inspect(running=False)
    inspect["Config"]["Env"].append("GPU_PROCESS_NAME=asr_offline")
    gpu_runtime["inspect_path"].write_text(json.dumps(inspect), encoding="utf-8")
    gpu_runtime["env"]["FAKE_PROCESS_ROWS_BEFORE"] = ""

    completed = _run(
        gpu_runtime,
        "--assert-stopped",
        "--evidence",
        str(prior),
    )

    assert completed.returncode != 0
    assert "GPU_PROCESS_NAME" in _report(gpu_runtime)["reason"]


def test_stopped_mode_rejects_recreated_container_id(
    gpu_runtime: dict[str, Any],
) -> None:
    assert _run(gpu_runtime).returncode == 0
    prior = gpu_runtime["output"]
    recovery_output = prior.parent.parent / "recovery/recreated-container.json"
    recovery_output.parent.mkdir()
    gpu_runtime["output"] = recovery_output
    gpu_runtime["inspect_path"].write_text(
        json.dumps(_base_inspect(running=False, container_id="c" * 64)),
        encoding="utf-8",
    )
    gpu_runtime["env"]["FAKE_PROCESS_ROWS_BEFORE"] = ""

    completed = _run(
        gpu_runtime,
        "--assert-stopped",
        "--evidence",
        str(prior),
        "--stop-timeout",
        "0.05",
    )

    assert completed.returncode != 0
    assert "容器 ID" in _report(gpu_runtime)["reason"]


@pytest.mark.parametrize("mismatch_source", ("argument", "inspect"))
def test_stopped_mode_requires_three_way_instance_id_match(
    gpu_runtime: dict[str, Any], mismatch_source: str
) -> None:
    initial = _run(gpu_runtime, "--instance-id", "asr-offline-gpu0")
    assert initial.returncode == 0, initial.stderr
    prior = gpu_runtime["output"]
    recovery_output = prior.parent.parent / f"recovery/{mismatch_source}.json"
    recovery_output.parent.mkdir(exist_ok=True)
    gpu_runtime["output"] = recovery_output
    inspect = _base_inspect(running=False)
    instance_argument = "asr-offline-gpu0"
    if mismatch_source == "argument":
        instance_argument = "asr-offline-gpu1"
    else:
        inspect["Config"]["Env"][0] = "PLATFORM_INSTANCE_ID=asr-offline-gpu1"
    gpu_runtime["inspect_path"].write_text(json.dumps(inspect), encoding="utf-8")
    gpu_runtime["env"]["FAKE_PROCESS_ROWS_BEFORE"] = ""
    completed = _run(
        gpu_runtime,
        "--instance-id",
        instance_argument,
        "--assert-stopped",
        "--evidence",
        str(prior),
    )
    assert completed.returncode != 0
    report = _report(gpu_runtime)
    assert "instance" in report["reason"].lower() or "实例" in report["reason"]
    expected_target = (
        "asr-offline-gpu1" if mismatch_source == "inspect" else "asr-offline-gpu0"
    )
    assert report["target"]["instance_id"] == expected_target


def test_stopped_mode_rejects_prior_evidence_stored_under_wrong_sha(
    gpu_runtime: dict[str, Any],
) -> None:
    assert _run(gpu_runtime).returncode == 0
    prior = gpu_runtime["output"]
    wrong_root = prior.parent.parent.parent / ("c" * 40) / "gpu-instances"
    wrong_root.mkdir(parents=True)
    wrong_prior = wrong_root / prior.name
    wrong_prior.write_bytes(prior.read_bytes())
    recovery_output = wrong_root.parent / "recovery/stopped.json"
    recovery_output.parent.mkdir()
    gpu_runtime["output"] = recovery_output
    gpu_runtime["inspect_path"].write_text(
        json.dumps(_base_inspect(running=False)), encoding="utf-8"
    )

    completed = _run(gpu_runtime, "--assert-stopped", "--evidence", str(wrong_prior))

    assert completed.returncode != 0
    assert "release SHA" in _report(gpu_runtime)["reason"]


def test_stopped_mode_requires_output_and_prior_evidence_same_release_sha(
    gpu_runtime: dict[str, Any],
) -> None:
    assert _run(gpu_runtime).returncode == 0
    prior = gpu_runtime["output"]
    other_release = prior.parent.parent.parent / ("c" * 40)
    recovery_output = other_release / "recovery/stopped.json"
    recovery_output.parent.mkdir(parents=True)
    gpu_runtime["output"] = recovery_output
    gpu_runtime["inspect_path"].write_text(
        json.dumps(_base_inspect(running=False)), encoding="utf-8"
    )
    gpu_runtime["env"]["FAKE_PROCESS_ROWS_BEFORE"] = ""

    completed = _run(
        gpu_runtime, "--assert-stopped", "--evidence", str(prior)
    )

    assert completed.returncode != 0
    assert "release SHA" in _report(gpu_runtime)["reason"]


@pytest.mark.parametrize(
    ("environment", "reason"),
    [
        ({"FAKE_DOCKER_TOP_DELAY": "1"}, "docker"),
        ({"FAKE_DOCKER_EXEC_DELAY": "1"}, "docker"),
        ({"FAKE_NVIDIA_SMI_DELAY": "1"}, "nvidia-smi"),
    ],
)
def test_helper_commands_have_a_bounded_timeout_and_write_failure_report(
    gpu_runtime: dict[str, Any], environment: dict[str, str], reason: str
) -> None:
    gpu_runtime["env"].update(environment)

    completed = _run(gpu_runtime, "--command-timeout", "0.3")

    assert completed.returncode != 0
    assert "超时" in _report(gpu_runtime)["reason"]
    assert reason in _report(gpu_runtime)["reason"]


def test_verifier_kills_trigger_process_group_when_helper_command_fails(
    gpu_runtime: dict[str, Any],
) -> None:
    child_pid_file = gpu_runtime["tmp_path"] / "child.pid"
    parent_pid_file = gpu_runtime["tmp_path"] / "parent.pid"
    child = gpu_runtime["tmp_path"] / "child.py"
    child.write_text(
        "import os,time\n"
        "open(os.environ['CHILD_PID_FILE'],'w').write(str(os.getpid()))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    parent = gpu_runtime["tmp_path"] / "parent.py"
    parent.write_text(
        "import json,os,subprocess,sys,time\n"
        "open(os.environ['PARENT_PID_FILE'],'w').write(str(os.getpid()))\n"
        "subprocess.Popen([sys.executable,os.environ['CHILD_SCRIPT']])\n"
        "event={'event':'start','nonce':os.environ['GPU_EVIDENCE_ACTIVITY_NONCE'],"
        "'operator_code':'asr_offline','instance_id':'asr-offline-gpu0',"
        "'run_id':'cleanup-run','attempt':1,"
        "'target_origin':'http://127.0.0.1:18083'}\n"
        "os.write(int(os.environ['GPU_EVIDENCE_ACTIVITY_FD']),"
        "(json.dumps(event)+'\\n').encode())\n"
        "open(os.environ['TRIGGER_MARKER'],'w').write('running')\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    gpu_runtime["trigger_file"].write_text(
        json.dumps([sys.executable, str(parent)]), encoding="utf-8"
    )
    gpu_runtime["env"].update(
        {
            "CHILD_PID_FILE": str(child_pid_file),
            "PARENT_PID_FILE": str(parent_pid_file),
            "CHILD_SCRIPT": str(child),
            "FAKE_DOCKER_TOP_DELAY": "1.5",
        }
    )

    completed = _run(gpu_runtime, "--command-timeout", "0.6")

    assert completed.returncode != 0
    assert "超时" in _report(gpu_runtime)["reason"]
    parent_pid = int(parent_pid_file.read_text(encoding="utf-8"))
    child_pid = int(child_pid_file.read_text(encoding="utf-8"))
    _wait_process_gone(parent_pid)
    _wait_process_gone(child_pid)
    with pytest.raises(ProcessLookupError):
        os.killpg(parent_pid, signal.SIGTERM)


@pytest.mark.parametrize("child_ignores_sigterm", [False, True])
def test_cleanup_kills_process_group_after_trigger_leader_exits(
    gpu_runtime: dict[str, Any], child_ignores_sigterm: bool
) -> None:
    child_pid_file = gpu_runtime["tmp_path"] / "orphan-child.pid"
    parent_pid_file = gpu_runtime["tmp_path"] / "exited-parent.pid"
    child = gpu_runtime["tmp_path"] / "orphan-child.py"
    signal_setup = (
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        if child_ignores_sigterm
        else ""
    )
    child.write_text(
        "import os,signal,time\n"
        + signal_setup
        + "open(os.environ['CHILD_PID_FILE'],'w').write(str(os.getpid()))\n"
        + "time.sleep(60)\n",
        encoding="utf-8",
    )
    parent = gpu_runtime["tmp_path"] / "exited-parent.py"
    parent.write_text(
        "import os,subprocess,sys,time\n"
        "open(os.environ['PARENT_PID_FILE'],'w').write(str(os.getpid()))\n"
        "subprocess.Popen([sys.executable,os.environ['CHILD_SCRIPT']])\n"
        "deadline=time.monotonic()+2\n"
        "while not os.path.exists(os.environ['CHILD_PID_FILE']):\n"
        "  assert time.monotonic()<deadline\n"
        "  time.sleep(.01)\n",
        encoding="utf-8",
    )
    gpu_runtime["trigger_file"].write_text(
        json.dumps([sys.executable, str(parent)]), encoding="utf-8"
    )
    gpu_runtime["env"].update(
        {
            "CHILD_PID_FILE": str(child_pid_file),
            "PARENT_PID_FILE": str(parent_pid_file),
            "CHILD_SCRIPT": str(child),
        }
    )

    completed = _run(gpu_runtime)

    assert completed.returncode != 0
    parent_pid = int(parent_pid_file.read_text(encoding="utf-8"))
    child_pid = int(child_pid_file.read_text(encoding="utf-8"))
    _wait_process_gone(parent_pid)
    _wait_process_gone(child_pid)
    with pytest.raises(ProcessLookupError):
        os.killpg(parent_pid, 0)


def test_linux_process_group_with_only_zombies_is_not_live(tmp_path: Path) -> None:
    namespace = runpy.run_path(str(VERIFIER))
    process_group = 4321
    proc_root = tmp_path / "proc"
    _write_process_stat(proc_root, 111, state="Z", process_group=process_group)
    namespace["_process_group_has_live_members"].__globals__[  # type: ignore[attr-defined]
        "_process_group_exists"
    ] = lambda _: True

    assert namespace["_process_group_has_live_members"](
        process_group, proc_root=proc_root
    ) is False


def test_linux_process_group_with_running_member_remains_live(tmp_path: Path) -> None:
    namespace = runpy.run_path(str(VERIFIER))
    process_group = 4321
    proc_root = tmp_path / "proc"
    _write_process_stat(proc_root, 111, state="Z", process_group=process_group)
    _write_process_stat(proc_root, 222, state="S", process_group=process_group)
    namespace["_process_group_has_live_members"].__globals__[  # type: ignore[attr-defined]
        "_process_group_exists"
    ] = lambda _: True

    assert namespace["_process_group_has_live_members"](
        process_group, proc_root=proc_root
    ) is True
