#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import io
import ipaddress
import json
import re
import stat
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, cast


class PreflightError(ValueError):
    pass


GPU_ROW_PATTERN = re.compile(
    r"^\s*(?P<index>[0-9]+)\s*,\s*"
    r"(?P<uuid>GPU-[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})\s*$"
)
CHINESE_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
EXPECTED_DATABASE_COLUMNS = {
    "course_jobs": {"id", "task_id", "input_snapshot", "created_at", "updated_at"},
    "course_task_types": {
        "id",
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
    },
    "task_nodes": {
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
    },
    "node_results": {
        "task_node_id",
        "result",
        "artifact_path",
        "artifact_count",
        "progress",
        "effective_params",
        "result_version",
        "created_at",
        "updated_at",
    },
    "node_work_items": {
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
    },
    "outbox_events": {
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
    },
    "operator_instances": {
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
    },
    "operator_instance_events": {
        "id",
        "instance_id",
        "event_type",
        "event_payload",
        "occurred_at",
    },
    "visual_fallback_values": {
        "id",
        "course_task_type_id",
        "metric_code",
        "value",
        "created_at",
    },
    "task_node_dependencies": {"node_id", "prerequisite_node_id"},
}
EXPECTED_DATABASE_INDEXES = {
    "idx_task_nodes_ready_claim": "task_nodes",
    "idx_course_task_types_task_query": "course_task_types",
    "idx_task_nodes_task_query": "task_nodes",
    "idx_outbox_events_pending_scan": "outbox_events",
    "idx_task_node_dependencies_prerequisite": "task_node_dependencies",
    "idx_operator_instance_events_instance_time": "operator_instance_events",
}
TOPIC_METADATA_PATTERN = re.compile(
    r"Topic:\s*(?P<topic>\S+).*?PartitionCount:\s*(?P<partitions>[0-9]+)"
    r".*?ReplicationFactor:\s*(?P<replication>[0-9]+)"
)
EXPECTED_KAFKA_TOPICS = (
    "algorithm.course.commands",
    "algorithm.visual.commands",
    "algorithm.visual.events",
)
GIT_SHA_PATTERN = re.compile(r"[0-9a-fA-F]{40}")
DOCKER_IMAGE_ID_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
REVISION_LABEL = "org.opencontainers.image.revision"
WILDCARD_HOST = "*"
PortMapping = tuple[int, int, str, str]
EXPECTED_PLATFORM_PORT_MAPPINGS = {
    "postgres": (5432, 5432, "tcp", "127.0.0.1"),
    "redis": (6379, 6379, "tcp", "127.0.0.1"),
    "kafka": (9092, 9092, "tcp", "127.0.0.1"),
    "mongodb": (27017, 27017, "tcp", "127.0.0.1"),
    "control-service": (18100, 18100, "tcp", WILDCARD_HOST),
    "orchestrator-service": (18101, 18101, "tcp", "127.0.0.1"),
    "vision-orchestrator-service": (18102, 8010, "tcp", "127.0.0.1"),
    "online-gateway-service": (18103, 8001, "tcp", WILDCARD_HOST),
}
EXPECTED_OPERATOR_PORT_MAPPINGS = {
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
        for operator, target, suffix in (
            ("ppt-slice", 9001, 9001),
            ("text-analysis", 8000, 8000),
        )
        for index in range(3)
    },
}


def validate_gpu_output(output: str) -> None:
    rows = [
        match.groupdict()
        for line in output.splitlines()
        if (match := GPU_ROW_PATTERN.fullmatch(line))
    ]
    if len(rows) != 3:
        raise PreflightError(
            f"NVIDIA container must expose exactly 3 GPUs; found {len(rows)} valid GPU records"
        )
    indexes = [row["index"] for row in rows]
    uuids = [row["uuid"] for row in rows]
    if len(set(indexes)) != len(indexes) or len(set(uuids)) != len(uuids):
        raise PreflightError("NVIDIA GPU indexes and UUIDs must be unique")


def validate_readiness(urls: list[str], timeout: float) -> None:
    if timeout <= 0:
        raise PreflightError("readiness timeout must be positive")
    for url in urls:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = response.status
                payload = json.load(response)
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            json.JSONDecodeError,
        ) as error:
            raise PreflightError(f"readiness request failed for {url}: {error}") from error
        if status != 200 or not isinstance(payload, dict) or payload.get("status") != "ready":
            raise PreflightError(f"readiness check is not ready for {url}")


def _csv_rows(content: str, width: int, label: str) -> list[tuple[str, ...]]:
    try:
        rows = [tuple(value.strip() for value in row) for row in csv.reader(io.StringIO(content))]
    except csv.Error as error:
        raise PreflightError(f"PostgreSQL {label} catalog CSV is invalid") from error
    if any(len(row) != width for row in rows):
        raise PreflightError(f"PostgreSQL {label} catalog row is invalid")
    return rows


def _has_chinese_comment(comment: str) -> bool:
    return bool(comment.strip()) and CHINESE_PATTERN.search(comment) is not None


def validate_database_catalog(tables_text: str, columns_text: str, indexes_text: str) -> None:
    table_rows = _csv_rows(tables_text, 2, "table")
    table_names = [row[0] for row in table_rows]
    expected_tables = set(EXPECTED_DATABASE_COLUMNS)
    if len(table_names) != len(set(table_names)) or set(table_names) != expected_tables:
        raise PreflightError(
            "PostgreSQL table catalog must contain exactly the 10 formal scheduling tables"
        )
    for table, comment in table_rows:
        if not _has_chinese_comment(comment):
            raise PreflightError(f"PostgreSQL table requires a non-empty Chinese comment: {table}")

    column_rows = _csv_rows(columns_text, 3, "column")
    actual_columns: dict[str, set[str]] = {table: set() for table in expected_tables}
    seen_columns: set[tuple[str, str]] = set()
    for table, column, comment in column_rows:
        key = (table, column)
        if table not in expected_tables or key in seen_columns:
            raise PreflightError(
                f"PostgreSQL column catalog contains an unexpected row: {table}.{column}"
            )
        seen_columns.add(key)
        actual_columns[table].add(column)
        if not _has_chinese_comment(comment):
            raise PreflightError(
                f"PostgreSQL column requires a non-empty Chinese comment: {table}.{column}"
            )
    if actual_columns != EXPECTED_DATABASE_COLUMNS:
        raise PreflightError("PostgreSQL column catalog does not match expected migration fields")

    index_rows = _csv_rows(indexes_text, 2, "index")
    actual_indexes = {index: table for table, index in index_rows}
    if len(actual_indexes) != len(index_rows):
        raise PreflightError("PostgreSQL index catalog contains duplicate names")
    missing_or_wrong = {
        index: table
        for index, table in EXPECTED_DATABASE_INDEXES.items()
        if actual_indexes.get(index) != table
    }
    if missing_or_wrong:
        raise PreflightError(
            "PostgreSQL expected migration index is missing or on the wrong table: "
            f"{sorted(missing_or_wrong)}"
        )


def validate_kafka_topics(output: str, config_path: Path) -> None:
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise PreflightError(f"cannot read orchestrator Kafka config: {config_path}") from error
    kafka = config.get("kafka") if isinstance(config, dict) else None
    if not isinstance(kafka, dict):
        raise PreflightError("orchestrator Kafka config section is missing")
    topic_keys = ("course_command_topic", "visual_command_topic", "visual_event_topic")
    topics = [kafka.get(key) for key in topic_keys]
    partitions = kafka.get("topic_partitions")
    replication = kafka.get("topic_replication_factor")
    if any(not isinstance(topic, str) or not topic for topic in topics):
        raise PreflightError("orchestrator Kafka topic config is invalid")
    if tuple(topics) != EXPECTED_KAFKA_TOPICS:
        raise PreflightError(
            "orchestrator Kafka topics must use the three canonical algorithm topic names"
        )
    if len(set(topics)) != 3:
        raise PreflightError("orchestrator Kafka topics must be unique")
    if partitions != 1 or replication != 1:
        raise PreflightError("orchestrator Kafka partition and replication config must both be 1")

    metadata: dict[str, tuple[int, int]] = {}
    for line in output.splitlines():
        match = TOPIC_METADATA_PATTERN.search(line)
        if match is None:
            continue
        topic = match.group("topic")
        if topic in metadata:
            raise PreflightError(f"Kafka topic metadata is duplicated: {topic}")
        metadata[topic] = (int(match.group("partitions")), int(match.group("replication")))
    for topic in topics:
        if topic not in metadata:
            raise PreflightError(f"Kafka topic is missing: {topic}")
        actual_partitions, actual_replication = metadata[topic]
        if actual_partitions != partitions:
            raise PreflightError(
                f"Kafka topic partition count does not match orchestrator config: {topic}"
            )
        if actual_replication != replication:
            raise PreflightError(
                f"Kafka topic replication factor does not match orchestrator config: {topic}"
            )


def _services(document: Any, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(document, dict) or not isinstance(document.get("services"), dict):
        raise PreflightError(f"{label} Compose JSON must contain a services object")
    services = document["services"]
    if any(
        not isinstance(name, str) or not isinstance(service, dict)
        for name, service in services.items()
    ):
        raise PreflightError(f"{label} Compose services are invalid")
    return cast(dict[str, dict[str, Any]], services)


def _environment(service: dict[str, Any], instance_id: str) -> dict[str, str]:
    environment = service.get("environment", {})
    if isinstance(environment, dict):
        if any(
            not isinstance(key, str) or not isinstance(value, (str, int))
            for key, value in environment.items()
        ):
            raise PreflightError(f"operator environment is invalid: {instance_id}")
        return {key: str(value) for key, value in environment.items()}
    if isinstance(environment, list):
        result: dict[str, str] = {}
        for entry in environment:
            if not isinstance(entry, str):
                raise PreflightError(f"operator environment is invalid: {instance_id}")
            key, separator, value = entry.partition("=")
            if not separator or not key or key in result:
                raise PreflightError(f"operator environment is invalid: {instance_id}")
            result[key] = value
        return result
    raise PreflightError(f"operator environment is invalid: {instance_id}")


def _reservation_devices(service: dict[str, Any], instance_id: str) -> list[dict[str, Any]]:
    deploy = service.get("deploy", {})
    if deploy is None:
        return []
    if not isinstance(deploy, dict):
        raise PreflightError(f"operator deploy section is invalid: {instance_id}")
    resources = deploy.get("resources", {}) or {}
    reservations = resources.get("reservations", {}) or {}
    devices = reservations.get("devices", []) or []
    if (
        not isinstance(resources, dict)
        or not isinstance(reservations, dict)
        or not isinstance(devices, list)
    ):
        raise PreflightError(f"operator GPU reservation is invalid: {instance_id}")
    if any(not isinstance(device, dict) for device in devices):
        raise PreflightError(f"operator GPU reservation is invalid: {instance_id}")
    return devices


def _validate_gpu_service(
    instance_id: str,
    service: dict[str, Any],
    environment: dict[str, str],
    profile: str,
) -> None:
    match = re.fullmatch(r"gpu([0-9]+)", profile)
    if match is None:
        raise PreflightError(f"operator GPU profile is invalid: {instance_id}")
    gpu = match.group(1)
    if (
        environment.get("PLATFORM_GPU_ID") != gpu
        or environment.get("NVIDIA_VISIBLE_DEVICES") != gpu
    ):
        raise PreflightError(f"operator GPU environment does not match profile: {instance_id}")
    devices = _reservation_devices(service, instance_id)
    if len(devices) != 1:
        raise PreflightError(f"operator GPU reservation must contain one device: {instance_id}")
    device = devices[0]
    capabilities = device.get("capabilities", [])
    if (
        device.get("driver") != "nvidia"
        or device.get("device_ids") != [gpu]
        or not isinstance(capabilities, list)
        or "gpu" not in capabilities
    ):
        raise PreflightError(f"operator GPU reservation does not match profile: {instance_id}")


def _validate_cpu_service(
    instance_id: str, service: dict[str, Any], environment: dict[str, str]
) -> None:
    forbidden = {"PLATFORM_GPU_ID", "NVIDIA_VISIBLE_DEVICES"} & environment.keys()
    if forbidden:
        raise PreflightError(f"CPU operator has GPU environment labels: {instance_id}")
    if _reservation_devices(service, instance_id):
        raise PreflightError(f"CPU operator has a GPU reservation: {instance_id}")


def _validate_bind_mount(
    service_name: str,
    service: dict[str, Any],
    *,
    target: str,
    expected_source: str,
) -> None:
    volumes = service.get("volumes", []) or []
    if not isinstance(volumes, list):
        raise PreflightError(f"Compose volumes are invalid: {service_name}")
    mounts = [
        mount for mount in volumes if isinstance(mount, dict) and mount.get("target") == target
    ]
    if len(mounts) != 1:
        raise PreflightError(f"{service_name} must mount {target} exactly once")
    mount = mounts[0]
    source = mount.get("source")
    try:
        source_identity = (
            _directory_identity(Path(source).expanduser().resolve(strict=True))
            if isinstance(source, str)
            else None
        )
        expected_identity = _directory_identity(
            Path(expected_source).expanduser().resolve(strict=True)
        )
    except (OSError, RuntimeError) as error:
        raise PreflightError(f"{service_name} bind mount source is invalid: {target}") from error
    if (
        mount.get("type") != "bind"
        or source_identity != expected_identity
        or mount.get("read_only", False) is not False
    ):
        raise PreflightError(f"{service_name} must use writable host bind mount {target}")


def _port_number(value: Any, label: str, service_name: str) -> int:
    if isinstance(value, bool) or not (
        isinstance(value, int)
        or isinstance(value, str)
        and re.fullmatch(r"[0-9]+", value) is not None
    ):
        raise PreflightError(f"{label} Compose port mapping is invalid: {service_name}")
    result = int(value)
    if not 1 <= result <= 65535:
        raise PreflightError(f"{label} Compose port mapping is invalid: {service_name}")
    return result


def _normalize_host_ip(value: Any, error_message: str) -> str:
    if not isinstance(value, str):
        raise PreflightError(error_message)
    if not value:
        return WILDCARD_HOST
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise PreflightError(error_message) from error
    if getattr(address, "scope_id", None) is not None:
        raise PreflightError(error_message)
    return WILDCARD_HOST if address.is_unspecified else str(address)


def _compose_port_mappings(
    service: dict[str, Any], label: str, service_name: str
) -> list[PortMapping]:
    ports = service.get("ports")
    if not isinstance(ports, list):
        raise PreflightError(f"{label} Compose port mapping is invalid: {service_name}")
    result: list[PortMapping] = []
    for port in ports:
        if not isinstance(port, dict):
            raise PreflightError(f"{label} Compose port mapping is invalid: {service_name}")
        protocol = port.get("protocol")
        if not isinstance(protocol, str):
            raise PreflightError(f"{label} Compose port mapping is invalid: {service_name}")
        host_ip = _normalize_host_ip(
            port["host_ip"] if "host_ip" in port else "",
            f"{label} Compose port mapping is invalid: {service_name}",
        )
        result.append(
            (
                _port_number(port.get("published"), label, service_name),
                _port_number(port.get("target"), label, service_name),
                protocol,
                host_ip,
            )
        )
    return result


def _published_ports(services: dict[str, dict[str, Any]], label: str) -> dict[int, str]:
    result: dict[int, str] = {}
    for service_name, service in services.items():
        for published, _, _, _ in _compose_port_mappings(service, label, service_name):
            if published in result:
                raise PreflightError(
                    f"duplicate published port {published}: "
                    f"{label}/{result[published]} and {label}/{service_name}"
                )
            result[published] = service_name
    return result


def _validate_port_contract(
    services: dict[str, dict[str, Any]],
    label: str,
    expected: dict[str, PortMapping],
) -> None:
    for service_name, expected_mapping in expected.items():
        service = services.get(service_name)
        if service is None:
            raise PreflightError(f"{label} Compose port service is missing: {service_name}")
        if _compose_port_mappings(service, label, service_name) != [expected_mapping]:
            raise PreflightError(
                f"{label} Compose port mapping is not canonical: {service_name}"
            )
    for service_name in sorted(set(services) - set(expected)):
        ports = services[service_name].get("ports")
        if ports not in (None, []):
            raise PreflightError(
                f"{label} Compose port mapping is not canonical: {service_name}"
            )


def _directory_identity(path: Path) -> tuple[int, int]:
    metadata = path.stat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise NotADirectoryError(path)
    return metadata.st_dev, metadata.st_ino


def _validate_shared_roots(course_root: str, result_root: str) -> tuple[str, str]:
    try:
        resolved_course_root = Path(course_root).expanduser().resolve(strict=True)
        resolved_result_root = Path(result_root).expanduser().resolve(strict=True)
        course_identity = _directory_identity(resolved_course_root)
        result_identity = _directory_identity(resolved_result_root)
        course_ancestor_identities = {
            _directory_identity(path)
            for path in (resolved_course_root, *resolved_course_root.parents)
        }
        result_ancestor_identities = {
            _directory_identity(path)
            for path in (resolved_result_root, *resolved_result_root.parents)
        }
    except (OSError, RuntimeError) as error:
        raise PreflightError(
            "course_root and result_root must resolve to real directories"
        ) from error
    if (
        course_identity in result_ancestor_identities
        or result_identity in course_ancestor_identities
    ):
        raise PreflightError("course_root and result_root must not overlap")
    return str(resolved_course_root), str(resolved_result_root)


def _validate_operator_services(
    operator_services: dict[str, dict[str, Any]],
    *,
    course_root: str,
    result_root: str,
) -> None:
    if len(operator_services) != 24:
        raise PreflightError(
            f"operator Compose must define exactly 24 services; found {len(operator_services)}"
        )

    instance_ids: list[str] = []
    for service_name, service in operator_services.items():
        environment = _environment(service, service_name)
        instance_id = environment.get("PLATFORM_INSTANCE_ID")
        if instance_id != service_name:
            raise PreflightError(f"operator instance ID must match service name: {service_name}")
        instance_ids.append(instance_id)
        profiles = service.get("profiles")
        if not isinstance(profiles, list) or len(profiles) != 1 or not isinstance(profiles[0], str):
            raise PreflightError(f"operator profile must contain exactly one value: {service_name}")
        profile = profiles[0]
        if profile == "cpu":
            if re.search(r"-cpu[0-9]+$", service_name) is None:
                raise PreflightError(
                    f"CPU operator service name does not match profile: {service_name}"
                )
            _validate_cpu_service(service_name, service, environment)
        elif profile.startswith("gpu"):
            if profile not in {"gpu0", "gpu1", "gpu2"} or not service_name.endswith(f"-{profile}"):
                raise PreflightError(
                    f"operator GPU service name does not match profile: {service_name}"
                )
            _validate_gpu_service(service_name, service, environment, profile)
        else:
            raise PreflightError(f"operator profile is invalid: {service_name}")
        _validate_bind_mount(
            service_name, service, target="/data/course", expected_source=course_root
        )
        _validate_bind_mount(
            service_name, service, target="/data/result", expected_source=result_root
        )
    if len(set(instance_ids)) != 24:
        raise PreflightError("operator instance IDs must be unique")


def validate_host_compose(
    platform_document: Any,
    operator_document: Any,
    *,
    course_root: str,
    result_root: str,
) -> list[int]:
    course_root, result_root = _validate_shared_roots(course_root, result_root)

    platform_services = _services(platform_document, "platform")
    operator_services = _services(operator_document, "operator")
    _validate_operator_services(operator_services, course_root=course_root, result_root=result_root)

    required_shared_storage_services = {
        "control-service",
        "orchestrator-service",
        "vision-orchestrator-service",
    }
    required_mounts = {
        "/data/course": course_root,
        "/data/result": result_root,
    }
    for service_name in sorted(required_shared_storage_services):
        if service_name not in platform_services:
            raise PreflightError(f"platform Compose service is missing: {service_name}")
        for target, expected_source in required_mounts.items():
            _validate_bind_mount(
                service_name,
                platform_services[service_name],
                target=target,
                expected_source=expected_source,
            )
    for service_name, service in platform_services.items():
        if service_name in required_shared_storage_services:
            continue
        volumes = service.get("volumes", []) or []
        if not isinstance(volumes, list):
            raise PreflightError(f"platform Compose volumes are invalid: {service_name}")
        if any(
            isinstance(mount, dict) and mount.get("target") == "/data/result" for mount in volumes
        ):
            _validate_bind_mount(
                service_name, service, target="/data/result", expected_source=result_root
            )

    platform_ports = _published_ports(platform_services, "platform")
    operator_ports = _published_ports(operator_services, "operator")
    duplicates = sorted(set(platform_ports) & set(operator_ports))
    if duplicates:
        port = duplicates[0]
        raise PreflightError(
            f"duplicate published port {port}: "
            f"platform/{platform_ports[port]} and operator/{operator_ports[port]}"
        )
    _validate_port_contract(
        platform_services, "platform", EXPECTED_PLATFORM_PORT_MAPPINGS
    )
    _validate_port_contract(
        operator_services, "operator", EXPECTED_OPERATOR_PORT_MAPPINGS
    )
    return sorted(set(platform_ports) | set(operator_ports))


def _select_operator_services(
    operator_document: Any,
    *,
    profiles: list[str],
    course_root: str,
    result_root: str,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    services = _services(operator_document, "operator")
    _validate_operator_services(services, course_root=course_root, result_root=result_root)
    _validate_port_contract(services, "operator", EXPECTED_OPERATOR_PORT_MAPPINGS)
    known_profiles = {
        profile
        for service in services.values()
        for profile in service.get("profiles", [])
        if isinstance(profile, str)
    }
    requested = set(profiles)
    unknown = sorted(requested - known_profiles)
    if unknown:
        raise PreflightError(f"unknown operator Compose profile: {unknown}")
    selected = sorted(
        service_name
        for service_name, service in services.items()
        if not requested or requested.intersection(service["profiles"])
    )
    if not selected:
        raise PreflightError("operator selection is empty")
    return services, selected


def select_operator_services(
    operator_document: Any,
    *,
    profiles: list[str],
    course_root: str,
    result_root: str,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    course_root, result_root = _validate_shared_roots(course_root, result_root)
    return _select_operator_services(
        operator_document,
        profiles=profiles,
        course_root=course_root,
        result_root=result_root,
    )


def _actual_environment(record: dict[str, Any], service_name: str) -> dict[str, str]:
    config = record.get("Config")
    values = config.get("Env") if isinstance(config, dict) else None
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise PreflightError(f"running container environment is invalid: {service_name}")
    result: dict[str, str] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key or key in result:
            raise PreflightError(f"running container environment is invalid: {service_name}")
        result[key] = item
    return result


def _actual_device_requests(record: dict[str, Any], service_name: str) -> list[dict[str, Any]]:
    host_config = record.get("HostConfig")
    values = host_config.get("DeviceRequests") if isinstance(host_config, dict) else None
    if values is None:
        return []
    if not isinstance(values, list) or any(not isinstance(value, dict) for value in values):
        raise PreflightError(f"running container GPU requests are invalid: {service_name}")
    return values


def _actual_port_bindings(
    record: dict[str, Any], service_name: str
) -> list[PortMapping]:
    host_config = record.get("HostConfig")
    bindings = host_config.get("PortBindings") if isinstance(host_config, dict) else None
    if not isinstance(bindings, dict):
        raise PreflightError(f"running container port bindings are invalid: {service_name}")
    result: list[PortMapping] = []
    wildcard_bindings: set[PortMapping] = set()
    for container_port, entries in bindings.items():
        if not isinstance(container_port, str):
            raise PreflightError(
                f"running container port bindings are invalid: {service_name}"
            )
        match = re.fullmatch(r"(?P<target>[0-9]+)/(?P<protocol>[a-z0-9]+)", container_port)
        if match is None or not isinstance(entries, list) or not entries:
            raise PreflightError(
                f"running container port bindings are invalid: {service_name}"
            )
        target = _port_number(match.group("target"), "running container", service_name)
        protocol = match.group("protocol")
        for entry in entries:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("HostPort"), str)
            ):
                raise PreflightError(
                    f"running container port bindings are invalid: {service_name}"
                )
            published = _port_number(
                entry["HostPort"], "running container", service_name
            )
            host_ip = _normalize_host_ip(
                entry["HostIp"] if "HostIp" in entry else "",
                f"running container port bindings are invalid: {service_name}",
            )
            mapping = (published, target, protocol, host_ip)
            if host_ip == WILDCARD_HOST:
                if mapping in wildcard_bindings:
                    continue
                wildcard_bindings.add(mapping)
            result.append(mapping)
    return result


def _validate_actual_mount(
    record: dict[str, Any],
    service_name: str,
    *,
    target: str,
    expected_source: str,
) -> None:
    values = record.get("Mounts")
    if not isinstance(values, list):
        raise PreflightError(f"running container mounts are invalid: {service_name}")
    mounts = [
        mount for mount in values if isinstance(mount, dict) and mount.get("Destination") == target
    ]
    if len(mounts) != 1:
        raise PreflightError(f"running container must mount {target} exactly once: {service_name}")
    mount = mounts[0]
    source = mount.get("Source")
    try:
        source_identity = (
            _directory_identity(Path(source).expanduser().resolve(strict=True))
            if isinstance(source, str)
            else None
        )
        expected_identity = _directory_identity(
            Path(expected_source).expanduser().resolve(strict=True)
        )
    except (OSError, RuntimeError) as error:
        raise PreflightError(
            f"running container bind mount source is invalid: {target}: {service_name}"
        ) from error
    if (
        mount.get("Type") != "bind"
        or source_identity != expected_identity
        or mount.get("RW") is not True
    ):
        raise PreflightError(
            f"running container must use writable host bind mount {target}: {service_name}"
        )


def validate_operator_runtime(
    operator_document: Any,
    inspection_document: Any,
    image_inspection_document: Any,
    *,
    profiles: list[str],
    course_root: str,
    result_root: str,
    expected_git_sha: str,
) -> None:
    course_root, result_root = _validate_shared_roots(course_root, result_root)
    services, selected = _select_operator_services(
        operator_document,
        profiles=profiles,
        course_root=course_root,
        result_root=result_root,
    )
    image_ids = runtime_container_image_ids(
        inspection_document,
        expected_services=selected,
    )
    validate_image_revisions(
        image_inspection_document,
        expected_image_ids=image_ids,
        expected_git_sha=expected_git_sha,
    )
    if not isinstance(inspection_document, list) or any(
        not isinstance(record, dict) for record in inspection_document
    ):
        raise PreflightError("docker inspect output must be a JSON array")
    if len(inspection_document) != len(selected):
        raise PreflightError(
            f"selected services require exactly {len(selected)} running containers; "
            f"found {len(inspection_document)}"
        )

    actual: dict[str, dict[str, Any]] = {}
    container_ids: set[str] = set()
    for record in inspection_document:
        container_id = record.get("Id")
        if (
            not isinstance(container_id, str)
            or re.fullmatch(r"[0-9a-f]{64}", container_id) is None
            or container_id in container_ids
        ):
            raise PreflightError("running container IDs must be unique full Docker IDs")
        container_ids.add(container_id)
        state = record.get("State")
        if (
            not isinstance(state, dict)
            or state.get("Running") is not True
            or state.get("Status") != "running"
        ):
            raise PreflightError(f"selected operator container is not running: {container_id}")
        config = record.get("Config")
        labels = config.get("Labels") if isinstance(config, dict) else None
        service_name = (
            labels.get("com.docker.compose.service") if isinstance(labels, dict) else None
        )
        if not isinstance(service_name, str) or service_name in actual:
            raise PreflightError("running container Compose service labels must be unique")
        actual[service_name] = record
    if set(actual) != set(selected):
        raise PreflightError(
            "running container services do not match the selected Compose services"
        )

    for service_name in selected:
        expected_service = services[service_name]
        expected_environment = _environment(expected_service, service_name)
        actual_environment = _actual_environment(actual[service_name], service_name)
        if actual_environment.get("PLATFORM_INSTANCE_ID") != service_name:
            raise PreflightError(f"running container instance ID is wrong: {service_name}")
        expected_bindings = _compose_port_mappings(
            expected_service, "operator", service_name
        )
        actual_bindings = _actual_port_bindings(actual[service_name], service_name)
        if (
            len(actual_bindings) != len(set(actual_bindings))
            or sorted(actual_bindings) != sorted(expected_bindings)
        ):
            raise PreflightError(
                f"running container port bindings do not match Compose: {service_name}"
            )
        profile = expected_service["profiles"][0]
        device_requests = _actual_device_requests(actual[service_name], service_name)
        if profile == "cpu":
            forbidden = {
                "PLATFORM_GPU_ID",
                "NVIDIA_VISIBLE_DEVICES",
            } & actual_environment.keys()
            if forbidden:
                raise PreflightError(f"running CPU container has GPU environment: {service_name}")
            if device_requests:
                raise PreflightError(f"running CPU container has GPU requests: {service_name}")
        else:
            gpu = expected_environment["PLATFORM_GPU_ID"]
            if (
                actual_environment.get("PLATFORM_GPU_ID") != gpu
                or actual_environment.get("NVIDIA_VISIBLE_DEVICES") != gpu
            ):
                raise PreflightError(f"running container GPU environment is wrong: {service_name}")
            if len(device_requests) != 1:
                raise PreflightError(f"running container GPU request is wrong: {service_name}")
            request = device_requests[0]
            capabilities = request.get("Capabilities")
            if (
                request.get("Driver") != "nvidia"
                or request.get("DeviceIDs") != [gpu]
                or not isinstance(capabilities, list)
                or not any(
                    isinstance(capability_set, list) and "gpu" in capability_set
                    for capability_set in capabilities
                )
            ):
                raise PreflightError(f"running container GPU request is wrong: {service_name}")
        mismatched_environment = sorted(
            key
            for key, expected_value in expected_environment.items()
            if actual_environment.get(key) != expected_value
        )
        if mismatched_environment:
            raise PreflightError(
                "running container environment does not match Compose: "
                f"{service_name}: {mismatched_environment[0]}"
            )
        _validate_actual_mount(
            actual[service_name],
            service_name,
            target="/data/course",
            expected_source=course_root,
        )
        _validate_actual_mount(
            actual[service_name],
            service_name,
            target="/data/result",
            expected_source=result_root,
        )


def normalize_git_sha(value: str) -> str:
    if GIT_SHA_PATTERN.fullmatch(value) is None:
        raise PreflightError("Git SHA must be a full 40-character hexadecimal revision")
    return value.lower()


def runtime_container_image_ids(
    inspection_document: Any,
    *,
    expected_services: list[str],
) -> list[str]:
    if not expected_services or len(expected_services) != len(set(expected_services)):
        raise PreflightError("expected runtime Compose services must be unique and non-empty")
    if not isinstance(inspection_document, list) or any(
        not isinstance(record, dict) for record in inspection_document
    ):
        raise PreflightError("docker inspect output must be a JSON array")
    if len(inspection_document) != len(expected_services):
        raise PreflightError(
            f"expected exactly {len(expected_services)} running runtime containers; "
            f"found {len(inspection_document)}"
        )

    actual: dict[str, str] = {}
    container_ids: set[str] = set()
    for record in inspection_document:
        container_id = record.get("Id")
        if (
            not isinstance(container_id, str)
            or re.fullmatch(r"[0-9a-f]{64}", container_id) is None
            or container_id in container_ids
        ):
            raise PreflightError("running container IDs must be unique full Docker IDs")
        container_ids.add(container_id)
        state = record.get("State")
        if (
            not isinstance(state, dict)
            or state.get("Running") is not True
            or state.get("Status") != "running"
        ):
            raise PreflightError(f"runtime container is not running: {container_id}")
        config = record.get("Config")
        labels = config.get("Labels") if isinstance(config, dict) else None
        service_name = (
            labels.get("com.docker.compose.service") if isinstance(labels, dict) else None
        )
        if not isinstance(service_name, str) or service_name in actual:
            raise PreflightError("runtime container Compose service labels must be unique")
        image_id = record.get("Image")
        if (
            not isinstance(image_id, str)
            or DOCKER_IMAGE_ID_PATTERN.fullmatch(image_id) is None
        ):
            raise PreflightError(
                f"runtime container has no immutable Docker image ID: {service_name}"
            )
        actual[service_name] = image_id
    if set(actual) != set(expected_services):
        raise PreflightError(
            "running container services do not match the expected Compose services"
        )
    return list(dict.fromkeys(actual[service] for service in sorted(expected_services)))


def validate_image_revisions(
    inspection_document: Any,
    *,
    expected_image_ids: list[str],
    expected_git_sha: str,
) -> None:
    expected_git_sha = normalize_git_sha(expected_git_sha)
    if (
        not expected_image_ids
        or len(expected_image_ids) != len(set(expected_image_ids))
        or any(
            DOCKER_IMAGE_ID_PATTERN.fullmatch(image_id) is None
            for image_id in expected_image_ids
        )
    ):
        raise PreflightError("expected Docker image IDs must be unique immutable IDs")
    if not isinstance(inspection_document, list) or any(
        not isinstance(record, dict) for record in inspection_document
    ):
        raise PreflightError("docker image inspect output must be a JSON array")
    if len(inspection_document) != len(expected_image_ids):
        raise PreflightError(
            f"expected exactly {len(expected_image_ids)} inspected images; "
            f"found {len(inspection_document)}"
        )

    actual: dict[str, dict[str, Any]] = {}
    for record in inspection_document:
        image_id = record.get("Id")
        if (
            not isinstance(image_id, str)
            or DOCKER_IMAGE_ID_PATTERN.fullmatch(image_id) is None
            or image_id in actual
        ):
            raise PreflightError("inspected Docker image IDs must be unique immutable IDs")
        actual[image_id] = record
    if set(actual) != set(expected_image_ids):
        raise PreflightError("inspected Docker images do not match running container images")

    for image_id in expected_image_ids:
        config = actual[image_id].get("Config")
        labels = config.get("Labels") if isinstance(config, dict) else None
        revision = labels.get(REVISION_LABEL) if isinstance(labels, dict) else None
        if not isinstance(revision, str) or not revision:
            raise PreflightError(f"image revision label is missing: {image_id}")
        if GIT_SHA_PATTERN.fullmatch(revision) is None:
            raise PreflightError(f"image revision label is invalid: {image_id}")
        if revision.lower() != expected_git_sha:
            raise PreflightError(
                f"image revision does not match expected Git SHA: {image_id}"
            )


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PreflightError(f"cannot read rendered Compose JSON: {path}") from error


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("gpus", allow_abbrev=False)
    compose = subparsers.add_parser("host-compose", allow_abbrev=False)
    compose.add_argument("--course-root", required=True)
    compose.add_argument("--result-root", required=True)
    compose.add_argument("platform_json", type=Path)
    compose.add_argument("operator_json", type=Path)
    readiness = subparsers.add_parser("readiness", allow_abbrev=False)
    readiness.add_argument("--timeout", type=float, default=5.0)
    readiness.add_argument("urls", nargs="+")
    database = subparsers.add_parser("database", allow_abbrev=False)
    database.add_argument("tables_csv", type=Path)
    database.add_argument("columns_csv", type=Path)
    database.add_argument("indexes_csv", type=Path)
    topics = subparsers.add_parser("topics", allow_abbrev=False)
    topics.add_argument("--config", type=Path, required=True)
    selection = subparsers.add_parser("operator-selection", allow_abbrev=False)
    selection.add_argument("--course-root", required=True)
    selection.add_argument("--result-root", required=True)
    selection.add_argument("--profile", action="append", default=[])
    selection.add_argument("operator_json", type=Path)
    runtime = subparsers.add_parser("operator-runtime", allow_abbrev=False)
    runtime.add_argument("--course-root", required=True)
    runtime.add_argument("--result-root", required=True)
    runtime.add_argument("--profile", action="append", default=[])
    runtime.add_argument("--git-sha", required=True)
    runtime.add_argument("--image-inspection-json", type=Path, required=True)
    runtime.add_argument("operator_json", type=Path)
    runtime.add_argument("inspection_json", type=Path)
    container_images = subparsers.add_parser("container-images", allow_abbrev=False)
    container_images.add_argument("--service", action="append", required=True)
    container_images.add_argument("inspection_json", type=Path)
    image_revisions = subparsers.add_parser("image-revisions", allow_abbrev=False)
    image_revisions.add_argument("--git-sha", required=True)
    image_revisions.add_argument("--image-id", action="append", required=True)
    image_revisions.add_argument("inspection_json", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "gpus":
            validate_gpu_output(sys.stdin.read())
        elif args.command == "host-compose":
            ports = validate_host_compose(
                _load_json(args.platform_json),
                _load_json(args.operator_json),
                course_root=args.course_root,
                result_root=args.result_root,
            )
            print(" ".join(str(port) for port in ports))
        elif args.command == "readiness":
            validate_readiness(args.urls, args.timeout)
        elif args.command == "database":
            validate_database_catalog(
                args.tables_csv.read_text(encoding="utf-8"),
                args.columns_csv.read_text(encoding="utf-8"),
                args.indexes_csv.read_text(encoding="utf-8"),
            )
        elif args.command == "topics":
            validate_kafka_topics(sys.stdin.read(), args.config)
        elif args.command == "operator-selection":
            _, selected = select_operator_services(
                _load_json(args.operator_json),
                profiles=args.profile,
                course_root=args.course_root,
                result_root=args.result_root,
            )
            print(" ".join(selected))
        elif args.command == "operator-runtime":
            validate_operator_runtime(
                _load_json(args.operator_json),
                _load_json(args.inspection_json),
                _load_json(args.image_inspection_json),
                profiles=args.profile,
                course_root=args.course_root,
                result_root=args.result_root,
                expected_git_sha=args.git_sha,
            )
        elif args.command == "container-images":
            image_ids = runtime_container_image_ids(
                _load_json(args.inspection_json),
                expected_services=args.service,
            )
            print(" ".join(image_ids))
        elif args.command == "image-revisions":
            validate_image_revisions(
                _load_json(args.inspection_json),
                expected_image_ids=args.image_id,
                expected_git_sha=args.git_sha,
            )
    except PreflightError as error:
        print(f"preflight: FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
