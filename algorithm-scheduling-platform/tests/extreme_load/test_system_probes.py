from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from scripts.extreme_load.system_probes import (
    CommandResult,
    ControlMetricsProbe,
    DockerMetricsProbe,
    GatewayMetricsProbe,
    HttpResponse,
    HttpxProbeClient,
    KafkaLagProbe,
    LoadHostProbe,
    NvidiaSmiProbe,
    ProbeAttemptsExhausted,
    ProbeDisabledError,
    ProbeError,
    SshCommandRunner,
    SshTarget,
    SubprocessCommandRunner,
    TargetHostProbe,
    parse_prometheus,
)


@dataclass
class FakeRunner:
    outputs: dict[tuple[str, ...], CommandResult]
    calls: list[tuple[tuple[str, ...], float]] = field(default_factory=list)

    def run(self, argv: Sequence[str], *, timeout_seconds: float) -> CommandResult:
        command = tuple(argv)
        self.calls.append((command, timeout_seconds))
        try:
            return self.outputs[command]
        except KeyError as error:
            raise AssertionError(f"unexpected command: {command}") from error


@dataclass
class SuffixRunner:
    outputs: dict[tuple[str, ...], CommandResult]
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def run(self, argv: Sequence[str], *, timeout_seconds: float) -> CommandResult:
        del timeout_seconds
        command = tuple(argv)
        self.calls.append(command)
        for suffix, result in self.outputs.items():
            if command[-len(suffix) :] == suffix:
                return result
        raise AssertionError(f"unexpected SSH command: {command}")


@dataclass
class FakeHttpClient:
    responses: dict[str, HttpResponse]
    calls: list[tuple[str, float, int]] = field(default_factory=list)

    def get(self, url: str, *, timeout_seconds: float, max_bytes: int) -> HttpResponse:
        self.calls.append((url, timeout_seconds, max_bytes))
        try:
            return self.responses[url]
        except KeyError as error:
            raise AssertionError(f"unexpected URL: {url}") from error


def _ok(stdout: str) -> CommandResult:
    return CommandResult(0, stdout, "")


def _json_response(document: object) -> HttpResponse:
    return HttpResponse(200, "application/json; charset=utf-8", json.dumps(document).encode())


def _metrics_response(text: str) -> HttpResponse:
    return HttpResponse(200, "text/plain; version=0.0.4; charset=utf-8", text.encode())


def test_subprocess_runner_never_uses_a_shell_and_bounds_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed["args"] = args
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(args=("probe",), returncode=0, stdout=b"ok", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = SubprocessCommandRunner().run(("probe", "--read-only"), timeout_seconds=2)

    assert result == CommandResult(0, "ok", "")
    kwargs = observed["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["shell"] is False
    assert kwargs["timeout"] == 2.0
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["capture_output"] is True
    environment = kwargs["env"]
    assert isinstance(environment, dict)
    assert environment["LC_ALL"] == "C"
    assert environment["LANG"] == "C"


def test_subprocess_timeout_and_non_utf8_fail_without_command_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        del args, kwargs
        raise subprocess.TimeoutExpired(cmd=("probe",), timeout=1)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(ProbeError, match="超时"):
        SubprocessCommandRunner().run(("probe",), timeout_seconds=1)

    def non_utf8(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        del args, kwargs
        return subprocess.CompletedProcess(
            args=("probe",), returncode=0, stdout=b"\xff", stderr=b""
        )

    monkeypatch.setattr(subprocess, "run", non_utf8)
    with pytest.raises(ProbeError, match="UTF-8"):
        SubprocessCommandRunner().run(("probe",), timeout_seconds=1)


def test_command_failures_redact_credentials() -> None:
    runner = FakeRunner(
        {
            ("ps", "-A", "-o", "%cpu="): CommandResult(
                4,
                "sensitive stdout must not be included",
                "password=hunter2 token:abc http://user:pass@example.test/path",
            )
        }
    )

    with pytest.raises(ProbeError) as captured:
        LoadHostProbe(runner, platform="linux", cpu_count=1).collect()

    message = str(captured.value)
    assert "hunter2" not in message
    assert "token:abc" not in message
    assert "user:pass" not in message
    assert "sensitive stdout" not in message


def test_ssh_is_disabled_by_default_and_never_calls_base_runner() -> None:
    base = FakeRunner({})
    ssh = SshCommandRunner(base, SshTarget("192.168.29.11", "root"))

    with pytest.raises(ProbeDisabledError, match="默认关闭"):
        ssh.run(("cat", "/proc/meminfo"), timeout_seconds=2)
    assert base.calls == []


def test_explicit_ssh_uses_batch_mode_strict_host_keys_and_safe_remote_argv() -> None:
    base = SuffixRunner({("cat", "/proc/meminfo"): _ok("MemTotal: 1 kB\n")})
    ssh = SshCommandRunner(
        base,
        SshTarget("192.168.29.11", "root", identity_file=Path("/keys/campaign")),
        enabled=True,
    )

    result = ssh.run(("cat", "/proc/meminfo"), timeout_seconds=3.1)

    assert result.returncode == 0
    command = base.calls[0]
    assert command[0] == "ssh"
    assert "BatchMode=yes" in command
    assert "StrictHostKeyChecking=yes" in command
    assert "ConnectTimeout=4" in command
    assert command[-5:] == (
        "root@192.168.29.11",
        "env",
        "LC_ALL=C",
        "cat",
        "/proc/meminfo",
    )
    with pytest.raises(ValueError, match="安全字符集"):
        ssh.run(("cat", "/proc/meminfo;reboot"), timeout_seconds=2)


def test_linux_load_host_probe_collects_cpu_memory_socket_fd_and_network() -> None:
    netdev = (
        "Inter-|   Receive                                                |  Transmit\n"
        " face |bytes packets errs drop fifo frame compressed multicast|"
        "bytes packets errs drop fifo colls carrier compressed\n"
        "    lo: 100 1 0 0 0 0 0 0 200 2 0 0 0 0 0 0\n"
        "  eth0: 300 3 0 0 0 0 0 0 400 4 0 0 0 0 0 0\n"
    )
    runner = FakeRunner(
        {
            ("ps", "-A", "-o", "%cpu="): _ok("100.0\n20.0\n"),
            ("cat", "/proc/meminfo"): _ok(
                "MemTotal:       1000 kB\nMemAvailable:    400 kB\nCached: 20 kB\n"
            ),
            ("cat", "/proc/net/dev"): _ok(netdev),
            ("ss", "-H", "-a"): _ok("tcp LISTEN\nudp UNCONN\n"),
            ("cat", "/proc/sys/fs/file-nr"): _ok("123 0 999\n"),
        }
    )

    metrics = LoadHostProbe(runner, platform="linux", cpu_count=4).collect()

    assert metrics.cpu_percent == 30
    assert metrics.memory_total_bytes == 1000 * 1024
    assert metrics.memory_available_bytes == 400 * 1024
    assert metrics.network_receive_bytes == 400
    assert metrics.network_transmit_bytes == 600
    assert metrics.open_socket_count == 2
    assert metrics.open_file_handle_count == 123


def test_darwin_load_host_probe_deduplicates_interface_address_rows() -> None:
    runner = FakeRunner(
        {
            ("ps", "-A", "-o", "%cpu="): _ok("50.0\n"),
            ("sysctl", "-n", "hw.memsize"): _ok("8388608\n"),
            ("vm_stat",): _ok(
                "Mach Virtual Memory Statistics: (page size of 4096 bytes)\n"
                "Pages free: 100.\nPages inactive: 200.\nPages speculative: 50.\n"
            ),
            ("netstat", "-ibdn"): _ok(
                "Name Mtu Network Address Ipkts Ierrs Ibytes Opkts Oerrs Obytes Coll Drop\n"
                "lo0 16384 link loop 1 0 100 1 0 200 0 0\n"
                "gif0* 1280 link 0 0 0 0 0 0 0 0\n"
                "en0 1500 link mac 2 0 300 2 0 400 0 0\n"
                "en0 1500 inet 2 - 300 2 - 400 - -\n"
            ),
            ("netstat", "-an", "-f", "inet"): _ok(
                "Active Internet connections\nProto Recv-Q Send-Q\n"
                "tcp4 row\nudp4 row\nActive Multipath Internet connections\n"
            ),
            ("sysctl", "-n", "kern.num_files"): _ok("77\n"),
        }
    )

    metrics = LoadHostProbe(runner, platform="darwin", cpu_count=2).collect()

    assert metrics.cpu_percent == 25
    assert metrics.memory_available_bytes == 350 * 4096
    assert metrics.network_receive_bytes == 400
    assert metrics.network_transmit_bytes == 600
    assert metrics.open_socket_count == 2
    assert metrics.open_file_handle_count == 77


def test_load_host_probe_rejects_malformed_kernel_counters() -> None:
    runner = FakeRunner(
        {
            ("ps", "-A", "-o", "%cpu="): _ok("10\n"),
            ("cat", "/proc/meminfo"): _ok("MemTotal: 100 kB\nMemAvailable: 200 kB\n"),
        }
    )

    with pytest.raises(ValueError, match="可用内存大于"):
        LoadHostProbe(runner, platform="linux", cpu_count=1).collect()


def test_target_host_probe_uses_explicit_ssh_for_df_memory_and_oom() -> None:
    base = SuffixRunner(
        {
            ("df", "-B1", "-P", "--", "/"): _ok(
                "Filesystem 1-blocks Used Available Capacity Mounted on\n"
                "/dev/sda1 1000000 400000 600000 40% /\n"
            ),
            ("df", "-B1", "-P", "--", "/data"): _ok(
                "Filesystem 1-blocks Used Available Capacity Mounted on\n"
                "/dev/sdb1 2000000 1500000 500000 75% /data\n"
            ),
            ("du", "-s", "-B1", "--", "/data/course"): _ok(
                "123456\t/data/course\n"
            ),
            ("du", "-s", "-B1", "--", "/data/result"): _ok(
                "654321\t/data/result\n"
            ),
            ("cat", "/proc/meminfo"): _ok("MemTotal: 2000 kB\nMemAvailable: 500 kB\n"),
            ("cat", "/proc/vmstat"): _ok("pgfault 10\noom_kill 3\n"),
        }
    )
    ssh = SshCommandRunner(base, SshTarget("192.168.29.11", "root"), enabled=True)

    probe = TargetHostProbe(ssh, filesystem_paths=("/", "/data"))
    metrics = probe.collect()
    directory_sizes = probe.collect_directory_sizes()

    assert tuple(item.requested_path for item in metrics.filesystems) == ("/", "/data")
    assert metrics.filesystems[1].available_bytes == 500000
    assert metrics.directory_sizes == ()
    assert {item.requested_path: item.size_bytes for item in directory_sizes} == {
        "/data/course": 123456,
        "/data/result": 654321,
    }
    assert metrics.memory_total_bytes == 2000 * 1024
    assert metrics.memory_available_bytes == 500 * 1024
    assert metrics.oom_events == 3
    assert all(call[0] == "ssh" for call in base.calls)


def test_target_host_directory_bytes_use_only_fixed_argv_paths_and_fail_closed() -> None:
    base = SuffixRunner(
        {
            ("df", "-B1", "-P", "--", "/"): _ok(
                "Filesystem 1-blocks Used Available Capacity Mounted on\n"
                "/dev/sda1 1000000 400000 600000 40% /\n"
            ),
            ("du", "-s", "-B1", "--", "/data/course"): _ok(
                "not-a-number /data/course\n"
            ),
            ("du", "-s", "-B1", "--", "/data/result"): _ok(
                "1 /data/result\n"
            ),
        }
    )
    ssh = SshCommandRunner(base, SshTarget("192.168.29.11", "root"), enabled=True)

    with pytest.raises(ValueError, match="du 目录字节输出格式错误"):
        TargetHostProbe(ssh, filesystem_paths=("/",)).collect_directory_sizes()
    assert any(
        call[-5:] == ("du", "-s", "-B1", "--", "/data/course") for call in base.calls
    )
    assert not any("/tmp" in argument for call in base.calls for argument in call)

    with pytest.raises(ValueError, match="固定数据目录"):
        TargetHostProbe(
            ssh,
            filesystem_paths=("/",),
            directory_paths=("/data/course", "/tmp"),
        )


def test_target_host_missing_fixed_directory_fails_closed() -> None:
    base = SuffixRunner(
        {
            ("df", "-B1", "-P", "--", "/"): _ok(
                "Filesystem 1-blocks Used Available Capacity Mounted on\n"
                "/dev/sda1 1000000 400000 600000 40% /\n"
            ),
            ("du", "-s", "-B1", "--", "/data/course"): CommandResult(
                1,
                "",
                "No such file or directory",
            ),
        }
    )
    ssh = SshCommandRunner(base, SshTarget("192.168.29.11", "root"), enabled=True)

    with pytest.raises(ProbeError, match="退出码 1"):
        TargetHostProbe(ssh, filesystem_paths=("/",)).collect_directory_sizes()


def test_target_host_probe_does_not_enable_ssh_implicitly() -> None:
    base = FakeRunner({})
    ssh = SshCommandRunner(base, SshTarget("192.168.29.11", "root"))

    with pytest.raises(ProbeDisabledError):
        TargetHostProbe(ssh, filesystem_paths=("/",)).collect()
    assert base.calls == []


def _inspect_item(
    container_id: str,
    *,
    project: str,
    service: str,
    restart_count: int,
    health: str | None,
) -> dict[str, Any]:
    state: dict[str, object] = {"Running": True}
    if health is not None:
        state["Health"] = {"Status": health}
    return {
        "Id": container_id,
        "RestartCount": restart_count,
        "Config": {
            "Labels": {
                "com.docker.compose.project": project,
                "com.docker.compose.service": service,
                "credential": "must-not-be-returned",
            }
        },
        "State": state,
    }


def test_docker_probe_collects_exact_compose_identity_restart_health_cpu_and_memory() -> None:
    platform_id = "a" * 64
    unrelated_id = "b" * 64
    inspect = [
        _inspect_item(
            platform_id,
            project="algorithm-scheduling-platform",
            service="control-service",
            restart_count=2,
            health="healthy",
        ),
        _inspect_item(
            unrelated_id,
            project="unrelated",
            service="other",
            restart_count=99,
            health=None,
        ),
    ]
    runner = FakeRunner(
        {
            ("docker", "ps", "-q", "--no-trunc"): _ok(f"{platform_id}\n{unrelated_id}\n"),
            ("docker", "inspect", platform_id, unrelated_id): _ok(json.dumps(inspect)),
            (
                "docker",
                "stats",
                "--no-stream",
                "--no-trunc",
                platform_id,
            ): _ok(
                "CONTAINER ID NAME CPU % MEM USAGE / LIMIT MEM % NET I/O BLOCK I/O PIDS\n"
                f"{platform_id} control 12.5% 1.5MiB / 2GiB 0.1% 1kB / 2kB "
                "3kB / 4kB 5\n"
            ),
        }
    )

    metrics = DockerMetricsProbe(
        runner,
        compose_projects=("algorithm-scheduling-platform",),
    ).collect()

    assert len(metrics) == 1
    assert metrics[0].container_id == platform_id
    assert metrics[0].compose_service == "control-service"
    assert metrics[0].restart_count == 2
    assert metrics[0].healthy is True
    assert metrics[0].cpu_percent == 12.5
    assert metrics[0].memory_bytes == int(Decimal("1.5") * 1024**2)
    assert "credential" not in repr(metrics[0])


def test_docker_probe_rounds_human_readable_fractional_memory_to_nearest_byte() -> None:
    container_id = "9" * 64
    inspect = [
        _inspect_item(
            container_id,
            project="algorithm-scheduling-platform",
            service="control-service",
            restart_count=0,
            health="healthy",
        )
    ]
    runner = FakeRunner(
        {
            ("docker", "ps", "-q", "--no-trunc"): _ok(container_id + "\n"),
            ("docker", "inspect", container_id): _ok(json.dumps(inspect)),
            (
                "docker",
                "stats",
                "--no-stream",
                "--no-trunc",
                container_id,
            ): _ok(
                "CONTAINER ID NAME CPU % MEM USAGE / LIMIT MEM % NET I/O BLOCK I/O PIDS\n"
                f"{container_id} control 0.1% 126.1MiB / 125GiB 0.1% "
                "1kB / 2kB 3kB / 4kB 5\n"
            ),
        }
    )

    metrics = DockerMetricsProbe(
        runner,
        compose_projects=("algorithm-scheduling-platform",),
    ).collect()

    assert metrics[0].memory_bytes == 132_225_434


def test_docker_probe_rejects_missing_compose_service_before_stats() -> None:
    container_id = "c" * 64
    inspect = _inspect_item(
        container_id,
        project="algorithm-scheduling-platform",
        service="control-service",
        restart_count=0,
        health=None,
    )
    del inspect["Config"]["Labels"]["com.docker.compose.service"]
    runner = FakeRunner(
        {
            ("docker", "ps", "-q", "--no-trunc"): _ok(container_id + "\n"),
            ("docker", "inspect", container_id): _ok(json.dumps([inspect])),
        }
    )

    with pytest.raises(ValueError, match="service 身份"):
        DockerMetricsProbe(
            runner,
            compose_projects=("algorithm-scheduling-platform",),
        ).collect()
    assert all(call[0][1] != "stats" for call in runner.calls)


def test_kafka_lag_probe_uses_exact_container_and_sums_required_groups() -> None:
    container_id = "d" * 64
    inspect = [
        _inspect_item(
            container_id,
            project="algorithm-scheduling-platform",
            service="kafka",
            restart_count=0,
            health="healthy",
        )
    ]
    group_one = "algorithm-orchestrator"
    group_two = "algorithm-orchestrator-visual-events"
    command = (
        "docker",
        "exec",
        container_id,
        "/opt/kafka/bin/kafka-consumer-groups.sh",
        "--bootstrap-server",
        "kafka:29092",
        "--describe",
        "--all-groups",
    )
    header = "GROUP TOPIC PARTITION CURRENT-OFFSET LOG-END-OFFSET LAG CONSUMER-ID HOST CLIENT-ID\n"
    runner = FakeRunner(
        {
            ("docker", "ps", "-q", "--no-trunc"): _ok(container_id + "\n"),
            ("docker", "inspect", container_id): _ok(json.dumps(inspect)),
            command: _ok(
                header
                + f"{group_one} course.commands 0 10 13 3 - - -\n"
                + f"{group_one} course.commands 1 20 25 5 - - -\n"
                + "unrelated other.topic 0 1 100 99 - - -\n"
                + "\n"
                + header
                + f"{group_two} visual.events 0 7 11 4 - - -\n"
            ),
        }
    )

    lag = KafkaLagProbe(
        runner,
        compose_project="algorithm-scheduling-platform",
        compose_service="kafka",
        consumer_groups=(group_one, group_two),
    ).collect()

    assert lag == 12
    assert all(call[0][0] == "docker" for call in runner.calls)
    assert all("sh" not in call[0] and "bash" not in call[0] for call in runner.calls)


def test_kafka_lag_probe_retries_one_transient_command_failure() -> None:
    container_id = "f" * 64
    inspect = [
        _inspect_item(
            container_id,
            project="algorithm-scheduling-platform",
            service="kafka",
            restart_count=0,
            health="healthy",
        )
    ]
    command = (
        "docker",
        "exec",
        container_id,
        "/opt/kafka/bin/kafka-consumer-groups.sh",
        "--bootstrap-server",
        "kafka:29092",
        "--describe",
        "--all-groups",
    )

    @dataclass
    class TransientRunner:
        calls: list[tuple[tuple[str, ...], float]] = field(default_factory=list)
        kafka_calls: int = 0

        def run(self, argv: Sequence[str], *, timeout_seconds: float) -> CommandResult:
            current = tuple(argv)
            self.calls.append((current, timeout_seconds))
            if current == ("docker", "ps", "-q", "--no-trunc"):
                return _ok(container_id + "\n")
            if current == ("docker", "inspect", container_id):
                return _ok(json.dumps(inspect))
            if current == command:
                self.kafka_calls += 1
                if self.kafka_calls == 1:
                    return CommandResult(124, "", "transient detail must stay private")
                return _ok(
                    "GROUP TOPIC PARTITION CURRENT-OFFSET LOG-END-OFFSET LAG "
                    "CONSUMER-ID HOST CLIENT-ID\n"
                    "algorithm-orchestrator course.commands 0 1 3 2 - - -\n"
                )
            raise AssertionError(f"unexpected command: {current}")

    runner = TransientRunner()
    lag = KafkaLagProbe(
        runner,
        compose_project="algorithm-scheduling-platform",
        compose_service="kafka",
        consumer_groups=("algorithm-orchestrator",),
        attempts=2,
        retry_delay_seconds=0,
    ).collect()

    assert lag == 2
    assert runner.kafka_calls == 2


def test_kafka_lag_probe_fails_closed_after_bounded_attempts() -> None:
    container_id = "a" * 64
    inspect = [
        _inspect_item(
            container_id,
            project="algorithm-scheduling-platform",
            service="kafka",
            restart_count=0,
            health="healthy",
        )
    ]
    command = (
        "docker",
        "exec",
        container_id,
        "/opt/kafka/bin/kafka-consumer-groups.sh",
        "--bootstrap-server",
        "kafka:29092",
        "--describe",
        "--all-groups",
    )
    runner = FakeRunner(
        {
            ("docker", "ps", "-q", "--no-trunc"): _ok(container_id + "\n"),
            ("docker", "inspect", container_id): _ok(json.dumps(inspect)),
            command: CommandResult(124, "", "password=must-not-leak"),
        }
    )

    with pytest.raises(ProbeAttemptsExhausted) as captured:
        KafkaLagProbe(
            runner,
            compose_project="algorithm-scheduling-platform",
            compose_service="kafka",
            consumer_groups=("algorithm-orchestrator",),
            attempts=2,
            retry_delay_seconds=0,
        ).collect()

    assert sum(call[0] == command for call in runner.calls) == 2
    assert captured.value.attempts == 2
    assert "must-not-leak" not in str(captured.value)


def test_kafka_lag_probe_rejects_more_than_one_retry() -> None:
    with pytest.raises(ValueError, match="1–2"):
        KafkaLagProbe(
            FakeRunner({}),
            compose_project="algorithm-scheduling-platform",
            compose_service="kafka",
            consumer_groups=("algorithm-orchestrator",),
            attempts=3,
        )


def test_kafka_lag_probe_fails_closed_without_exact_healthy_container() -> None:
    container_id = "e" * 64
    inspect = [
        _inspect_item(
            container_id,
            project="algorithm-scheduling-platform",
            service="kafka",
            restart_count=0,
            health="unhealthy",
        )
    ]
    runner = FakeRunner(
        {
            ("docker", "ps", "-q", "--no-trunc"): _ok(container_id + "\n"),
            ("docker", "inspect", container_id): _ok(json.dumps(inspect)),
        }
    )

    with pytest.raises(ProbeError, match="唯一健康"):
        KafkaLagProbe(
            runner,
            compose_project="algorithm-scheduling-platform",
            compose_service="kafka",
            consumer_groups=("algorithm-orchestrator",),
        ).collect()


def test_nvidia_smi_probe_binds_uuid_pid_memory_and_redacts_unsafe_process_names() -> None:
    gpu0 = "GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    gpu1 = "GPU-11111111-2222-3333-4444-555555555555"
    runner = FakeRunner(
        {
            (
                "nvidia-smi",
                "--query-gpu=uuid,utilization.gpu,memory.used",
                "--format=csv,noheader,nounits",
            ): _ok(f"{gpu0}, 75, 1024\n{gpu1}, 25.5, 512\n"),
            (
                "nvidia-smi",
                "--query-compute-apps=gpu_uuid,pid,process_name,used_memory",
                "--format=csv,noheader,nounits",
            ): _ok(f'{gpu0}, 123, /usr/bin/python3, 256\n{gpu1}, 456, "secret process", 64\n'),
        }
    )

    metrics = NvidiaSmiProbe(runner).collect()

    by_uuid = {item.uuid: item for item in metrics}
    assert by_uuid[gpu0].utilization_percent == 75
    assert by_uuid[gpu0].memory_used_bytes == 1024 * 1024**2
    assert by_uuid[gpu0].processes[0].name == "python3"
    assert by_uuid[gpu0].processes[0].memory_bytes == 256 * 1024**2
    assert by_uuid[gpu1].processes[0].name.startswith("redacted-process-")
    assert "secret process" not in repr(metrics)


def test_nvidia_smi_probe_rejects_process_for_unknown_gpu() -> None:
    gpu = "GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    runner = FakeRunner(
        {
            (
                "nvidia-smi",
                "--query-gpu=uuid,utilization.gpu,memory.used",
                "--format=csv,noheader,nounits",
            ): _ok(f"{gpu}, 10, 100\n"),
            (
                "nvidia-smi",
                "--query-compute-apps=gpu_uuid,pid,process_name,used_memory",
                "--format=csv,noheader,nounits",
            ): _ok("GPU-11111111-2222-3333-4444-555555555555, 9, python, 1\n"),
        }
    )

    with pytest.raises(ValueError, match="未知 GPU"):
        NvidiaSmiProbe(runner).collect()


def test_prometheus_parser_rejects_duplicate_malformed_and_nonfinite_samples() -> None:
    duplicate = 'metric_total{outcome="ok"} 1\nmetric_total{outcome="ok"} 2\n'
    with pytest.raises(ValueError, match="重复"):
        parse_prometheus(duplicate)
    with pytest.raises(ValueError, match="样本行"):
        parse_prometheus('metric_total{outcome="ok",password="secret"} NaN\n')
    with pytest.raises(ValueError, match="label 列表"):
        parse_prometheus('metric_total{outcome="unterminated} 1\n')
    with pytest.raises(ValueError, match="逗号结尾"):
        parse_prometheus('metric_total{outcome="ok",} 1\n')


def test_control_probe_collects_queue_kafka_lag_and_instance_capacity() -> None:
    origin = "http://control.test:18100"
    client = FakeHttpClient(
        {
            f"{origin}/ops/queues": _json_response(
                {
                    "queues": [
                        {"status": 30, "count": 3},
                        {"status": 50, "count": 2},
                    ],
                    "outbox_pending": 4,
                }
            ),
            f"{origin}/metrics": _metrics_response(
                "# HELP algorithm_kafka_consumer_lag lag\n"
                "algorithm_kafka_consumer_lag{"
                'topic="commands",consumer_group="orch",partition="0"} 5\n'
                "algorithm_kafka_consumer_lag{"
                'topic="commands",consumer_group="orch",partition="1"} 7\n'
            ),
            f"{origin}/ops/operator-instances/snapshot": _json_response(
                [
                    {
                        "instance_id": "ocr-gpu0",
                        "operator_code": "ocr",
                        "declared_capacity": 3,
                        "reported_inflight": 2,
                        "active_lease_count": 1,
                    },
                    {
                        "instance_id": "vbas-gpu0",
                        "operator_code": "vbas",
                        "declared_capacity": 5,
                        "reported_inflight": 4,
                        "active_lease_count": 4,
                    },
                ]
            ),
        }
    )

    metrics = ControlMetricsProbe(client, origin).collect()

    assert metrics.task_queue_depth == 5
    assert metrics.outbox_pending == 4
    assert metrics.kafka_lag == 12
    assert [item.instance_id for item in metrics.instances] == ["ocr-gpu0", "vbas-gpu0"]
    assert metrics.instances[0].inflight == 2
    assert metrics.instances[0].active_leases == 1
    assert metrics.instances[0].declared_capacity == 3
    assert {url.rsplit("/", 1)[-1] for url, _, _ in client.calls} == {
        "queues",
        "metrics",
        "snapshot",
    }


def test_control_probe_fails_closed_when_kafka_lag_series_is_absent() -> None:
    origin = "http://control.test:18100"
    client = FakeHttpClient(
        {
            f"{origin}/ops/queues": _json_response({"queues": [], "outbox_pending": 0}),
            f"{origin}/metrics": _metrics_response("unrelated_metric_total 0\n"),
        }
    )

    with pytest.raises(ProbeError, match="Kafka lag"):
        ControlMetricsProbe(client, origin).collect()


def test_control_probe_accepts_explicit_verified_external_kafka_lag_source() -> None:
    origin = "http://control.test:18100"
    client = FakeHttpClient(
        {
            f"{origin}/ops/queues": _json_response(
                {"queues": [], "outbox_pending": 0}
            ),
            f"{origin}/ops/operator-instances/snapshot": _json_response([]),
        }
    )

    metrics = ControlMetricsProbe(
        client,
        origin,
        kafka_lag_source=lambda: 17,
    ).collect()

    assert metrics.kafka_lag == 17
    assert all(not url.endswith("/metrics") for url, _, _ in client.calls)


def test_control_probe_can_defer_kafka_lag_to_independent_surface() -> None:
    origin = "http://control.test:18100"
    client = FakeHttpClient(
        {
            f"{origin}/ops/queues": _json_response({"queues": [], "outbox_pending": 0}),
            f"{origin}/ops/operator-instances/snapshot": _json_response([]),
        }
    )

    metrics = ControlMetricsProbe(client, origin, include_kafka_lag=False).collect()

    assert metrics.kafka_lag == 0
    assert all(not url.endswith("/metrics") for url, _, _ in client.calls)


@pytest.mark.parametrize(
    "snapshot",
    (
        [
            {
                "instance_id": "ocr-gpu0",
                "operator_code": "ocr",
                "declared_capacity": True,
                "reported_inflight": 0,
                "active_lease_count": 0,
            }
        ],
        [
            {
                "instance_id": "ocr-gpu0",
                "operator_code": "ocr",
                "declared_capacity": 1,
                "reported_inflight": 0,
                "active_lease_count": 0,
            },
            {
                "instance_id": "ocr-gpu0",
                "operator_code": "ocr",
                "declared_capacity": 1,
                "reported_inflight": 0,
                "active_lease_count": 0,
            },
        ],
    ),
)
def test_control_instance_probe_rejects_bool_counts_and_duplicate_ids(snapshot: object) -> None:
    origin = "http://control.test:18100"
    client = FakeHttpClient({f"{origin}/ops/operator-instances/snapshot": _json_response(snapshot)})

    with pytest.raises(ValueError):
        ControlMetricsProbe(client, origin).collect_instances()


def test_gateway_probe_aggregates_operator_requests_and_lease_outcomes() -> None:
    origin = "http://gateway.test:18103"
    metrics_text = (
        "# TYPE algorithm_operator_request_latency_seconds histogram\n"
        "algorithm_operator_request_latency_seconds_count{"
        'operator_code="ocr",capability="ocr",instance_id="ocr-gpu0"} 7\n'
        "algorithm_operator_request_latency_seconds_count{"
        'operator_code="ocr",capability="ocr",instance_id="ocr-gpu1"} 3\n'
        "algorithm_capacity_lease_events_total{"
        'capability="ocr",instance_id="ocr-gpu0",outcome="acquired"} 7\n'
        "algorithm_capacity_lease_events_total{"
        'capability="ocr",instance_id="none",outcome="rejected"} 2\n'
        "algorithm_capacity_lease_events_total{"
        'capability="ocr",instance_id="ocr-gpu0",outcome="released"} 6\n'
        "algorithm_capacity_lease_events_total{"
        'capability="ocr",instance_id="none",outcome="requested"} 12\n'
    )
    client = FakeHttpClient({f"{origin}/metrics": _metrics_response(metrics_text)})

    metrics = GatewayMetricsProbe(client, origin).collect()

    assert metrics.counters.requests_total == 10
    assert metrics.counters.lease_acquired_total == 7
    assert metrics.counters.lease_rejected_total == 2
    assert metrics.counters.lease_released_total == 6
    assert metrics.instance_requests == {"ocr-gpu0": 7, "ocr-gpu1": 3}


def test_gateway_probe_rejects_fractional_counter_and_unsafe_labels() -> None:
    origin = "http://gateway.test:18103"
    fractional = (
        'algorithm_operator_request_latency_seconds_count{operator_code="ocr",'
        'capability="ocr",instance_id="ocr-gpu0"} 1.5\n'
    )
    client = FakeHttpClient({f"{origin}/metrics": _metrics_response(fractional)})
    with pytest.raises(ValueError, match="不是整数"):
        GatewayMetricsProbe(client, origin).collect()

    unsafe = (
        'algorithm_capacity_lease_events_total{capability="ocr",instance_id="bad id",'
        'outcome="acquired"} 1\n'
    )
    client = FakeHttpClient({f"{origin}/metrics": _metrics_response(unsafe)})
    with pytest.raises(ValueError, match="不安全"):
        GatewayMetricsProbe(client, origin).collect()


def test_httpx_probe_client_is_bounded_does_not_follow_redirects_or_echo_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"location": "/secret"})
        if request.url.path == "/large":
            return httpx.Response(200, content=b"x" * 20, headers={"content-type": "text/plain"})
        return httpx.Response(
            500,
            text="password=hunter2",
            headers={"content-type": "text/plain"},
        )

    client = HttpxProbeClient(transport=httpx.MockTransport(handler))

    with pytest.raises(ProbeError, match="302"):
        client.get("http://probe.test/redirect", timeout_seconds=1, max_bytes=10)
    with pytest.raises(ProbeError, match="超过安全上限"):
        client.get("http://probe.test/large", timeout_seconds=1, max_bytes=10)
    with pytest.raises(ProbeError) as captured:
        client.get("http://probe.test/error", timeout_seconds=1, max_bytes=100)
    assert "hunter2" not in str(captured.value)


@pytest.mark.parametrize(
    ("probe", "origin"),
    (
        (ControlMetricsProbe, "http://user:password@control.test:18100"),
        (ControlMetricsProbe, "http://control.test:18103"),
        (GatewayMetricsProbe, "http://gateway.test:18100"),
        (GatewayMetricsProbe, "http://gateway.test:18103/path"),
    ),
)
def test_http_probes_reject_credentials_wrong_ports_and_paths(
    probe: type[ControlMetricsProbe] | type[GatewayMetricsProbe],
    origin: str,
) -> None:
    with pytest.raises(ValueError, match="origin"):
        probe(FakeHttpClient({}), origin)
