#!/usr/bin/env python3
"""Strict loader for the current seven-operator deployment authority."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

DeviceKind = Literal["gpu", "cpu"]
DEPLOY_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TOPOLOGY_PATH = DEPLOY_ROOT / "operator-topology.json"
OPERATOR_FIELDS = {
    "operator_code",
    "service_prefix",
    "device_kind",
    "instance_count",
    "container_port",
    "host_port_base",
    "endpoint_scheme",
    "project_directory",
    "dockerfile",
    "image_repository",
    "local_config_name",
    "deploy_config_name",
    "config_target",
    "declared_capacity",
    "capabilities",
    "smoke_case_id",
}
EXPECTED_TOTALS = {
    "operator_types": 7,
    "instances": 21,
    "gpu_instances": 18,
    "cpu_instances": 3,
    "config_authority_processes": 14,
    "operator_smoke_types": 7,
}


class OperatorTopologyError(ValueError):
    """Raised when the deployment topology authority is invalid."""


@dataclass(frozen=True)
class OperatorTopologyEntry:
    operator_code: str
    service_prefix: str
    device_kind: DeviceKind
    instance_count: int
    container_port: int
    host_port_base: int
    endpoint_scheme: str
    project_directory: str
    dockerfile: str
    image_repository: str
    local_config_name: str
    deploy_config_name: str
    config_target: str
    declared_capacity: int
    capabilities: tuple[str, ...]
    smoke_case_id: str

    def instance_id(self, index: int) -> str:
        if not 0 <= index < self.instance_count:
            raise OperatorTopologyError("operator instance index is outside topology")
        return f"{self.service_prefix}-{self.device_kind}{index}"

    def host_port(self, index: int) -> int:
        if not 0 <= index < self.instance_count:
            raise OperatorTopologyError("operator instance index is outside topology")
        return self.host_port_base + index * 10_000


@dataclass(frozen=True)
class OperatorTopology:
    schema_version: int
    operators: tuple[OperatorTopologyEntry, ...]
    totals: dict[str, int]

    @property
    def by_code(self) -> dict[str, OperatorTopologyEntry]:
        return {entry.operator_code: entry for entry in self.operators}

    @property
    def by_prefix(self) -> dict[str, OperatorTopologyEntry]:
        return {entry.service_prefix: entry for entry in self.operators}

    @property
    def instance_ids(self) -> tuple[str, ...]:
        return tuple(
            entry.instance_id(index)
            for entry in self.operators
            for index in range(entry.instance_count)
        )

    def entries(self, device_kind: DeviceKind) -> tuple[OperatorTopologyEntry, ...]:
        return tuple(entry for entry in self.operators if entry.device_kind == device_kind)


def _required_string(value: object, context: str) -> str:
    if type(value) is not str or not value or any(character.isspace() for character in value):
        raise OperatorTopologyError(f"{context} must be a non-empty token")
    return value


def _required_positive_int(value: object, context: str) -> int:
    if type(value) is not int or value <= 0:
        raise OperatorTopologyError(f"{context} must be a positive integer")
    return value


def _load_entry(raw: object, index: int) -> OperatorTopologyEntry:
    if type(raw) is not dict or set(raw) != OPERATOR_FIELDS:
        raise OperatorTopologyError(f"operators[{index}] fields do not match authority")
    document = cast(dict[str, object], raw)
    device_kind = _required_string(document["device_kind"], f"operators[{index}].device_kind")
    if device_kind not in {"gpu", "cpu"}:
        raise OperatorTopologyError(f"operators[{index}].device_kind is invalid")
    endpoint_scheme = _required_string(
        document["endpoint_scheme"], f"operators[{index}].endpoint_scheme"
    )
    if endpoint_scheme not in {"http", "ws"}:
        raise OperatorTopologyError(f"operators[{index}].endpoint_scheme is invalid")
    capabilities = document["capabilities"]
    if (
        type(capabilities) is not list
        or not capabilities
        or any(type(value) is not str or not value for value in capabilities)
        or len(capabilities) != len(set(capabilities))
    ):
        raise OperatorTopologyError(f"operators[{index}].capabilities is invalid")
    return OperatorTopologyEntry(
        operator_code=_required_string(
            document["operator_code"], f"operators[{index}].operator_code"
        ),
        service_prefix=_required_string(
            document["service_prefix"], f"operators[{index}].service_prefix"
        ),
        device_kind=cast(DeviceKind, device_kind),
        instance_count=_required_positive_int(
            document["instance_count"], f"operators[{index}].instance_count"
        ),
        container_port=_required_positive_int(
            document["container_port"], f"operators[{index}].container_port"
        ),
        host_port_base=_required_positive_int(
            document["host_port_base"], f"operators[{index}].host_port_base"
        ),
        endpoint_scheme=endpoint_scheme,
        project_directory=_required_string(
            document["project_directory"], f"operators[{index}].project_directory"
        ),
        dockerfile=_required_string(document["dockerfile"], f"operators[{index}].dockerfile"),
        image_repository=_required_string(
            document["image_repository"], f"operators[{index}].image_repository"
        ),
        local_config_name=_required_string(
            document["local_config_name"], f"operators[{index}].local_config_name"
        ),
        deploy_config_name=_required_string(
            document["deploy_config_name"], f"operators[{index}].deploy_config_name"
        ),
        config_target=_required_string(
            document["config_target"], f"operators[{index}].config_target"
        ),
        declared_capacity=_required_positive_int(
            document["declared_capacity"], f"operators[{index}].declared_capacity"
        ),
        capabilities=tuple(capabilities),
        smoke_case_id=_required_string(
            document["smoke_case_id"], f"operators[{index}].smoke_case_id"
        ),
    )


def load_operator_topology(path: Path = DEFAULT_TOPOLOGY_PATH) -> OperatorTopology:
    if path.is_symlink() or not path.is_file():
        raise OperatorTopologyError("operator topology must be a regular file")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise OperatorTopologyError("operator topology is not valid JSON") from error
    if type(document) is not dict or set(document) != {
        "schema_version",
        "operators",
        "totals",
    }:
        raise OperatorTopologyError("operator topology fields do not match authority")
    if document["schema_version"] != 1 or type(document["operators"]) is not list:
        raise OperatorTopologyError("operator topology schema is unsupported")
    operators = tuple(
        _load_entry(raw, index) for index, raw in enumerate(document["operators"])
    )
    codes = [entry.operator_code for entry in operators]
    prefixes = [entry.service_prefix for entry in operators]
    smoke_ids = [entry.smoke_case_id for entry in operators]
    instance_ids = [
        entry.instance_id(index)
        for entry in operators
        for index in range(entry.instance_count)
    ]
    identities = (codes, prefixes, smoke_ids, instance_ids)
    if any(len(values) != len(set(values)) for values in identities):
        raise OperatorTopologyError("operator topology identities must be unique")
    totals = document["totals"]
    if type(totals) is not dict or set(totals) != set(EXPECTED_TOTALS):
        raise OperatorTopologyError("operator topology totals fields are invalid")
    actual_totals = {
        "operator_types": len(operators),
        "instances": len(instance_ids),
        "gpu_instances": sum(
            entry.instance_count for entry in operators if entry.device_kind == "gpu"
        ),
        "cpu_instances": sum(
            entry.instance_count for entry in operators if entry.device_kind == "cpu"
        ),
        "config_authority_processes": len(operators) * 2,
        "operator_smoke_types": len(smoke_ids),
    }
    if totals != EXPECTED_TOTALS or actual_totals != EXPECTED_TOTALS:
        raise OperatorTopologyError(
            f"operator topology totals changed: declared={totals}, actual={actual_totals}"
        )
    return OperatorTopology(1, operators, dict(totals))


CURRENT_TOPOLOGY = load_operator_topology()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("command", choices=("gpu-matrix", "cpu-matrix", "services"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    command = _build_parser().parse_args(argv).command
    if command == "services":
        for instance_id in CURRENT_TOPOLOGY.instance_ids:
            print(instance_id)
        return 0
    kind: DeviceKind = "gpu" if command == "gpu-matrix" else "cpu"
    for entry in CURRENT_TOPOLOGY.entries(kind):
        for index in range(entry.instance_count):
            suffix = str(index) if kind == "gpu" else ""
            print(f"{entry.instance_id(index)}|{entry.operator_code}|{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
