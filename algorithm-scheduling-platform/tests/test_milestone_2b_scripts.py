from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import runpy
import shutil
import subprocess
import sys
import threading
import time
from csv import writer
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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

GPU_UUIDS = (
    "GPU-11111111-1111-1111-1111-111111111111",
    "GPU-22222222-2222-2222-2222-222222222222",
    "GPU-33333333-3333-3333-3333-333333333333",
)

EXPECTED_DATABASE_COLUMNS = {
    "course_jobs": ("id", "task_id", "input_snapshot", "created_at", "updated_at"),
    "course_task_types": (
        "id",
        "submission_id",
        "task_id",
        "task_type",
        "status",
        "priority",
        "reason",
        "request_payload",
        "effective_params",
        "requested_at",
        "started_at",
        "finished_at",
        "updated_at",
    ),
    "task_nodes": (
        "id",
        "course_task_type_id",
        "node_code",
        "status",
        "priority",
        "reason",
        "required_capability",
        "prerequisite_count",
        "completed_prerequisite_count",
        "attempt",
        "ready_at",
        "claimed_by",
        "claim_token",
        "claimed_at",
        "started_at",
        "finished_at",
        "created_at",
        "updated_at",
    ),
    "node_results": (
        "task_node_id",
        "result",
        "artifact_path",
        "artifact_count",
        "progress",
        "effective_params",
        "result_version",
        "created_at",
        "updated_at",
    ),
    "node_work_items": (
        "id",
        "task_node_id",
        "item_key",
        "ordinal",
        "status",
        "reason",
        "result",
        "attempt",
        "created_at",
        "updated_at",
    ),
    "outbox_events": (
        "event_id",
        "aggregate_type",
        "aggregate_id",
        "event_type",
        "payload",
        "available_at",
        "published_at",
        "publish_attempts",
        "last_error",
        "created_at",
        "claim_token",
        "claimed_at",
    ),
    "operator_instances": (
        "instance_id",
        "operator_code",
        "capabilities",
        "service_url",
        "model_version",
        "api_version",
        "declared_capacity",
        "labels",
        "desired_state",
        "last_registered_at",
        "last_heartbeat_at",
        "unregistered_at",
        "created_at",
        "updated_at",
    ),
    "operator_instance_events": (
        "id",
        "instance_id",
        "event_type",
        "event_payload",
        "occurred_at",
    ),
    "visual_fallback_values": (
        "id",
        "course_task_type_id",
        "metric_code",
        "value",
        "created_at",
    ),
    "task_node_dependencies": ("node_id", "prerequisite_node_id"),
}

EXPECTED_DATABASE_INDEXES = {
    "idx_task_nodes_ready_claim": "task_nodes",
    "idx_course_task_types_task_query": "course_task_types",
    "idx_task_nodes_task_query": "task_nodes",
    "idx_outbox_events_pending_scan": "outbox_events",
    "idx_task_node_dependencies_prerequisite": "task_node_dependencies",
    "idx_operator_instance_events_instance_time": "operator_instance_events",
}
WILDCARD_HOST = "*"
EXPECTED_PLATFORM_PORTS = {
    "postgres": (5432, 5432, "tcp", "127.0.0.1"),
    "redis": (6379, 6379, "tcp", "127.0.0.1"),
    "kafka": (9092, 9092, "tcp", "127.0.0.1"),
    "mongodb": (27017, 27017, "tcp", "127.0.0.1"),
    "control-service": (18100, 18100, "tcp", WILDCARD_HOST),
    "orchestrator-service": (18101, 18101, "tcp", "127.0.0.1"),
    "vision-orchestrator-service": (18102, 8010, "tcp", "127.0.0.1"),
    "online-gateway-service": (18103, 8001, "tcp", WILDCARD_HOST),
}
EXPECTED_OPERATOR_PORTS = {
    **{
        f"{operator}-gpu{gpu}": (
            (gpu + 1) * 10000 + suffix,
            target,
            "tcp",
            "127.0.0.1",
        )
        for operator, target, suffix in (
            ("asr-offline", 8083, 8083),
            ("asr-online", 8084, 8084),
            ("ocr", 8866, 8866),
            ("vbas", 8981, 8981),
            ("facerec", 8000, 8003),
            ("screen-det", 8880, 8880),
        )
        for gpu in range(3)
    },
    **{
        f"{operator}-cpu{index}": (
            (index + 1) * 10000 + suffix,
            target,
            "tcp",
            "127.0.0.1",
        )
        for operator, target, suffix in (("ppt-slice", 9001, 9001),)
        for index in range(3)
    },
}


def _csv_text(rows: list[tuple[str, ...]]) -> str:
    stream = io.StringIO()
    csv_writer = writer(stream, lineterminator="\n")
    csv_writer.writerows(rows)
    return stream.getvalue()


def _database_table_rows() -> list[tuple[str, ...]]:
    return [(table, f"{table}中文说明") for table in EXPECTED_DATABASE_COLUMNS]


def _database_column_rows() -> list[tuple[str, ...]]:
    return [
        (table, column, f"{column}中文说明")
        for table, columns in EXPECTED_DATABASE_COLUMNS.items()
        for column in columns
    ]


def _database_index_rows() -> list[tuple[str, ...]]:
    return [(table, index) for index, table in EXPECTED_DATABASE_INDEXES.items()]


def test_runtime_preflight_database_columns_match_control_readiness() -> None:
    from control_service.app.infrastructure.runtime import CONTROL_SCHEMA_COLUMNS

    from deploy.scripts.preflight_checks import EXPECTED_DATABASE_COLUMNS as PREFLIGHT_COLUMNS

    assert PREFLIGHT_COLUMNS == {
        table: set(columns) for table, columns in CONTROL_SCHEMA_COLUMNS.items()
    }


def _kafka_topic_output(
    *,
    topics: tuple[str, ...] = (
        "algorithm.course.commands",
        "algorithm.visual.commands",
        "algorithm.visual.events",
    ),
    partitions: int = 1,
    replicas: int = 1,
) -> str:
    return "\n".join(
        f"Topic: {topic}\tTopicId: id-{index}\tPartitionCount: {partitions}"
        f"\tReplicationFactor: {replicas}\tConfigs:"
        for index, topic in enumerate(topics)
    )


def _platform_compose_config() -> dict[str, Any]:
    def service(name: str, *, shared_storage: bool = False) -> dict[str, Any]:
        published, target, protocol, host_ip = EXPECTED_PLATFORM_PORTS[name]
        volumes: list[dict[str, Any]] = []
        if shared_storage:
            volumes = [
                {"type": "bind", "source": "/data/course", "target": "/data/course"},
                {"type": "bind", "source": "/data/result", "target": "/data/result"},
            ]
        port = {
            "published": str(published),
            "target": target,
            "protocol": protocol,
        }
        if host_ip != WILDCARD_HOST:
            port["host_ip"] = host_ip
        return {
            "ports": [port],
            "volumes": volumes,
        }

    return {
        "services": {
            "postgres": service("postgres"),
            "redis": service("redis"),
            "kafka": service("kafka"),
            "mongodb": service("mongodb"),
            "control-service": service("control-service", shared_storage=True),
            "orchestrator-service": service(
                "orchestrator-service", shared_storage=True
            ),
            "vision-orchestrator-service": service(
                "vision-orchestrator-service", shared_storage=True
            ),
            "online-gateway-service": service("online-gateway-service"),
        }
    }


def _operator_compose_config() -> dict[str, Any]:
    def environment(
        instance_id: str, target: int, capacity: int, *, gpu: int | None = None
    ) -> dict[str, str]:
        values = {
            "OPERATOR_PORT": str(target),
            "PORT": str(target),
            "PLATFORM_INSTANCE_ID": instance_id,
            "PLATFORM_SERVICE_URL": f"http://{instance_id}:{target}",
        }
        if gpu is not None:
            values.update(
                {
                    "PLATFORM_GPU_ID": str(gpu),
                    "NVIDIA_VISIBLE_DEVICES": str(gpu),
                }
            )
        return values

    services: dict[str, Any] = {}
    gpu_operators = (
        ("asr-offline", 8083),
        ("asr-online", 8084),
        ("ocr", 8866),
        ("vbas", 8981),
        ("facerec", 8000),
        ("screen-det", 8880),
    )
    cpu_operators = (("ppt-slice", 9001, 10),)
    for operator, target in gpu_operators:
        for gpu in range(3):
            instance_id = f"{operator}-gpu{gpu}"
            published, expected_target, protocol, host_ip = EXPECTED_OPERATOR_PORTS[
                instance_id
            ]
            assert expected_target == target
            services[instance_id] = {
                "profiles": [f"gpu{gpu}"],
                "environment": environment(instance_id, target, 1, gpu=gpu),
                "deploy": {
                    "resources": {
                        "reservations": {
                            "devices": [
                                {
                                    "driver": "nvidia",
                                    "device_ids": [str(gpu)],
                                    "capabilities": ["gpu"],
                                }
                            ]
                        }
                    }
                },
                "ports": [
                    {
                        "published": str(published),
                        "target": target,
                        "protocol": protocol,
                        "host_ip": host_ip,
                    }
                ],
                "volumes": [
                    {"type": "bind", "source": "/data/course", "target": "/data/course"},
                    {"type": "bind", "source": "/data/result", "target": "/data/result"},
                ],
            }
    for operator, target, capacity in cpu_operators:
        for index in range(3):
            instance_id = f"{operator}-cpu{index}"
            published, expected_target, protocol, host_ip = EXPECTED_OPERATOR_PORTS[
                instance_id
            ]
            assert expected_target == target
            services[instance_id] = {
                "profiles": ["cpu"],
                "environment": environment(instance_id, target, capacity),
                "ports": [
                    {
                        "published": str(published),
                        "target": target,
                        "protocol": protocol,
                        "host_ip": host_ip,
                    }
                ],
                "volumes": [
                    {"type": "bind", "source": "/data/course", "target": "/data/course"},
                    {"type": "bind", "source": "/data/result", "target": "/data/result"},
                ],
            }
    return {"services": services}


def _operator_runtime_fixtures() -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    service_ids: dict[str, str] = {}
    inspections: dict[str, dict[str, Any]] = {}
    for service_name, service in _operator_compose_config()["services"].items():
        container_id = hashlib.sha256(service_name.encode()).hexdigest()
        service_ids[service_name] = container_id
        image_family = service_name.rsplit("-", 1)[0]
        image_id = f"sha256:{hashlib.sha256(image_family.encode()).hexdigest()}"
        published, target, protocol, _ = EXPECTED_OPERATOR_PORTS[service_name]
        environment = [f"{key}={value}" for key, value in service["environment"].items()]
        profiles = service["profiles"]
        gpu = profiles[0].removeprefix("gpu") if profiles[0].startswith("gpu") else None
        device_requests: list[dict[str, Any]] = []
        if gpu is not None:
            device_requests = [
                {
                    "Driver": "nvidia",
                    "Count": 0,
                    "DeviceIDs": [gpu],
                    "Capabilities": [["gpu"]],
                    "Options": {},
                }
            ]
        inspections[container_id] = {
            "Id": container_id,
            "Image": image_id,
            "Name": f"/algorithm-operators-{service_name}-1",
            "State": {"Running": True, "Status": "running"},
            "Config": {
                "Env": environment,
                "Labels": {"com.docker.compose.service": service_name},
            },
            "HostConfig": {
                "DeviceRequests": device_requests,
                "PortBindings": {
                    f"{target}/{protocol}": [
                        {"HostIp": "127.0.0.1", "HostPort": str(published)}
                    ]
                },
            },
            "Mounts": [
                {
                    "Type": "bind",
                    "Source": "/data/course",
                    "Destination": "/data/course",
                    "RW": True,
                },
                {
                    "Type": "bind",
                    "Source": "/data/result",
                    "Destination": "/data/result",
                    "RW": True,
                },
            ],
        }
    return service_ids, inspections


def _platform_runtime_fixtures() -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    service_ids: dict[str, str] = {}
    inspections: dict[str, dict[str, Any]] = {}
    for service_name in (
        "control-service",
        "orchestrator-service",
        "vision-orchestrator-service",
        "online-gateway-service",
    ):
        container_id = hashlib.sha256(f"container:{service_name}".encode()).hexdigest()
        image_id = f"sha256:{hashlib.sha256(f'image:{service_name}'.encode()).hexdigest()}"
        service_ids[service_name] = container_id
        inspections[container_id] = {
            "Id": container_id,
            "Image": image_id,
            "Name": f"/algorithm-scheduling-platform-{service_name}-1",
            "State": {"Running": True, "Status": "running"},
            "Config": {
                "Labels": {"com.docker.compose.service": service_name},
            },
        }
    return service_ids, inspections


def _image_inspection_fixtures(
    *inspection_sets: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        record["Image"]: {
            "Id": record["Image"],
            "Config": {
                "Labels": {"org.opencontainers.image.revision": "a" * 40}
            },
        }
        for inspections in inspection_sets
        for record in inspections.values()
    }


def _registered_operator_instances() -> list[dict[str, Any]]:
    contracts = {
        "asr-offline": ("asr_offline", ["asr_offline"], 8083, 4),
        "asr-online": ("asr_online", ["asr_online"], 8084, 10),
        "ocr": ("ocr", ["ocr"], 8866, 256),
        "vbas": ("vbas", ["student_behavior", "teacher_behavior"], 8981, 1024),
        "facerec": ("facerec", ["recognize"], 8000, 128),
        "screen-det": ("screen_det", ["detect_all"], 8880, 128),
        "ppt-slice": ("ppt_slice", ["ppt_slice"], 9001, 10),
    }
    instances: list[dict[str, Any]] = []
    for prefix, (code, capabilities, port, capacity) in contracts.items():
        kind = "cpu" if prefix == "ppt-slice" else "gpu"
        for index in range(3):
            instance_id = f"{prefix}-{kind}{index}"
            instances.append(
                {
                    "instance_id": instance_id,
                    "operator_code": code,
                    "capabilities": capabilities,
                    "service_url": f"http://{instance_id}:{port}",
                    "declared_capacity": capacity,
                    "labels": {"gpu": str(index)} if kind == "gpu" else {},
                    "lifecycle": "ONLINE",
                    "inflight": 0,
                    "model_ready": True,
                    "last_heartbeat_at": "2026-08-12T00:00:01Z",
                }
            )
    return instances


def _registration_responses(
    instances: list[dict[str, Any]],
) -> dict[str, tuple[int, dict[str, Any] | list[dict[str, Any]]]]:
    responses: dict[str, tuple[int, dict[str, Any] | list[dict[str, Any]]]] = {
        "/ops/operator-instances": (200, instances)
    }
    for instance in instances:
        instance_id = instance["instance_id"]
        responses[f"/ops/operator-instances/{instance_id}/events?limit=100"] = (
            200,
            [
                {"event_type": "REGISTERED"},
                {
                    "event_type": "HEARTBEAT_SUMMARY",
                    "event_payload": {"model_ready": True},
                },
            ],
        )
    return responses


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _base_environment(fake_bin: Path, **overrides: str) -> dict[str, str]:
    operator_service_ids, operator_inspections = _operator_runtime_fixtures()
    platform_service_ids, platform_inspections = _platform_runtime_fixtures()
    default_course_root = fake_bin.parent / "default-course-root"
    default_result_root = fake_bin.parent / "default-result-root"
    default_course_root.mkdir(exist_ok=True)
    default_result_root.mkdir(exist_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "COMMAND_LOG": str(fake_bin / "commands.jsonl"),
            "DF_AVAILABLE_KIB": str(200 * 1024 * 1024),
            "GPU_OUTPUT": "\n".join(
                f"{index}, {gpu_uuid}" for index, gpu_uuid in enumerate(GPU_UUIDS)
            ),
            "GIT_SHA": "a" * 40,
            "GIT_STATUS": "",
            "EXPECTED_GIT_SHA": "a" * 40,
            "OPERATOR_REGISTRY_TOKEN": "test-explicit-registry-token",
            "SS_OUTPUT": "",
            "DOCKER_PS_IDS": "",
            "DOCKER_INSPECT_FIXTURES": "{}",
            "PLATFORM_COMPOSE_CONFIG": json.dumps(_platform_compose_config()),
            "OPERATOR_COMPOSE_CONFIG": json.dumps(_operator_compose_config()),
            "DB_TABLES_OUTPUT": _csv_text(_database_table_rows()),
            "DB_COLUMNS_OUTPUT": _csv_text(_database_column_rows()),
            "DB_INDEXES_OUTPUT": _csv_text(_database_index_rows()),
            "KAFKA_TOPICS_OUTPUT": _kafka_topic_output(),
            "OPERATOR_SERVICE_IDS": json.dumps(operator_service_ids),
            "OPERATOR_INSPECT_FIXTURES": json.dumps(operator_inspections),
            "PLATFORM_SERVICE_IDS": json.dumps(platform_service_ids),
            "PLATFORM_INSPECT_FIXTURES": json.dumps(platform_inspections),
            "IMAGE_INSPECT_FIXTURES": json.dumps(
                _image_inspection_fixtures(operator_inspections, platform_inspections)
            ),
            "COURSE_ROOT": str(default_course_root),
            "RESULT_ROOT": str(default_result_root),
        }
    )
    environment.update(overrides)
    return environment


def _separate_shared_roots(tmp_path: Path) -> tuple[Path, Path]:
    course = tmp_path / "course"
    result = tmp_path / "result"
    course.mkdir(exist_ok=True)
    result.mkdir(exist_ok=True)
    return course, result


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
  "status --porcelain")
    printf '%s' "${GIT_STATUS}"
    exit "${GIT_STATUS_EXIT:-0}"
    ;;
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
if args[:1] == ["compose"] and "config" in args:
    variable = (
        "OPERATOR_COMPOSE_CONFIG"
        if any("docker-compose.operators.yml" in argument for argument in args)
        else "PLATFORM_COMPOSE_CONFIG"
    )
    document = json.loads(os.environ[variable])
    for service in document.get("services", {}).values():
        for mount in service.get("volumes", []):
            if mount.get("source") == "/data/course":
                mount["source"] = os.environ.get("COURSE_ROOT", "/data/course")
            elif mount.get("source") == "/data/result":
                mount["source"] = os.environ.get("RESULT_ROOT", "/data/result")
    print(json.dumps(document))
    raise SystemExit(int(os.environ.get("COMPOSE_CONFIG_EXIT", "0")))
if args[:1] == ["compose"] and "exec" in args:
    if "postgres" in args and "psql" in args:
        command = args[-1]
        if "col_description" in command:
            print(os.environ.get("DB_COLUMNS_OUTPUT", ""), end="")
        elif "pg_indexes" in command:
            print(os.environ.get("DB_INDEXES_OUTPUT", ""), end="")
        elif "obj_description" in command:
            print(os.environ.get("DB_TABLES_OUTPUT", ""), end="")
        else:
            raise SystemExit(65)
        raise SystemExit(int(os.environ.get("PSQL_EXIT", "0")))
    if "kafka" in args and any(argument.endswith("/kafka-topics.sh") for argument in args):
        print(os.environ.get("KAFKA_TOPICS_OUTPUT", ""))
        raise SystemExit(int(os.environ.get("KAFKA_TOPICS_EXIT", "0")))
if args[:1] == ["compose"] and "ps" in args:
    if os.environ.get("COMPOSE_PS_EXIT", "0") != "0":
        raise SystemExit(int(os.environ["COMPOSE_PS_EXIT"]))
    variable = (
        "OPERATOR_SERVICE_IDS"
        if any("docker-compose.operators.yml" in argument for argument in args)
        else "PLATFORM_SERVICE_IDS"
    )
    service_ids = json.loads(os.environ.get(variable, "{}"))
    quiet_index = args.index("-q")
    for service in args[quiet_index + 1:]:
        container_id = service_ids.get(service)
        if container_id:
            print(container_id)
    raise SystemExit(0)
if args[:2] == ["image", "inspect"] and len(args) >= 3:
    fixtures = json.loads(os.environ.get("IMAGE_INSPECT_FIXTURES", "{}"))
    if not all(image_id in fixtures for image_id in args[2:]):
        raise SystemExit(1)
    print(json.dumps([fixtures[image_id] for image_id in args[2:]]))
    raise SystemExit(int(os.environ.get("IMAGE_INSPECT_EXIT", "0")))
if args[:1] == ["run"]:
    print(os.environ.get("GPU_OUTPUT", ""))
    raise SystemExit(int(os.environ.get("GPU_RUN_EXIT", "0")))
if args[:1] == ["exec"] and len(args) >= 5:
    version = os.environ.get("OPERATOR_PACKAGE_PROBE_VERSION", "0.2.0")
    forbidden = json.loads(os.environ.get("OPERATOR_PACKAGE_PROBE_FORBIDDEN", "[]"))
    print(json.dumps({
        "distribution": "algorithm-operator-registry-client",
        "version": version,
        "forbidden_reads": forbidden,
    }, sort_keys=True, separators=(",", ":")))
    raise SystemExit(int(os.environ.get("OPERATOR_PACKAGE_PROBE_EXIT", "0")))
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
if args[:1] == ["inspect"] and len(args) >= 2:
    runtime_fixtures = {
        **json.loads(os.environ.get("OPERATOR_INSPECT_FIXTURES", "{}")),
        **json.loads(os.environ.get("PLATFORM_INSPECT_FIXTURES", "{}")),
    }
    if all(container_id in runtime_fixtures for container_id in args[1:]):
        records = [runtime_fixtures[container_id] for container_id in args[1:]]
        for record in records:
            for mount in record.get("Mounts", []):
                if mount.get("Source") == "/data/course":
                    mount["Source"] = os.environ.get("COURSE_ROOT", "/data/course")
                elif mount.get("Source") == "/data/result":
                    mount["Source"] = os.environ.get("RESULT_ROOT", "/data/result")
        print(json.dumps(records))
        raise SystemExit(int(os.environ.get("OPERATOR_INSPECT_EXIT", "0")))
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


@pytest.mark.parametrize(
    ("process_name", "expected_framework", "expected_interpreter"),
    [
        ("asr_offline", "torch", "python"),
        ("asr_online", "torch", "python"),
        ("vbas", "torch", "python"),
        ("screen_det", "torch", "python"),
        ("ocr", "paddle", "python"),
        ("facerec", "fastdeploy", "python3"),
    ],
)
def test_gpu_framework_default_probe_interpreter_contract(
    process_name: str, expected_framework: str, expected_interpreter: str
) -> None:
    namespace = runpy.run_path(str(SCRIPTS / "verify-gpu-instance"))

    framework, argv = namespace["_framework_probe_argv"](process_name)

    assert framework == expected_framework
    assert argv[:2] == [expected_interpreter, "-c"]


def test_gpu_evidence_atomic_write_removes_final_link_when_directory_fsync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = runpy.run_path(str(SCRIPTS / "verify-gpu-instance"))
    output = tmp_path / "gpu-evidence.json"
    real_fsync = os.fsync
    directory_fsync_failed = False

    def fail_first_directory_fsync(descriptor: int) -> None:
        nonlocal directory_fsync_failed
        if not directory_fsync_failed and output.exists() and os.path.isdir(descriptor):
            directory_fsync_failed = True
            raise OSError("injected directory fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_first_directory_fsync)

    with pytest.raises(OSError, match="injected directory fsync failure"):
        namespace["_atomic_write_once"](output, {"status": "PASS"})

    assert directory_fsync_failed is True
    assert not output.exists()
    assert not output.is_symlink()


def test_gpu_evidence_main_resets_pass_before_writing_late_failure_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = runpy.run_path(str(SCRIPTS / "verify-gpu-instance"))
    release_sha = "a" * 40
    output = tmp_path / "releases" / "test" / release_sha / "gpu-instances" / "gpu.json"
    output.parent.mkdir(parents=True)
    arguments = argparse.Namespace(
        container="asr-offline-gpu0",
        instance_id="asr-offline-gpu0",
        physical_gpu=0,
        process_name="asr_offline",
        output=output,
        trigger_file=None,
        probe_file=None,
        sample_window=1.0,
        sample_interval=0.1,
        trigger_timeout=10.0,
        assert_stopped=False,
        evidence=None,
        stop_timeout=1.0,
        command_timeout=1.0,
    )
    writes: list[dict[str, Any]] = []

    def verify_running(
        _arguments: argparse.Namespace,
        report: dict[str, Any],
    ) -> None:
        report["release_sha"] = release_sha

    def fail_then_write(path: Path, payload: dict[str, Any]) -> None:
        writes.append(dict(payload))
        if len(writes) == 1:
            raise OSError("injected late publication failure")
        path.write_text(json.dumps(payload), encoding="utf-8")

    main_globals = namespace["main"].__globals__
    monkeypatch.setitem(main_globals, "_parse_args", lambda: arguments)
    monkeypatch.setitem(main_globals, "_verify_running", verify_running)
    monkeypatch.setitem(main_globals, "_atomic_write_once", fail_then_write)

    assert namespace["main"]() == 2
    assert [write["status"] for write in writes] == ["PASS", "FAIL"]
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "FAIL"


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


@pytest.fixture
def readiness_server() -> Any:
    state: dict[str, tuple[int, Any]] = {
        "/control": (200, {"status": "ready"}),
        "/orchestrator": (200, {"status": "ready"}),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            status, payload = state.get(self.path, (404, {"status": "missing"}))
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


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
    assert [
        "docker",
        "run",
        "--rm",
        "--gpus",
        "all",
        "--entrypoint",
        "nvidia-smi",
        "nvidia/cuda:12.4.1-base-ubuntu22.04",
        "--query-gpu=index,uuid",
        "--format=csv,noheader,nounits",
    ] in _commands(environment)


def test_preflight_rejects_missing_operator_registry_token_before_docker(
    fake_bin: Path,
) -> None:
    environment = _base_environment(fake_bin)
    environment.pop("OPERATOR_REGISTRY_TOKEN")

    completed = _run("preflight", "host", environment=environment)

    assert completed.returncode != 0
    assert "OPERATOR_REGISTRY_TOKEN is required" in completed.stderr
    assert _commands(environment) == []


def test_preflight_rejects_known_development_registry_token_before_docker(
    fake_bin: Path,
) -> None:
    environment = _base_environment(
        fake_bin,
        OPERATOR_REGISTRY_TOKEN="local-development-registry-token",
    )

    completed = _run("preflight", "host", environment=environment)

    assert completed.returncode != 0
    assert "development registry token is forbidden" in completed.stderr
    assert _commands(environment) == []


def test_preflight_fails_closed_when_git_status_cannot_be_inspected(
    fake_bin: Path, tmp_path: Path
) -> None:
    course, result = _separate_shared_roots(tmp_path)
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
        GIT_STATUS_EXIT="23",
    )

    completed = _run("preflight", "host", environment=environment)

    assert completed.returncode != 0
    assert "Git working tree inspection failed" in completed.stderr
    assert "preflight host: PASS" not in completed.stdout


def test_preflight_explicit_host_stage_matches_the_default(
    fake_bin: Path, tmp_path: Path
) -> None:
    course, result = _separate_shared_roots(tmp_path)
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
    )

    completed = _run("preflight", "host", environment=environment)

    assert completed.returncode == 0, completed.stderr
    assert "preflight host: PASS" in completed.stdout
    compose_commands = [command for command in _commands(environment) if "config" in command]
    assert len(compose_commands) == 2
    operator_command = next(
        command
        for command in compose_commands
        if any("docker-compose.operators.yml" in argument for argument in command)
    )
    assert "--profile" in operator_command
    assert "*" in operator_command
    assert operator_command[-3:] == ["config", "--format", "json"]


@pytest.mark.parametrize(
    "relationship",
    ["same", "course-contains-result", "result-contains-course", "symlink-alias"],
)
def test_preflight_rejects_overlapping_shared_roots(
    fake_bin: Path, tmp_path: Path, relationship: str
) -> None:
    storage = tmp_path / "storage"
    storage.mkdir()
    if relationship == "same":
        course = result = storage / "shared"
        course.mkdir()
    elif relationship == "course-contains-result":
        course = storage / "course"
        result = course / "persistent-result"
        result.mkdir(parents=True)
    elif relationship == "result-contains-course":
        result = storage / "result"
        course = result / "temporary-course"
        course.mkdir(parents=True)
    else:
        course = storage / "course"
        course.mkdir()
        result = storage / "result-alias"
        result.symlink_to(course, target_is_directory=True)
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
    )

    completed = _run("preflight", "host", environment=environment)

    assert completed.returncode != 0
    assert "overlap" in completed.stderr


def test_preflight_rejects_case_alias_samefile_shared_roots(
    fake_bin: Path, tmp_path: Path
) -> None:
    course = tmp_path / "SharedRoot"
    course.mkdir()
    result = course.with_name(course.name.swapcase())
    if not result.exists():
        pytest.skip("filesystem is case-sensitive")
    assert course.samefile(result)
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
    )

    completed = _run("preflight", "host", environment=environment)

    assert completed.returncode != 0
    assert "overlap" in completed.stderr


def test_preflight_accepts_normalized_separate_shared_roots(
    fake_bin: Path, tmp_path: Path
) -> None:
    course, result = _separate_shared_roots(tmp_path)
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course / ".." / "course"),
        RESULT_ROOT=str(result / ".." / "result"),
    )

    completed = _run("preflight", "host", environment=environment)

    assert completed.returncode == 0, completed.stderr


def test_preflight_accepts_samefile_compose_mount_source_alias(
    fake_bin: Path, tmp_path: Path
) -> None:
    course, result = _separate_shared_roots(tmp_path)
    course_alias = course.with_name(course.name.swapcase())
    if not course_alias.exists():
        pytest.skip("filesystem is case-sensitive")
    assert course.samefile(course_alias)
    document = _operator_compose_config()
    document["services"]["asr-offline-gpu0"]["volumes"][0]["source"] = str(
        course_alias
    )
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
        OPERATOR_COMPOSE_CONFIG=json.dumps(document),
    )

    completed = _run("preflight", "host", environment=environment)

    assert completed.returncode == 0, completed.stderr


def test_preflight_ignores_runtime_banner_before_three_gpu_records(
    fake_bin: Path, tmp_path: Path
) -> None:
    banner = "\n".join(f"arbitrary runtime banner line {index}" for index in range(9))
    gpu_records = "\n".join(
        [
            "0, GPU-11111111-1111-1111-1111-111111111111",
            "1, GPU-22222222-2222-2222-2222-222222222222",
            "2, GPU-33333333-3333-3333-3333-333333333333",
        ]
    )
    course, result = _separate_shared_roots(tmp_path)
    environment = _base_environment(
        fake_bin,
        GPU_OUTPUT=f"{banner}\n{gpu_records}",
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
        REQUIRED_PORTS="",
    )

    completed = _run("preflight", environment=environment)

    assert completed.returncode == 0, completed.stderr
    assert "preflight: PASS" in completed.stdout


@pytest.mark.parametrize(
    "gpu_output",
    [
        "\n".join(
            [
                "0, GPU-11111111-1111-1111-1111-111111111111",
                "0, GPU-22222222-2222-2222-2222-222222222222",
                "2, GPU-33333333-3333-3333-3333-333333333333",
            ]
        ),
        "\n".join(
            [
                "0, GPU-11111111-1111-1111-1111-111111111111",
                "1, GPU-11111111-1111-1111-1111-111111111111",
                "2, GPU-33333333-3333-3333-3333-333333333333",
            ]
        ),
    ],
    ids=["duplicate-index", "duplicate-uuid"],
)
def test_preflight_rejects_duplicate_gpu_identity(
    fake_bin: Path, tmp_path: Path, gpu_output: str
) -> None:
    course, result = _separate_shared_roots(tmp_path)
    environment = _base_environment(
        fake_bin,
        GPU_OUTPUT=gpu_output,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
        REQUIRED_PORTS="",
    )

    completed = _run("preflight", environment=environment)

    assert completed.returncode != 0
    assert "unique" in completed.stderr


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


@pytest.mark.parametrize(
    "gpu_output",
    [
        "\n".join(f"{index}, {GPU_UUIDS[index]}" for index in range(2)),
        "\n".join(
            [
                *(f"{index}, {GPU_UUIDS[index]}" for index in range(3)),
                "3, GPU-44444444-4444-4444-4444-444444444444",
            ]
        ),
        "nine lines of output\nwithout a valid GPU record",
        "\n".join(
            [
                f"0, {GPU_UUIDS[0]}",
                f"1, {GPU_UUIDS[1]}",
                "2, GPU-not-a-uuid",
            ]
        ),
    ],
    ids=["two", "four", "no-valid-records", "malformed-uuid"],
)
def test_preflight_rejects_any_gpu_count_other_than_three(
    fake_bin: Path, tmp_path: Path, gpu_output: str
) -> None:
    course, result = _separate_shared_roots(tmp_path)
    environment = _base_environment(
        fake_bin,
        GPU_OUTPUT=gpu_output,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
        REQUIRED_PORTS="",
    )

    completed = _run("preflight", environment=environment)

    assert completed.returncode != 0
    assert "exactly 3 GPUs" in completed.stderr


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing-service", "21"),
        ("mismatched-instance-id", "instance ID"),
        ("gpu-profile", "GPU"),
        ("gpu-environment", "GPU"),
        ("gpu-reservation", "GPU"),
        ("cpu-environment", "CPU"),
        ("cpu-reservation", "CPU"),
        ("result-not-bind", "/data/result"),
        ("result-read-only", "/data/result"),
    ],
)
def test_preflight_rejects_invalid_authoritative_operator_compose(
    fake_bin: Path, tmp_path: Path, mutation: str, message: str
) -> None:
    course, result = _separate_shared_roots(tmp_path)
    document = _operator_compose_config()
    services = document["services"]
    gpu_service = services["asr-offline-gpu0"]
    cpu_service = services["ppt-slice-cpu0"]
    if mutation == "missing-service":
        del services["asr-offline-gpu0"]
    elif mutation == "mismatched-instance-id":
        gpu_service["environment"]["PLATFORM_INSTANCE_ID"] = "asr-offline-gpu1"
    elif mutation == "gpu-profile":
        gpu_service["profiles"] = ["gpu1"]
    elif mutation == "gpu-environment":
        gpu_service["environment"]["NVIDIA_VISIBLE_DEVICES"] = "1"
    elif mutation == "gpu-reservation":
        gpu_service["deploy"]["resources"]["reservations"]["devices"][0][
            "device_ids"
        ] = ["1"]
    elif mutation == "cpu-environment":
        cpu_service["environment"]["PLATFORM_GPU_ID"] = "0"
    elif mutation == "cpu-reservation":
        cpu_service["deploy"] = {
            "resources": {
                "reservations": {
                    "devices": [
                        {
                            "driver": "nvidia",
                            "device_ids": ["0"],
                            "capabilities": ["gpu"],
                        }
                    ]
                }
            }
        }
    elif mutation == "result-not-bind":
        gpu_service["volumes"][1]["type"] = "volume"
    elif mutation == "result-read-only":
        gpu_service["volumes"][1]["read_only"] = True
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
        OPERATOR_COMPOSE_CONFIG=json.dumps(document),
    )

    completed = _run("preflight", "host", environment=environment)

    assert completed.returncode != 0
    assert message in completed.stderr


@pytest.mark.parametrize(
    ("config_variable", "service_name"),
    [
        *(
            ("PLATFORM_COMPOSE_CONFIG", service_name)
            for service_name in EXPECTED_PLATFORM_PORTS
        ),
        *(
            ("OPERATOR_COMPOSE_CONFIG", service_name)
            for service_name in EXPECTED_OPERATOR_PORTS
        ),
    ],
)
def test_preflight_requires_canonical_port_mapping_for_every_service(
    fake_bin: Path,
    tmp_path: Path,
    config_variable: str,
    service_name: str,
) -> None:
    course, result = _separate_shared_roots(tmp_path)
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
    )
    document = json.loads(environment[config_variable])
    del document["services"][service_name]["ports"]
    environment[config_variable] = json.dumps(document)

    completed = _run("preflight", "host", environment=environment)

    assert completed.returncode != 0
    assert service_name in completed.stderr
    assert "port" in completed.stderr.lower()


@pytest.mark.parametrize(
    ("config_variable", "service_name"),
    [
        ("PLATFORM_COMPOSE_CONFIG", "vision-orchestrator-service"),
        ("OPERATOR_COMPOSE_CONFIG", "asr-offline-gpu0"),
    ],
)
@pytest.mark.parametrize(
    "mutation",
    [
        "extra",
        "published",
        "target",
        "protocol",
        "host-ip",
        "invalid-host-ip",
        "wrong-type-host-ip",
        "duplicate",
    ],
)
def test_preflight_rejects_noncanonical_compose_port_mapping(
    fake_bin: Path,
    tmp_path: Path,
    config_variable: str,
    service_name: str,
    mutation: str,
) -> None:
    course, result = _separate_shared_roots(tmp_path)
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
    )
    document = json.loads(environment[config_variable])
    ports = document["services"][service_name]["ports"]
    if mutation == "extra":
        ports.append({"published": "65000", "target": 65000, "protocol": "tcp"})
    elif mutation == "published":
        ports[0]["published"] = "65001"
    elif mutation == "target":
        ports[0]["target"] += 1
    elif mutation == "protocol":
        ports[0]["protocol"] = "udp"
    elif mutation == "host-ip":
        ports[0]["host_ip"] = "0.0.0.0"
    elif mutation == "invalid-host-ip":
        ports[0]["host_ip"] = "not-an-ip"
    elif mutation == "wrong-type-host-ip":
        ports[0]["host_ip"] = 123
    else:
        ports.append(dict(ports[0]))
    environment[config_variable] = json.dumps(document)

    completed = _run("preflight", "host", environment=environment)

    assert completed.returncode != 0
    assert service_name in completed.stderr
    assert "port" in completed.stderr.lower()


@pytest.mark.parametrize("host_ip", [None, "", "0.0.0.0", "::"])
def test_preflight_requires_mongodb_loopback_host_ip(
    fake_bin: Path, tmp_path: Path, host_ip: str | None
) -> None:
    course, result = _separate_shared_roots(tmp_path)
    document = _platform_compose_config()
    port = document["services"]["mongodb"]["ports"][0]
    if host_ip is None:
        del port["host_ip"]
    else:
        port["host_ip"] = host_ip
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
        PLATFORM_COMPOSE_CONFIG=json.dumps(document),
    )

    completed = _run("preflight", "host", environment=environment)

    assert completed.returncode != 0
    assert "mongodb" in completed.stderr
    assert "port" in completed.stderr.lower()


@pytest.mark.parametrize("host_ip", [None, "", "0.0.0.0", "::"])
def test_preflight_accepts_equivalent_wildcard_compose_host_ips(
    fake_bin: Path, tmp_path: Path, host_ip: str | None
) -> None:
    course, result = _separate_shared_roots(tmp_path)
    document = _platform_compose_config()
    port = document["services"]["control-service"]["ports"][0]
    if host_ip is not None:
        port["host_ip"] = host_ip
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
        PLATFORM_COMPOSE_CONFIG=json.dumps(document),
    )

    completed = _run("preflight", "host", environment=environment)

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    ("config_variable", "service", "port"),
    [
        ("PLATFORM_COMPOSE_CONFIG", "control-service", "18100"),
        ("OPERATOR_COMPOSE_CONFIG", "asr-offline-gpu0", "18083"),
    ],
)
def test_preflight_derives_occupied_ports_from_both_rendered_compose_documents(
    fake_bin: Path,
    tmp_path: Path,
    config_variable: str,
    service: str,
    port: str,
) -> None:
    course, result = _separate_shared_roots(tmp_path)
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
        REQUIRED_PORTS="",
        SS_OUTPUT=f"LISTEN 0 128 0.0.0.0:{port} 0.0.0.0:*\n",
    )
    document = json.loads(environment[config_variable])
    assert document["services"][service]["ports"][0]["published"] == port

    completed = _run("preflight", "host", environment=environment)

    assert completed.returncode != 0
    assert port in completed.stderr
    assert "unauthorized" in completed.stderr


@pytest.mark.parametrize(
    ("displayed_endpoint", "authorized_endpoint"),
    [
        ("0.0.0.0:18100", "0.0.0.0:18100"),
        ("127.0.0.1:18101", "127.0.0.1:18101"),
        ("[::]:18100", "[::]:18100"),
        (":::18100", "[::]:18100"),
        ("*:18100", "0.0.0.0:18100"),
        ("*:18100", "[::]:18100"),
        ("127.0.0.1%lo:18101", "127.0.0.1:18101"),
        ("[::]%lo:18100", "[::]:18100"),
    ],
)
def test_preflight_accepts_an_exactly_authorized_compose_derived_endpoint(
    fake_bin: Path,
    tmp_path: Path,
    displayed_endpoint: str,
    authorized_endpoint: str,
) -> None:
    course, result = _separate_shared_roots(tmp_path)
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
        REQUIRED_PORTS="",
        SS_OUTPUT=f"LISTEN 0 128 {displayed_endpoint} 0.0.0.0:*\n",
        AUTHORIZED_OCCUPIED_ENDPOINTS=authorized_endpoint,
    )

    completed = _run("preflight", "host", environment=environment)

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    "local_endpoint",
    ["127.0.0.53%lo:53", "[fe80::1]%eth0:443", "fe80::1%eth0:443"],
)
def test_preflight_accepts_a_valid_scoped_unrelated_system_listener(
    fake_bin: Path, tmp_path: Path, local_endpoint: str
) -> None:
    course, result = _separate_shared_roots(tmp_path)
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
        REQUIRED_PORTS="",
        SS_OUTPUT=f"LISTEN 0 128 {local_endpoint} 0.0.0.0:*\n",
    )

    completed = _run("preflight", "host", environment=environment)

    assert completed.returncode == 0, completed.stderr


def test_preflight_rejects_a_scoped_concrete_address_not_in_the_authority(
    fake_bin: Path, tmp_path: Path
) -> None:
    course, result = _separate_shared_roots(tmp_path)
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
        REQUIRED_PORTS="",
        SS_OUTPUT="LISTEN 0 128 127.0.0.2%lo:18101 0.0.0.0:*\n",
        AUTHORIZED_OCCUPIED_ENDPOINTS="127.0.0.1:18101",
    )

    completed = _run("preflight", "host", environment=environment)

    assert completed.returncode != 0
    assert "127.0.0.2:18101" in completed.stderr
    assert "unauthorized" in completed.stderr


@pytest.mark.parametrize("peer_endpoint", ["0.0.0.0:*", "[::]:*", "*:*", ":::*"])
def test_preflight_accepts_a_valid_peer_wildcard_endpoint(
    fake_bin: Path, tmp_path: Path, peer_endpoint: str
) -> None:
    course, result = _separate_shared_roots(tmp_path)
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
        REQUIRED_PORTS="",
        SS_OUTPUT=f"LISTEN 0 128 127.0.0.1:53 {peer_endpoint}\n",
    )

    completed = _run("preflight", "host", environment=environment)

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("peer_endpoint", ["8.8.8.8:53", "0.0.0.0:53"])
def test_preflight_rejects_a_numeric_port_on_the_peer_endpoint(
    fake_bin: Path, tmp_path: Path, peer_endpoint: str
) -> None:
    course, result = _separate_shared_roots(tmp_path)
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
        REQUIRED_PORTS="",
        SS_OUTPUT=f"LISTEN 0 128 127.0.0.1:53 {peer_endpoint}\n",
    )

    completed = _run("preflight", "host", environment=environment)

    assert completed.returncode != 0
    assert "socket endpoint" in completed.stderr


@pytest.mark.parametrize("local_endpoint", ["127.0.0.1:*", "[::]:*", "*:*"])
def test_preflight_rejects_a_wildcard_port_on_the_local_endpoint(
    fake_bin: Path, tmp_path: Path, local_endpoint: str
) -> None:
    course, result = _separate_shared_roots(tmp_path)
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
        REQUIRED_PORTS="",
        SS_OUTPUT=f"LISTEN 0 128 {local_endpoint} 0.0.0.0:*\n",
    )

    completed = _run("preflight", "host", environment=environment)

    assert completed.returncode != 0
    assert "socket endpoint" in completed.stderr


@pytest.mark.parametrize(
    ("socket_row", "reason"),
    [
        (
            "LISTEN 0 128 127.0.0.1:53 0.0.0.0:* unexpected-column\n",
            "listening socket row",
        ),
        ("LISTEN 0 128 127.0.0.1:53 invalid-peer\n", "socket endpoint"),
    ],
)
def test_preflight_rejects_an_invalid_complete_socket_row(
    fake_bin: Path, tmp_path: Path, socket_row: str, reason: str
) -> None:
    course, result = _separate_shared_roots(tmp_path)
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
        REQUIRED_PORTS="",
        SS_OUTPUT=socket_row,
    )

    completed = _run("preflight", "host", environment=environment)

    assert completed.returncode != 0
    assert reason in completed.stderr


@pytest.mark.parametrize(
    "local_endpoint",
    [
        "127.0.0.53%bad/zone:53",
        "[fe80::1]%bad%zone:443",
        "[fe80::1]%:443",
        "[fe80::1%eth0]:443",
        "*%lo:443",
        "[*]:443",
    ],
)
def test_preflight_rejects_an_invalid_socket_scope_zone(
    fake_bin: Path, tmp_path: Path, local_endpoint: str
) -> None:
    course, result = _separate_shared_roots(tmp_path)
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
        REQUIRED_PORTS="",
        SS_OUTPUT=f"LISTEN 0 128 {local_endpoint} 0.0.0.0:*\n",
    )

    completed = _run("preflight", "host", environment=environment)

    assert completed.returncode != 0
    assert "socket endpoint" in completed.stderr


def test_preflight_does_not_honor_legacy_numeric_port_authorization(
    fake_bin: Path, tmp_path: Path
) -> None:
    course, result = _separate_shared_roots(tmp_path)
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
        REQUIRED_PORTS="",
        SS_OUTPUT="LISTEN 0 128 127.0.0.1:18101 0.0.0.0:*\n",
        AUTHORIZED_OCCUPIED_PORTS="18101",
    )

    completed = _run("preflight", "host", environment=environment)

    assert completed.returncode != 0
    assert "127.0.0.1:18101" in completed.stderr
    assert "unauthorized" in completed.stderr


def test_preflight_does_not_match_a_legacy_wildcard_to_a_concrete_authority(
    fake_bin: Path, tmp_path: Path
) -> None:
    course, result = _separate_shared_roots(tmp_path)
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
        REQUIRED_PORTS="",
        SS_OUTPUT="LISTEN 0 128 *:18101 0.0.0.0:*\n",
        AUTHORIZED_OCCUPIED_ENDPOINTS="127.0.0.1:18101",
    )

    completed = _run("preflight", "host", environment=environment)

    assert completed.returncode != 0
    assert "*:18101" in completed.stderr
    assert "unauthorized" in completed.stderr


def test_preflight_rejects_a_second_listener_on_an_authorized_port(
    fake_bin: Path, tmp_path: Path
) -> None:
    course, result = _separate_shared_roots(tmp_path)
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
        REQUIRED_PORTS="",
        SS_OUTPUT=(
            "LISTEN 0 128 127.0.0.1:18101 0.0.0.0:*\n"
            "LISTEN 0 128 127.0.0.2:18101 0.0.0.0:*\n"
        ),
        AUTHORIZED_OCCUPIED_ENDPOINTS="127.0.0.1:18101",
        AUTHORIZED_OCCUPIED_PORTS="18101",
    )

    completed = _run("preflight", "host", environment=environment)

    assert completed.returncode != 0
    assert "127.0.0.2:18101" in completed.stderr
    assert "unauthorized" in completed.stderr


def test_preflight_fails_closed_when_the_host_socket_parser_exits_nonzero(
    fake_bin: Path, tmp_path: Path
) -> None:
    project_root = tmp_path / "platform"
    scripts = project_root / "deploy/scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(SCRIPTS / "preflight", scripts / "preflight")
    shutil.copy2(SCRIPTS / "preflight_checks.py", scripts / "preflight_checks.py")
    shutil.copy2(
        SCRIPTS / "deployment_contracts.py",
        scripts / "deployment_contracts.py",
    )
    shutil.copy2(SCRIPTS / "operator_topology.py", scripts / "operator_topology.py")
    shutil.copy2(
        PLATFORM_ROOT / "deploy/operator-topology.json",
        project_root / "deploy/operator-topology.json",
    )
    shutil.copy2(
        PLATFORM_ROOT / "deploy/docker-compose.operators.yml",
        project_root / "deploy/docker-compose.operators.yml",
    )
    shutil.copytree(
        PLATFORM_ROOT / "deploy/config/operators",
        project_root / "deploy/config/operators",
    )
    parser_called = tmp_path / "host-sockets.called"
    venv_bin = project_root / ".venv/bin"
    venv_bin.mkdir(parents=True)
    _write_executable(
        venv_bin / "python",
        """#!/usr/bin/env bash
if [[ "${2:-}" == "host-sockets" ]]; then
  printf called >"$PARSER_CALLED"
  exit 77
fi
exec "$REAL_PYTHON" "$@"
""",
    )
    _write_executable(
        fake_bin / "awk",
        """#!/usr/bin/env bash
if [[ "$*" == *'$4 ~'* ]]; then
  exit 77
fi
exec /usr/bin/awk "$@"
""",
    )
    course, result = _separate_shared_roots(tmp_path)
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
        REQUIRED_PORTS="",
        SS_OUTPUT="LISTEN 0 128 127.0.0.2:18101 0.0.0.0:*\n",
        AUTHORIZED_OCCUPIED_ENDPOINTS="127.0.0.1:18101",
        PARSER_CALLED=str(parser_called),
        REAL_PYTHON=sys.executable,
    )

    completed = subprocess.run(
        [str(scripts / "preflight"), "host"],
        cwd=project_root,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert parser_called.read_text(encoding="utf-8") == "called"


@pytest.mark.parametrize("mutation", ["missing", "wrong-source", "read-only"])
@pytest.mark.parametrize("target", ["/data/course", "/data/result"])
@pytest.mark.parametrize(
    "service_name",
    ["control-service", "orchestrator-service", "vision-orchestrator-service"],
)
def test_preflight_requires_each_shared_platform_service_mount(
    fake_bin: Path,
    tmp_path: Path,
    service_name: str,
    target: str,
    mutation: str,
) -> None:
    course = tmp_path / "course"
    result = tmp_path / "result"
    course.mkdir()
    result.mkdir()
    document = _platform_compose_config()
    volumes = document["services"][service_name]["volumes"]
    if mutation == "missing":
        document["services"][service_name]["volumes"] = [
            mount for mount in volumes if mount["target"] != target
        ]
    else:
        mount = next(mount for mount in volumes if mount["target"] == target)
        if mutation == "wrong-source":
            mount["source"] = "/wrong/shared-root"
        else:
            mount["read_only"] = True
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
        PLATFORM_COMPOSE_CONFIG=json.dumps(document),
    )

    completed = _run("preflight", "host", environment=environment)

    assert completed.returncode != 0
    assert service_name in completed.stderr
    assert target in completed.stderr


def test_preflight_rejects_duplicate_published_ports_across_compose_documents(
    fake_bin: Path, tmp_path: Path
) -> None:
    course, result = _separate_shared_roots(tmp_path)
    document = _operator_compose_config()
    document["services"]["asr-offline-gpu0"]["ports"][0]["published"] = "18100"
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
        OPERATOR_COMPOSE_CONFIG=json.dumps(document),
    )

    completed = _run("preflight", "host", environment=environment)

    assert completed.returncode != 0
    assert "duplicate published port" in completed.stderr
    assert "18100" in completed.stderr


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
    course, result = _separate_shared_roots(tmp_path)
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
        REQUIRED_PORTS="",
        **overrides,
    )

    completed = _run("preflight", environment=environment)

    assert completed.returncode != 0
    assert message in completed.stderr


def test_preflight_rejects_an_unauthorized_required_port_occupant(
    fake_bin: Path, tmp_path: Path
) -> None:
    course, result = _separate_shared_roots(tmp_path)
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
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
    course, result = _separate_shared_roots(tmp_path)
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
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
    course, result = _separate_shared_roots(tmp_path)
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
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
    course, result = _separate_shared_roots(tmp_path)
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
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
    assert "REQUIRED_PORTS" not in source
    assert "os.open" in source
    assert "O_EXCL" in source
    assert "os.fsync" in source
    assert "os.unlink" in source


def test_preflight_runtime_checks_readiness_schema_indexes_and_topics_read_only(
    fake_bin: Path, readiness_server: Any
) -> None:
    base_url, _ = readiness_server
    environment = _base_environment(
        fake_bin,
        CONTROL_READINESS_URL=f"{base_url}/control",
        ORCHESTRATOR_READINESS_URL=f"{base_url}/orchestrator",
    )

    completed = _run("preflight", "runtime", environment=environment)

    assert completed.returncode == 0, completed.stderr
    assert "preflight runtime: PASS" in completed.stdout
    commands = _commands(environment)
    platform_services = {
        "control-service",
        "orchestrator-service",
        "vision-orchestrator-service",
        "online-gateway-service",
    }
    ps_command = next(command for command in commands if "ps" in command)
    assert set(ps_command[ps_command.index("-q") + 1 :]) == platform_services
    container_inspect = next(command for command in commands if command[1] == "inspect")
    platform_ids = json.loads(environment["PLATFORM_SERVICE_IDS"])
    assert set(container_inspect[2:]) == set(platform_ids.values())
    image_inspect = next(command for command in commands if command[1:3] == ["image", "inspect"])
    platform_inspections = json.loads(environment["PLATFORM_INSPECT_FIXTURES"])
    assert set(image_inspect[3:]) == {
        record["Image"] for record in platform_inspections.values()
    }
    psql_commands = [command for command in commands if "psql" in command]
    assert len(psql_commands) == 3
    assert all("SELECT" in command[-1] for command in psql_commands)
    assert all(
        "PGOPTIONS=-c default_transaction_read_only=on" in command
        for command in psql_commands
    )
    kafka_commands = [
        command
        for command in commands
        if any(argument.endswith("/kafka-topics.sh") for argument in command)
    ]
    assert len(kafka_commands) == 1
    assert "--describe" in kafka_commands[0]
    assert commands.index(image_inspect) < commands.index(psql_commands[0])
    serialized = "\n".join(" ".join(command) for command in commands).lower()
    for forbidden in (" create ", " alter ", " drop ", "--create"):
        assert forbidden not in f" {serialized} "


@pytest.mark.parametrize(
    ("label_mutation", "expected_message"),
    [("missing", "missing"), ("mismatch", "does not match")],
)
def test_preflight_runtime_rejects_platform_revision_before_readiness_and_catalogs(
    fake_bin: Path,
    readiness_server: Any,
    label_mutation: str,
    expected_message: str,
) -> None:
    base_url, state = readiness_server
    state["/control"] = (503, {"status": "not_ready"})
    environment = _base_environment(
        fake_bin,
        CONTROL_READINESS_URL=f"{base_url}/control",
        ORCHESTRATOR_READINESS_URL=f"{base_url}/orchestrator",
    )
    images = json.loads(environment["IMAGE_INSPECT_FIXTURES"])
    platform_records = json.loads(environment["PLATFORM_INSPECT_FIXTURES"])
    image_id = next(iter(platform_records.values()))["Image"]
    labels = images[image_id]["Config"]["Labels"]
    if label_mutation == "missing":
        labels.pop("org.opencontainers.image.revision")
    else:
        labels["org.opencontainers.image.revision"] = "b" * 40
    environment["IMAGE_INSPECT_FIXTURES"] = json.dumps(images)

    completed = _run("preflight", "runtime", environment=environment)

    assert completed.returncode != 0
    assert "revision" in completed.stderr.lower()
    assert expected_message in completed.stderr
    assert not any("psql" in command for command in _commands(environment))


@pytest.mark.parametrize("git_sha", [None, "short", "g" * 40])
def test_preflight_runtime_requires_a_full_revision_before_docker_inspection(
    fake_bin: Path,
    git_sha: str | None,
) -> None:
    environment = _base_environment(fake_bin)
    environment.pop("EXPECTED_GIT_SHA")
    arguments: tuple[str, ...] = ("runtime",)
    if git_sha is not None:
        arguments = ("runtime", "--git-sha", git_sha)

    completed = _run("preflight", *arguments, environment=environment)

    assert completed.returncode != 0
    assert "Git SHA" in completed.stderr
    assert not any(command[1] == "inspect" for command in _commands(environment))


def test_preflight_runtime_prefers_and_normalizes_explicit_revision(
    fake_bin: Path, readiness_server: Any
) -> None:
    base_url, _ = readiness_server
    environment = _base_environment(
        fake_bin,
        EXPECTED_GIT_SHA="invalid-environment-value",
        CONTROL_READINESS_URL=f"{base_url}/control",
        ORCHESTRATOR_READINESS_URL=f"{base_url}/orchestrator",
    )

    completed = _run(
        "preflight", "runtime", "--git-sha", "A" * 40, environment=environment
    )

    assert completed.returncode == 0, completed.stderr


def test_preflight_runtime_does_not_fallback_when_explicit_revision_is_empty(
    fake_bin: Path,
) -> None:
    environment = _base_environment(fake_bin, EXPECTED_GIT_SHA="a" * 40)

    completed = _run("preflight", "runtime", "--git-sha=", environment=environment)

    assert completed.returncode != 0
    assert "Git SHA" in completed.stderr
    assert not any(command[1] == "inspect" for command in _commands(environment))


@pytest.mark.parametrize("service", ["control", "orchestrator"])
def test_preflight_runtime_rejects_unready_required_services_before_catalog_queries(
    fake_bin: Path, readiness_server: Any, service: str
) -> None:
    base_url, state = readiness_server
    state[f"/{service}"] = (503, {"status": "not_ready"})
    environment = _base_environment(
        fake_bin,
        CONTROL_READINESS_URL=f"{base_url}/control",
        ORCHESTRATOR_READINESS_URL=f"{base_url}/orchestrator",
    )

    completed = _run("preflight", "runtime", environment=environment)

    assert completed.returncode != 0
    assert "readiness" in completed.stderr
    assert not any("psql" in command for command in _commands(environment))


def test_preflight_runtime_excludes_migration_ledger_from_business_catalog(
    fake_bin: Path, readiness_server: Any
) -> None:
    base_url, _ = readiness_server
    environment = _base_environment(
        fake_bin,
        CONTROL_READINESS_URL=f"{base_url}/control",
        ORCHESTRATOR_READINESS_URL=f"{base_url}/orchestrator",
    )

    completed = _run("preflight", "runtime", environment=environment)

    assert completed.returncode == 0, completed.stderr
    psql_commands = [command for command in _commands(environment) if "psql" in command]
    assert len(psql_commands) == 3
    assert all(
        "algorithm_schema_migrations" in " ".join(command)
        for command in psql_commands
    )


@pytest.mark.parametrize(
    ("environment_key", "rows", "message"),
    [
        ("DB_TABLES_OUTPUT", _database_table_rows()[1:], "table"),
        (
            "DB_TABLES_OUTPUT",
            [(table, "English only") for table in EXPECTED_DATABASE_COLUMNS],
            "Chinese comment",
        ),
        ("DB_COLUMNS_OUTPUT", _database_column_rows()[1:], "column"),
        (
            "DB_COLUMNS_OUTPUT",
            [
                (table, column, "English only")
                for table, columns in EXPECTED_DATABASE_COLUMNS.items()
                for column in columns
            ],
            "Chinese comment",
        ),
        ("DB_INDEXES_OUTPUT", _database_index_rows()[1:], "index"),
    ],
    ids=[
        "missing-table",
        "table-comment",
        "missing-column",
        "column-comment",
        "missing-index",
    ],
)
def test_preflight_runtime_rejects_incomplete_database_catalog(
    fake_bin: Path,
    readiness_server: Any,
    environment_key: str,
    rows: list[tuple[str, ...]],
    message: str,
) -> None:
    base_url, _ = readiness_server
    environment = _base_environment(
        fake_bin,
        CONTROL_READINESS_URL=f"{base_url}/control",
        ORCHESTRATOR_READINESS_URL=f"{base_url}/orchestrator",
        **{environment_key: _csv_text(rows)},
    )

    completed = _run("preflight", "runtime", environment=environment)

    assert completed.returncode != 0
    assert message in completed.stderr


@pytest.mark.parametrize(
    ("topic_output", "message"),
    [
        (
            _kafka_topic_output(
                topics=("algorithm.course.commands", "algorithm.visual.commands")
            ),
            "topic",
        ),
        (_kafka_topic_output(partitions=2), "partition"),
        (_kafka_topic_output(replicas=2), "replication"),
    ],
    ids=["missing-topic", "partitions", "replicas"],
)
def test_preflight_runtime_rejects_invalid_kafka_topic_metadata(
    fake_bin: Path,
    readiness_server: Any,
    topic_output: str,
    message: str,
) -> None:
    base_url, _ = readiness_server
    environment = _base_environment(
        fake_bin,
        CONTROL_READINESS_URL=f"{base_url}/control",
        ORCHESTRATOR_READINESS_URL=f"{base_url}/orchestrator",
        KAFKA_TOPICS_OUTPUT=topic_output,
    )

    completed = _run("preflight", "runtime", environment=environment)

    assert completed.returncode != 0
    assert message in completed.stderr


def test_preflight_runtime_rejects_noncanonical_topics_even_when_broker_matches(
    fake_bin: Path, tmp_path: Path, readiness_server: Any
) -> None:
    config = tmp_path / "orchestrator.toml"
    config.write_text(
        """[kafka]
course_command_topic = "custom.course.commands"
visual_command_topic = "custom.visual.commands"
visual_event_topic = "custom.visual.events"
topic_partitions = 1
topic_replication_factor = 1
""",
        encoding="utf-8",
    )
    base_url, _ = readiness_server
    environment = _base_environment(
        fake_bin,
        CONTROL_READINESS_URL=f"{base_url}/control",
        ORCHESTRATOR_READINESS_URL=f"{base_url}/orchestrator",
        ORCHESTRATOR_CONFIG_PATH=str(config),
        KAFKA_TOPICS_OUTPUT=_kafka_topic_output(
            topics=(
                "custom.course.commands",
                "custom.visual.commands",
                "custom.visual.events",
            )
        ),
    )

    completed = _run("preflight", "runtime", environment=environment)

    assert completed.returncode != 0
    assert "canonical" in completed.stderr


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"PSQL_EXIT": "1"}, "PostgreSQL"),
        ({"KAFKA_TOPICS_EXIT": "1"}, "Kafka"),
    ],
)
def test_preflight_runtime_rejects_failed_read_only_inspection_commands(
    fake_bin: Path,
    readiness_server: Any,
    overrides: dict[str, str],
    message: str,
) -> None:
    base_url, _ = readiness_server
    environment = _base_environment(
        fake_bin,
        CONTROL_READINESS_URL=f"{base_url}/control",
        ORCHESTRATOR_READINESS_URL=f"{base_url}/orchestrator",
        **overrides,
    )

    completed = _run("preflight", "runtime", environment=environment)

    assert completed.returncode != 0
    assert message in completed.stderr


def _operator_preflight_arguments(
    tmp_path: Path, control_url: str, *selection: str, timeout: str = "1"
) -> tuple[str, ...]:
    return (
        "operators",
        *selection,
        "--control-url",
        control_url,
        "--release-tag",
        "v1.0_260812",
        "--git-sha",
        "a" * 40,
        "--reports-root",
        str(tmp_path / "reports"),
        "--timeout-seconds",
        timeout,
        "--poll-seconds",
        "0.01",
        "--request-timeout-seconds",
        "0.2",
    )


def _run_operator_helper_with_roots(
    tmp_path: Path,
    command: str,
    *,
    course_root: Path,
    result_root: Path,
) -> subprocess.CompletedProcess[str]:
    document = _operator_compose_config()
    for service in document["services"].values():
        for mount in service["volumes"]:
            if mount["target"] == "/data/course":
                mount["source"] = str(course_root)
            elif mount["target"] == "/data/result":
                mount["source"] = str(result_root)
    operator_json = tmp_path / f"{command}.operators.json"
    operator_json.write_text(json.dumps(document), encoding="utf-8")
    arguments = [
        sys.executable,
        str(SCRIPTS / "preflight_checks.py"),
        command,
        "--course-root",
        str(course_root),
        "--result-root",
        str(result_root),
        "--profile",
        "gpu0",
    ]
    if command == "operator-runtime":
        service_ids, inspections = _operator_runtime_fixtures()
        selected = [
            service_name
            for service_name, service in document["services"].items()
            if "gpu0" in service["profiles"]
        ]
        canonical_sources = {
            "/data/course": str(course_root.resolve(strict=False)),
            "/data/result": str(result_root.resolve(strict=False)),
        }
        for service_name in selected:
            for mount in inspections[service_ids[service_name]]["Mounts"]:
                mount["Source"] = canonical_sources[mount["Destination"]]
        inspection_json = tmp_path / "operator-runtime.inspections.json"
        selected_inspections = {
            service_ids[name]: inspections[service_ids[name]] for name in selected
        }
        inspection_json.write_text(
            json.dumps(list(selected_inspections.values())), encoding="utf-8"
        )
        image_json = tmp_path / "operator-runtime.images.json"
        image_json.write_text(
            json.dumps(list(_image_inspection_fixtures(selected_inspections).values())),
            encoding="utf-8",
        )
        arguments.extend(
            [
                "--git-sha",
                "a" * 40,
                "--image-inspection-json",
                str(image_json),
                str(operator_json),
                str(inspection_json),
            ]
        )
    else:
        arguments.append(str(operator_json))
    return subprocess.run(
        arguments,
        cwd=PLATFORM_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_preflight_passes_revision_evidence_to_operator_runtime() -> None:
    source = (SCRIPTS / "preflight").read_text(encoding="utf-8")
    operator_runtime_call = source.split(
        '"$preflight_python" "$preflight_checks" operator-runtime', 1
    )[1].split("printf 'preflight: OK: selected running", 1)[0]

    assert '--git-sha "$expected_git_sha"' in operator_runtime_call
    assert "--image-inspection-json" in operator_runtime_call


def test_operator_runtime_helper_requires_revision_evidence(tmp_path: Path) -> None:
    operator_json = tmp_path / "operators.json"
    inspection_json = tmp_path / "inspections.json"
    operator_json.write_text(json.dumps(_operator_compose_config()), encoding="utf-8")
    inspection_json.write_text("[]", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "preflight_checks.py"),
            "operator-runtime",
            "--course-root",
            str(tmp_path),
            "--result-root",
            str(tmp_path),
            str(operator_json),
            str(inspection_json),
        ],
        cwd=PLATFORM_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "--git-sha" in completed.stderr
    assert "--image-inspection-json" in completed.stderr


@pytest.mark.parametrize("command", ["operator-selection", "operator-runtime"])
@pytest.mark.parametrize(
    "relationship",
    [
        "same",
        "course-contains-result",
        "result-contains-course",
        "missing-course",
        "missing-result",
    ],
)
def test_operator_helpers_reject_invalid_shared_root_identity(
    tmp_path: Path, command: str, relationship: str
) -> None:
    if relationship == "same":
        course = result = tmp_path / "shared"
        course.mkdir()
    elif relationship == "course-contains-result":
        course = tmp_path / "course"
        result = course / "result"
        result.mkdir(parents=True)
    elif relationship == "result-contains-course":
        result = tmp_path / "result"
        course = result / "course"
        course.mkdir(parents=True)
    else:
        course = tmp_path / "course"
        result = tmp_path / "result"
        existing = result if relationship == "missing-course" else course
        existing.mkdir()

    completed = _run_operator_helper_with_roots(
        tmp_path,
        command,
        course_root=course,
        result_root=result,
    )

    assert completed.returncode != 0
    message = "real directories" if relationship.startswith("missing") else "overlap"
    assert message in completed.stderr


def test_preflight_operators_full_checks_running_topology_and_registration(
    fake_bin: Path, tmp_path: Path, readiness_server: Any
) -> None:
    base_url, state = readiness_server
    instances = _registered_operator_instances()
    state.update(_registration_responses(instances))
    course, result = _separate_shared_roots(tmp_path)
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
    )

    completed = _run(
        "preflight",
        *_operator_preflight_arguments(tmp_path, base_url, "--full"),
        environment=environment,
    )

    assert completed.returncode == 0, completed.stderr
    assert "preflight operators: PASS" in completed.stdout
    assert "verify-gpu-instance" in completed.stdout
    assert "smoke" in completed.stdout
    commands = _commands(environment)
    ps_command = next(command for command in commands if "ps" in command)
    assert "--no-trunc" in ps_command
    assert len(ps_command[ps_command.index("-q") + 1 :]) == 21
    inspect_command = next(command for command in commands if command[1] == "inspect")
    assert len(inspect_command[2:]) == 21
    image_command = next(
        command for command in commands if command[1:3] == ["image", "inspect"]
    )
    assert len(image_command[3:]) == 7
    exec_commands = [command for command in commands if command[1:2] == ["exec"]]
    assert len(exec_commands) == 21
    assert {command[2] for command in exec_commands} == set(
        _operator_runtime_fixtures()[0].values()
    )
    assert not any(
        action in command[1:]
        for command in commands
        for action in ("up", "start", "run")
    )


@pytest.mark.parametrize(
    "checkpoint_arguments",
    (
        ("--evidence-checkpoint", "stage45-post-recovery"),
        ("--evidence-checkpoint=stage45-post-recovery",),
    ),
)
def test_preflight_operators_forwards_stage45_registration_checkpoint(
    fake_bin: Path,
    tmp_path: Path,
    readiness_server: Any,
    checkpoint_arguments: tuple[str, ...],
) -> None:
    base_url, state = readiness_server
    state.update(_registration_responses(_registered_operator_instances()))
    course, result = _separate_shared_roots(tmp_path)
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
    )

    completed = _run(
        "preflight",
        *_operator_preflight_arguments(
            tmp_path,
            base_url,
            "--full",
            *checkpoint_arguments,
        ),
        environment=environment,
    )

    assert completed.returncode == 0, completed.stderr
    output = (
        tmp_path
        / "reports"
        / "milestone-2b"
        / "releases"
        / "v1.0_260812"
        / ("a" * 40)
        / "registration"
        / "operator-registration-stage45-post-recovery.json"
    )
    assert json.loads(output.read_text(encoding="utf-8"))[
        "evidence_checkpoint"
    ] == "stage45-post-recovery"


@pytest.mark.parametrize(
    "selection",
    (
        ("--evidence-checkpoint", "stage45-post-recovery"),
        ("--full", "--evidence-checkpoint"),
        ("--full", "--evidence-checkpoint="),
        ("--full", "--evidence-check", "stage45-post-recovery"),
        ("--full", "--evidence-checkpoint", "unknown"),
        (
            "--profile",
            "gpu0",
            "--evidence-checkpoint",
            "stage45-post-recovery",
        ),
        (
            "--full",
            "--evidence-checkpoint",
            "stage45-post-recovery",
            "--evidence-checkpoint",
            "stage45-post-recovery",
        ),
    ),
)
def test_preflight_operators_rejects_invalid_checkpoint_before_inspection(
    fake_bin: Path,
    tmp_path: Path,
    readiness_server: Any,
    selection: tuple[str, ...],
) -> None:
    base_url, _ = readiness_server
    environment = _base_environment(fake_bin)

    completed = _run(
        "preflight",
        *_operator_preflight_arguments(
            tmp_path,
            base_url,
            *selection,
            timeout="0.05",
        ),
        environment=environment,
    )

    assert completed.returncode != 0
    assert not _commands(environment)
    assert not list((tmp_path / "reports").rglob("operator-registration*.json"))


@pytest.mark.parametrize(
    ("override", "reason"),
    (
        ({"OPERATOR_PACKAGE_PROBE_VERSION": "0.1.0"}, "0.1.0"),
        (
            {"OPERATOR_PACKAGE_PROBE_FORBIDDEN": '["REQUIRE_GPU"]'},
            "REQUIRE_GPU",
        ),
        ({"OPERATOR_PACKAGE_PROBE_EXIT": "1"}, "probe failed"),
    ),
)
def test_preflight_operators_rejects_invalid_runtime_package_probe(
    fake_bin: Path,
    tmp_path: Path,
    readiness_server: Any,
    override: dict[str, str],
    reason: str,
) -> None:
    base_url, state = readiness_server
    state.update(_registration_responses(_registered_operator_instances()))
    course, result = _separate_shared_roots(tmp_path)
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
        **override,
    )

    completed = _run(
        "preflight",
        *_operator_preflight_arguments(tmp_path, base_url, "--profile", "gpu0"),
        environment=environment,
    )

    assert completed.returncode != 0
    assert reason in completed.stderr


def test_preflight_operators_profile_checks_only_selected_running_containers(
    fake_bin: Path, tmp_path: Path, readiness_server: Any
) -> None:
    base_url, state = readiness_server
    instances = [
        instance
        for instance in _registered_operator_instances()
        if instance["instance_id"].endswith("gpu0")
    ]
    state.update(_registration_responses(instances))
    course, result = _separate_shared_roots(tmp_path)
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
    )

    completed = _run(
        "preflight",
        *_operator_preflight_arguments(tmp_path, base_url, "--profile", "gpu0"),
        environment=environment,
    )

    assert completed.returncode == 0, completed.stderr
    ps_command = next(command for command in _commands(environment) if "ps" in command)
    selected = ps_command[ps_command.index("-q") + 1 :]
    assert len(selected) == 6
    assert all(service.endswith("gpu0") for service in selected)
    image_command = next(
        command
        for command in _commands(environment)
        if command[1:3] == ["image", "inspect"]
    )
    assert len(image_command[3:]) == 6
    report = (
        tmp_path
        / "reports"
        / "milestone-2b"
        / "releases"
        / "v1.0_260812"
        / ("a" * 40)
        / "registration"
        / "operator-registration-profile-gpu0.json"
    )
    assert json.loads(report.read_text(encoding="utf-8"))["summary"] == {
        "expected": 6,
        "observed": 6,
        "valid": 6,
    }


def test_preflight_operators_cpu_profile_verifies_three_instances_and_one_image(
    fake_bin: Path, tmp_path: Path, readiness_server: Any
) -> None:
    base_url, state = readiness_server
    instances = [
        instance
        for instance in _registered_operator_instances()
        if "-cpu" in instance["instance_id"]
    ]
    state.update(_registration_responses(instances))
    environment = _base_environment(fake_bin)

    completed = _run(
        "preflight",
        *_operator_preflight_arguments(tmp_path, base_url, "--profile", "cpu"),
        environment=environment,
    )

    assert completed.returncode == 0, completed.stderr
    commands = _commands(environment)
    ps_command = next(command for command in commands if "ps" in command)
    assert len(ps_command[ps_command.index("-q") + 1 :]) == 3
    image_command = next(
        command for command in commands if command[1:3] == ["image", "inspect"]
    )
    assert len(image_command[3:]) == 1


def test_preflight_operators_ignores_wrong_revision_on_unselected_profile(
    fake_bin: Path, tmp_path: Path, readiness_server: Any
) -> None:
    base_url, state = readiness_server
    instances = [
        instance
        for instance in _registered_operator_instances()
        if instance["instance_id"].endswith("gpu0")
    ]
    state.update(_registration_responses(instances))
    environment = _base_environment(fake_bin)
    operator_records = json.loads(environment["OPERATOR_INSPECT_FIXTURES"])
    images = json.loads(environment["IMAGE_INSPECT_FIXTURES"])
    unselected = next(
        record
        for record in operator_records.values()
        if record["Config"]["Labels"]["com.docker.compose.service"] == "ppt-slice-cpu0"
    )
    images[unselected["Image"]]["Config"]["Labels"][
        "org.opencontainers.image.revision"
    ] = "b" * 40
    environment["IMAGE_INSPECT_FIXTURES"] = json.dumps(images)

    completed = _run(
        "preflight",
        *_operator_preflight_arguments(tmp_path, base_url, "--profile", "gpu0"),
        environment=environment,
    )

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("label_mutation", ["missing", "mismatch"])
def test_preflight_operators_rejects_selected_revision_before_registration_http(
    fake_bin: Path,
    tmp_path: Path,
    readiness_server: Any,
    label_mutation: str,
) -> None:
    base_url, _ = readiness_server
    environment = _base_environment(fake_bin)
    service_ids = json.loads(environment["OPERATOR_SERVICE_IDS"])
    records = json.loads(environment["OPERATOR_INSPECT_FIXTURES"])
    images = json.loads(environment["IMAGE_INSPECT_FIXTURES"])
    image_id = records[service_ids["asr-offline-gpu0"]]["Image"]
    labels = images[image_id]["Config"]["Labels"]
    if label_mutation == "missing":
        labels.pop("org.opencontainers.image.revision")
    else:
        labels["org.opencontainers.image.revision"] = "b" * 40
    environment["IMAGE_INSPECT_FIXTURES"] = json.dumps(images)

    completed = _run(
        "preflight",
        *_operator_preflight_arguments(
            tmp_path, base_url, "--profile", "gpu0", timeout="0.05"
        ),
        environment=environment,
    )

    assert completed.returncode != 0
    assert "revision" in completed.stderr.lower()
    assert not list((tmp_path / "reports").rglob("operator-registration-*.json"))


@pytest.mark.parametrize("git_sha", [None, "short", "g" * 40])
def test_preflight_operators_requires_a_full_revision_before_container_inspection(
    fake_bin: Path,
    tmp_path: Path,
    readiness_server: Any,
    git_sha: str | None,
) -> None:
    base_url, _ = readiness_server
    arguments = list(
        _operator_preflight_arguments(tmp_path, base_url, "--profile", "gpu0")
    )
    index = arguments.index("--git-sha")
    del arguments[index : index + 2]
    if git_sha is not None:
        arguments[index:index] = ["--git-sha", git_sha]
    environment = _base_environment(fake_bin)

    completed = _run("preflight", *arguments, environment=environment)

    assert completed.returncode != 0
    assert "Git SHA" in completed.stderr
    assert not any(command[1] == "inspect" for command in _commands(environment))


def test_preflight_operators_accepts_normalized_runtime_mount_sources(
    fake_bin: Path, tmp_path: Path, readiness_server: Any
) -> None:
    base_url, state = readiness_server
    instances = [
        instance
        for instance in _registered_operator_instances()
        if instance["instance_id"].endswith("gpu0")
    ]
    state.update(_registration_responses(instances))
    storage = tmp_path / "storage"
    canonical_course = storage / "course"
    canonical_result = storage / "result"
    canonical_course.mkdir(parents=True)
    canonical_result.mkdir()
    course_alias = tmp_path / "course-alias"
    course_alias.symlink_to(canonical_course, target_is_directory=True)
    result_alias = canonical_result / ".." / "result"
    document = _operator_compose_config()
    for service in document["services"].values():
        for mount in service["volumes"]:
            if mount["target"] == "/data/course":
                mount["source"] = str(course_alias)
            elif mount["target"] == "/data/result":
                mount["source"] = str(result_alias)
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course_alias),
        RESULT_ROOT=str(result_alias),
        OPERATOR_COMPOSE_CONFIG=json.dumps(document),
    )
    service_ids = json.loads(environment["OPERATOR_SERVICE_IDS"])
    inspections = json.loads(environment["OPERATOR_INSPECT_FIXTURES"])
    for instance in instances:
        mounts = inspections[service_ids[instance["instance_id"]]]["Mounts"]
        next(
            mount for mount in mounts if mount["Destination"] == "/data/course"
        )["Source"] = str(canonical_course)
        next(
            mount for mount in mounts if mount["Destination"] == "/data/result"
        )["Source"] = str(canonical_result)
    environment["OPERATOR_INSPECT_FIXTURES"] = json.dumps(inspections)

    completed = _run(
        "preflight",
        *_operator_preflight_arguments(tmp_path, base_url, "--profile", "gpu0"),
        environment=environment,
    )

    assert completed.returncode == 0, completed.stderr


def test_preflight_operators_accepts_samefile_runtime_mount_source_alias(
    fake_bin: Path, tmp_path: Path, readiness_server: Any
) -> None:
    base_url, state = readiness_server
    instances = [
        instance
        for instance in _registered_operator_instances()
        if instance["instance_id"].endswith("gpu0")
    ]
    state.update(_registration_responses(instances))
    course, result = _separate_shared_roots(tmp_path)
    course_alias = course.with_name(course.name.swapcase())
    if not course_alias.exists():
        pytest.skip("filesystem is case-sensitive")
    assert course.samefile(course_alias)
    environment = _base_environment(
        fake_bin,
        COURSE_ROOT=str(course),
        RESULT_ROOT=str(result),
    )
    service_ids = json.loads(environment["OPERATOR_SERVICE_IDS"])
    inspections = json.loads(environment["OPERATOR_INSPECT_FIXTURES"])
    for instance in instances:
        mounts = inspections[service_ids[instance["instance_id"]]]["Mounts"]
        next(
            mount for mount in mounts if mount["Destination"] == "/data/course"
        )["Source"] = str(course_alias)
    environment["OPERATOR_INSPECT_FIXTURES"] = json.dumps(inspections)

    completed = _run(
        "preflight",
        *_operator_preflight_arguments(tmp_path, base_url, "--profile", "gpu0"),
        environment=environment,
    )

    assert completed.returncode == 0, completed.stderr


def test_preflight_operators_normalizes_list_environment_and_allows_system_extras(
    fake_bin: Path, tmp_path: Path, readiness_server: Any
) -> None:
    base_url, state = readiness_server
    instances = [
        instance
        for instance in _registered_operator_instances()
        if instance["instance_id"].endswith("gpu0")
    ]
    state.update(_registration_responses(instances))
    document = _operator_compose_config()
    service = document["services"]["asr-offline-gpu0"]
    service["environment"] = [
        f"{key}={value}" for key, value in service["environment"].items()
    ]
    environment = _base_environment(
        fake_bin,
        OPERATOR_COMPOSE_CONFIG=json.dumps(document),
    )
    service_ids = json.loads(environment["OPERATOR_SERVICE_IDS"])
    inspections = json.loads(environment["OPERATOR_INSPECT_FIXTURES"])
    inspections[service_ids["asr-offline-gpu0"]]["Config"]["Env"].append(
        "PATH=/usr/local/bin:/usr/bin"
    )
    environment["OPERATOR_INSPECT_FIXTURES"] = json.dumps(inspections)

    completed = _run(
        "preflight",
        *_operator_preflight_arguments(tmp_path, base_url, "--profile", "gpu0"),
        environment=environment,
    )

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    "key",
    [
        "PLATFORM_INSTANCE_ID",
        "PLATFORM_SERVICE_URL",
        "OPERATOR_PORT",
        "PORT",
    ],
)
def test_preflight_operators_rejects_compose_declared_environment_drift(
    fake_bin: Path, tmp_path: Path, readiness_server: Any, key: str
) -> None:
    base_url, state = readiness_server
    instances = [
        instance
        for instance in _registered_operator_instances()
        if instance["instance_id"].endswith("gpu0")
    ]
    state.update(_registration_responses(instances))
    environment = _base_environment(fake_bin)
    service_ids = json.loads(environment["OPERATOR_SERVICE_IDS"])
    inspections = json.loads(environment["OPERATOR_INSPECT_FIXTURES"])
    values = inspections[service_ids["asr-offline-gpu0"]]["Config"]["Env"]
    assert any(value.startswith(f"{key}=") for value in values)
    inspections[service_ids["asr-offline-gpu0"]]["Config"]["Env"] = [
        f"{key}=drifted" if value.startswith(f"{key}=") else value for value in values
    ]
    environment["OPERATOR_INSPECT_FIXTURES"] = json.dumps(inspections)

    completed = _run(
        "preflight",
        *_operator_preflight_arguments(tmp_path, base_url, "--profile", "gpu0"),
        environment=environment,
    )

    assert completed.returncode != 0
    expected_detail = "instance ID" if key == "PLATFORM_INSTANCE_ID" else key
    assert expected_detail in completed.stderr


def test_preflight_operators_rejects_an_unknown_profile_before_container_inspection(
    fake_bin: Path, tmp_path: Path, readiness_server: Any
) -> None:
    base_url, _ = readiness_server
    environment = _base_environment(fake_bin)

    completed = _run(
        "preflight",
        *_operator_preflight_arguments(tmp_path, base_url, "--profile", "gpu9"),
        environment=environment,
    )

    assert completed.returncode != 0
    assert "profile" in completed.stderr
    assert not any(command[1] == "inspect" for command in _commands(environment))


@pytest.mark.parametrize(
    "selection",
    [
        ("--profile=gpu0",),
        ("--instance=ocr-gpu0",),
        ("--prof", "gpu0"),
        ("--inst", "ocr-gpu0"),
        ("--expected-com", "rogue-compose.yml"),
    ],
)
def test_preflight_operators_rejects_ambiguous_selection_syntax_before_inspection(
    fake_bin: Path,
    tmp_path: Path,
    readiness_server: Any,
    selection: tuple[str, ...],
) -> None:
    base_url, _ = readiness_server
    environment = _base_environment(fake_bin)

    completed = _run(
        "preflight",
        *_operator_preflight_arguments(
            tmp_path, base_url, *selection, timeout="0.05"
        ),
        environment=environment,
    )

    assert completed.returncode != 0
    assert "full/profile selection" in completed.stderr
    assert not any(command[1] == "inspect" for command in _commands(environment))


def test_preflight_helper_rejects_abbreviated_profile_option(tmp_path: Path) -> None:
    compose_json = tmp_path / "operators.json"
    compose_json.write_text(json.dumps(_operator_compose_config()), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "preflight_checks.py"),
            "operator-selection",
            "--course-root",
            "/data/course",
            "--result-root",
            "/data/result",
            "--prof",
            "gpu0",
            str(compose_json),
        ],
        cwd=PLATFORM_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "unrecognized arguments: --prof" in completed.stderr


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--prof", "gpu0"),
        ("--inst", "ocr-gpu0"),
        ("--expected-com", "rogue-compose.yml"),
    ],
)
def test_registration_verifier_rejects_abbreviated_long_options(
    tmp_path: Path, option: str, value: str
) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "verify_operator_registration.py"),
            "--control-url",
            "http://127.0.0.1:9",
            "--release-tag",
            "v1.0_260812",
            "--git-sha",
            "a" * 40,
            "--reports-root",
            str(tmp_path / "reports"),
            "--timeout-seconds",
            "0",
            option,
            value,
        ],
        cwd=PLATFORM_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert f"unrecognized arguments: {option}" in completed.stderr


def test_preflight_operators_rejects_a_missing_selected_running_container(
    fake_bin: Path, tmp_path: Path, readiness_server: Any
) -> None:
    base_url, _ = readiness_server
    environment = _base_environment(fake_bin)
    service_ids = json.loads(environment["OPERATOR_SERVICE_IDS"])
    del service_ids["asr-offline-gpu0"]
    environment["OPERATOR_SERVICE_IDS"] = json.dumps(service_ids)

    completed = _run(
        "preflight",
        *_operator_preflight_arguments(
            tmp_path, base_url, "--profile", "gpu0", timeout="0.05"
        ),
        environment=environment,
    )

    assert completed.returncode != 0
    assert "running container" in completed.stderr


@pytest.mark.parametrize(
    ("profile", "instance_id", "mutation", "message"),
    [
        ("gpu0", "asr-offline-gpu0", "stopped", "running"),
        ("gpu0", "asr-offline-gpu0", "instance-id", "instance ID"),
        ("gpu0", "asr-offline-gpu0", "gpu-environment", "GPU"),
        ("gpu0", "asr-offline-gpu0", "gpu-reservation", "GPU"),
        ("gpu0", "asr-offline-gpu0", "course-mount", "/data/course"),
        ("gpu0", "asr-offline-gpu0", "result-mount", "/data/result"),
        ("cpu", "ppt-slice-cpu0", "cpu-environment", "CPU"),
        ("cpu", "ppt-slice-cpu0", "cpu-reservation", "CPU"),
    ],
)
def test_preflight_operators_rejects_runtime_drift_from_authoritative_compose(
    fake_bin: Path,
    tmp_path: Path,
    readiness_server: Any,
    profile: str,
    instance_id: str,
    mutation: str,
    message: str,
) -> None:
    base_url, _ = readiness_server
    environment = _base_environment(fake_bin)
    service_ids = json.loads(environment["OPERATOR_SERVICE_IDS"])
    inspections = json.loads(environment["OPERATOR_INSPECT_FIXTURES"])
    record = inspections[service_ids[instance_id]]
    if mutation == "stopped":
        record["State"] = {"Running": False, "Status": "exited"}
    elif mutation == "instance-id":
        record["Config"]["Env"] = [
            "PLATFORM_INSTANCE_ID=wrong" if value.startswith("PLATFORM_INSTANCE_ID=") else value
            for value in record["Config"]["Env"]
        ]
    elif mutation == "gpu-environment":
        record["Config"]["Env"] = [
            "NVIDIA_VISIBLE_DEVICES=1"
            if value.startswith("NVIDIA_VISIBLE_DEVICES=")
            else value
            for value in record["Config"]["Env"]
        ]
    elif mutation == "gpu-reservation":
        record["HostConfig"]["DeviceRequests"][0]["DeviceIDs"] = ["1"]
    elif mutation == "course-mount":
        record["Mounts"][0]["Type"] = "volume"
    elif mutation == "result-mount":
        record["Mounts"][1]["RW"] = False
    elif mutation == "cpu-environment":
        record["Config"]["Env"].append("PLATFORM_GPU_ID=0")
    elif mutation == "cpu-reservation":
        record["HostConfig"]["DeviceRequests"] = [
            {
                "Driver": "nvidia",
                "Count": 0,
                "DeviceIDs": ["0"],
                "Capabilities": [["gpu"]],
            }
        ]
    environment["OPERATOR_INSPECT_FIXTURES"] = json.dumps(inspections)

    completed = _run(
        "preflight",
        *_operator_preflight_arguments(
            tmp_path, base_url, "--profile", profile, timeout="0.05"
        ),
        environment=environment,
    )

    assert completed.returncode != 0
    assert message in completed.stderr


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "published",
        "target",
        "protocol",
        "host-ip-wildcard",
        "host-ip-invalid",
        "mixed-host-ip",
        "extra",
    ],
)
def test_preflight_operators_rejects_runtime_port_binding_drift(
    fake_bin: Path,
    tmp_path: Path,
    readiness_server: Any,
    mutation: str,
) -> None:
    base_url, state = readiness_server
    instances = [
        instance
        for instance in _registered_operator_instances()
        if instance["instance_id"].endswith("gpu0")
    ]
    state.update(_registration_responses(instances))
    environment = _base_environment(fake_bin)
    service_ids = json.loads(environment["OPERATOR_SERVICE_IDS"])
    inspections = json.loads(environment["OPERATOR_INSPECT_FIXTURES"])
    record = inspections[service_ids["asr-offline-gpu0"]]
    bindings = record["HostConfig"]["PortBindings"]
    if mutation == "missing":
        record["HostConfig"]["PortBindings"] = {}
    elif mutation == "published":
        bindings["8083/tcp"][0]["HostPort"] = "65000"
    elif mutation == "target":
        bindings["8084/tcp"] = bindings.pop("8083/tcp")
    elif mutation == "protocol":
        bindings["8083/udp"] = bindings.pop("8083/tcp")
    elif mutation == "host-ip-wildcard":
        bindings["8083/tcp"][0]["HostIp"] = ""
    elif mutation == "host-ip-invalid":
        bindings["8083/tcp"][0]["HostIp"] = "not-an-ip"
    elif mutation == "mixed-host-ip":
        bindings["8083/tcp"].append(
            {"HostIp": "0.0.0.0", "HostPort": "18083"}
        )
    elif mutation == "extra":
        bindings["9000/tcp"] = [
            {"HostIp": "0.0.0.0", "HostPort": "65000"}
        ]
    environment["OPERATOR_INSPECT_FIXTURES"] = json.dumps(inspections)

    completed = _run(
        "preflight",
        *_operator_preflight_arguments(
            tmp_path, base_url, "--profile", "gpu0", timeout="0.2"
        ),
        environment=environment,
    )

    assert completed.returncode != 0
    assert "port binding" in completed.stderr.lower()


def test_preflight_operators_rejects_duplicate_loopback_bindings(
    fake_bin: Path,
    tmp_path: Path,
    readiness_server: Any,
) -> None:
    base_url, state = readiness_server
    instances = [
        instance
        for instance in _registered_operator_instances()
        if instance["instance_id"].endswith("gpu0")
    ]
    state.update(_registration_responses(instances))
    environment = _base_environment(fake_bin)
    service_ids = json.loads(environment["OPERATOR_SERVICE_IDS"])
    inspections = json.loads(environment["OPERATOR_INSPECT_FIXTURES"])
    bindings = inspections[service_ids["asr-offline-gpu0"]]["HostConfig"][
        "PortBindings"
    ]
    bindings["8083/tcp"].append(
        {"HostIp": "127.0.0.1", "HostPort": "18083"}
    )
    environment["OPERATOR_INSPECT_FIXTURES"] = json.dumps(inspections)

    completed = _run(
        "preflight",
        *_operator_preflight_arguments(
            tmp_path, base_url, "--profile", "gpu0", timeout="0.2"
        ),
        environment=environment,
    )

    assert completed.returncode != 0
    assert "port binding" in completed.stderr.lower()


def test_preflight_operators_rejects_omitted_runtime_loopback_host_ip(
    fake_bin: Path,
    tmp_path: Path,
    readiness_server: Any,
) -> None:
    base_url, state = readiness_server
    instances = [
        instance
        for instance in _registered_operator_instances()
        if instance["instance_id"].endswith("gpu0")
    ]
    state.update(_registration_responses(instances))
    environment = _base_environment(fake_bin)
    service_ids = json.loads(environment["OPERATOR_SERVICE_IDS"])
    inspections = json.loads(environment["OPERATOR_INSPECT_FIXTURES"])
    binding = inspections[service_ids["asr-offline-gpu0"]]["HostConfig"][
        "PortBindings"
    ]["8083/tcp"][0]
    del binding["HostIp"]
    environment["OPERATOR_INSPECT_FIXTURES"] = json.dumps(inspections)

    completed = _run(
        "preflight",
        *_operator_preflight_arguments(
            tmp_path, base_url, "--profile", "gpu0", timeout="0.2"
        ),
        environment=environment,
    )

    assert completed.returncode != 0
    assert "port binding" in completed.stderr.lower()


def test_preflight_operators_propagates_registration_or_first_heartbeat_failure(
    fake_bin: Path, tmp_path: Path, readiness_server: Any
) -> None:
    base_url, state = readiness_server
    instances = [
        instance
        for instance in _registered_operator_instances()
        if instance["instance_id"].endswith("gpu0")
    ]
    state["/ops/operator-instances"] = (200, instances)
    for instance in instances:
        state[f"/ops/operator-instances/{instance['instance_id']}/events?limit=100"] = (
            200,
            [{"event_type": "REGISTERED"}],
        )
    environment = _base_environment(fake_bin)

    completed = _run(
        "preflight",
        *_operator_preflight_arguments(
            tmp_path, base_url, "--profile", "gpu0", timeout="0.08"
        ),
        environment=environment,
    )

    assert completed.returncode != 0
    assert "heartbeat" in completed.stderr.lower() or "\u5fc3\u8df3" in completed.stderr


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
