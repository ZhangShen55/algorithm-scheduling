from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
VERIFIER = PLATFORM_ROOT / "deploy/scripts/verify-gpu-instance"
RELEASE_SHA = "a" * 40
CONTAINER_ID = "b" * 64
OTHER_CONTAINER_ID = "b" * 63 + "c"


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
    process_name: str = "asr_offline",
) -> dict[str, Any]:
    return {
        "Id": container_id,
        "Name": "/asr-offline-gpu0",
        "State": {"Running": running, "Pid": 1000 if running else 0},
        "Config": {
            "Env": [
                "PLATFORM_INSTANCE_ID=asr-offline-gpu0",
                f"PLATFORM_GPU_ID={gpu_id}",
                f"GPU_PROCESS_NAME={process_name}",
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
if len(args) >= 2 and args[0] == "inspect" and args[1] in {{"asr-offline-gpu0", "{CONTAINER_ID}"}}:
    marker = os.environ.get("FAKE_RESTART_MARKER")
    if marker and pathlib.Path(marker).exists():
        inspect[0 if isinstance(inspect, list) else "Id"] = os.environ["FAKE_RESTART_ID"]
    print(json.dumps(inspect if isinstance(inspect, list) else [inspect]))
    raise SystemExit(0)
if (
    len(args) >= 3
    and args[0] == "top"
    and args[1] in {{"asr-offline-gpu0", "{CONTAINER_ID}"}}
    and args[2] == "-eo"
):
    time.sleep(float(os.environ.get("FAKE_DOCKER_TOP_DELAY", "0")))
    print("PID")
    print("1000")
    print("2000")
    raise SystemExit(0)
if len(args) >= 2 and args[0] == "exec" and args[1] in {{"asr-offline-gpu0", "{CONTAINER_ID}"}}:
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
    print("# gpu pid type sm mem enc dec command")
    print("# Idx # C/G % % % % name")
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
        "instance_id": "asr-offline-gpu0",
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
    ("process_name", "framework"),
    [("ocr", "paddle"), ("facerec", "fastdeploy")],
)
def test_non_torch_operator_uses_framework_specific_probe(
    gpu_runtime: dict[str, Any], process_name: str, framework: str
) -> None:
    inspect = _base_inspect(process_name=process_name)
    inspect["Config"]["Env"][0] = "PLATFORM_INSTANCE_ID=asr-offline-gpu0"
    gpu_runtime["inspect_path"].write_text(json.dumps(inspect), encoding="utf-8")
    gpu_runtime["env"]["FAKE_PROBE"] = json.dumps(
        {
            "framework_gpu_available": True,
            "device_count": 1,
            "current_device": 0,
            "container_cuda_runtime_version": "12.1",
        }
    )

    completed = _run_with_process(gpu_runtime, process_name)

    assert completed.returncode == 0, completed.stderr
    report = _report(gpu_runtime)
    assert report["framework_probe"]["framework"] == framework
    assert "torch" not in report["framework_probe"]["command"]


def _run_with_process(
    runtime: dict[str, Any], process_name: str, *extra: str
) -> subprocess.CompletedProcess[str]:
    command = [
        str(VERIFIER), "--container", "asr-offline-gpu0", "--physical-gpu", "0",
        "--process-name", process_name, "--output", str(runtime["output"]),
        "--trigger-file", str(runtime["trigger_file"]), "--sample-window", "0.6",
        "--sample-interval", "0.02", *extra,
    ]
    runtime["env"]["FAKE_PROCESS_ROWS_DURING"] = f"GPU-A, 2000, {process_name}, 300"
    runtime["env"]["ACTIVITY_OPERATOR"] = process_name
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
