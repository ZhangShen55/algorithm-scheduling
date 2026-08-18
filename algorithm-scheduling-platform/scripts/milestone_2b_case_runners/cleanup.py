from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import stat
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any, cast

import psycopg
from pymongo import MongoClient
from redis import Redis

from .infrastructure import (
    _POSTGRES_ADMIN_DSN,
    _cleanup_redis_prefix,
    _drop_isolated_database,
    _reset_kafka_resources,
)
from .process import FoundationGroup, foundation_cleanup_resources
from .safety import ResourceSpec


def _cleanup_temporary_directories(resource: ResourceSpec) -> list[str]:
    prefix_path = Path(resource.name)
    temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
    if prefix_path.parent.resolve(strict=True) != temporary_root:
        raise ValueError("cleanup temp prefix is outside the system temporary root")
    prefix = prefix_path.name
    directory_fd = os.open(
        temporary_root,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    removed: list[str] = []
    try:
        names = sorted(
            entry.name
            for entry in os.scandir(directory_fd)
            if entry.name.startswith(prefix)
        )
        for name in names:
            metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                raise ValueError(f"cleanup temp residue metadata is unsafe: {name}")
            shutil.rmtree(name, dir_fd=directory_fd)
            removed.append(name)
        residual = sorted(
            entry.name
            for entry in os.scandir(directory_fd)
            if entry.name.startswith(prefix)
        )
        if residual:
            raise ValueError(
                f"cleanup temp residue remains: {', '.join(residual)}"
            )
    finally:
        os.close(directory_fd)
    return removed


def _cleanup_redis(resource: ResourceSpec) -> None:
    client = Redis.from_url("redis://127.0.0.1:6379/15", decode_responses=True)
    try:
        client.ping()
        _cleanup_redis_prefix(client, resource.name)
    finally:
        client.close()


def _cleanup_postgresql(resource: ResourceSpec) -> None:
    if resource.kind != "database":
        raise ValueError("PostgreSQL cleanup requires a database resource")
    with psycopg.connect(_POSTGRES_ADMIN_DSN, autocommit=True) as admin:
        _drop_isolated_database(admin, resource.name)


def _cleanup_mongodb(resource: ResourceSpec) -> None:
    if resource.kind != "mongodb_database":
        raise ValueError("MongoDB cleanup requires a mongodb_database resource")
    username = os.getenv("MONGO_ROOT_USERNAME", "root")
    password = os.getenv("MONGO_ROOT_PASSWORD", "root")
    if not username or not password:
        raise ValueError("MongoDB cleanup credentials must be non-empty")
    client: MongoClient[dict[str, Any]] = MongoClient(
        "mongodb://127.0.0.1:27017/",
        username=username,
        password=password,
        authSource="admin",
        serverSelectionTimeoutMS=2000,
    )
    try:
        client.admin.command("ping")
        client.drop_database(resource.name)
    finally:
        client.close()


async def _capture_async_cleanup(
    errors: list[str], label: str, operation: Callable[[], Awaitable[None]]
) -> None:
    try:
        await operation()
    except Exception as exc:
        errors.append(f"{label}: {type(exc).__name__}: {exc}")


async def _capture_sync_cleanup(
    errors: list[str], label: str, operation: Callable[[], None]
) -> None:
    try:
        await asyncio.to_thread(operation)
    except Exception as exc:
        errors.append(f"{label}: {type(exc).__name__}: {exc}")


async def cleanup_foundation_resources(
    group: FoundationGroup, case_id: str, run_id: str
) -> dict[str, object]:
    resources = foundation_cleanup_resources(group, case_id, run_id)
    temporary = resources[0]
    errors: list[str] = []
    preserve_temporary = False
    if group == "registry":
        await _capture_sync_cleanup(
            errors,
            "redis",
            lambda: _cleanup_redis(resources[1]),
        )
        if case_id in {"REG-014", "REG-015"}:
            await _capture_sync_cleanup(
                errors,
                "postgresql",
                lambda: _cleanup_postgresql(resources[2]),
            )
    elif group == "infrastructure":
        postgresql, mongodb, topic, consumer_group, redis = resources[1:]
        await _capture_sync_cleanup(
            errors,
            "postgresql",
            lambda: _cleanup_postgresql(postgresql),
        )
        await _capture_sync_cleanup(
            errors,
            "mongodb",
            lambda: _cleanup_mongodb(mongodb),
        )
        await _capture_async_cleanup(
            errors,
            "kafka",
            lambda: _reset_kafka_resources(topic.name, consumer_group.name),
        )
        await _capture_sync_cleanup(
            errors,
            "redis",
            lambda: _cleanup_redis(redis),
        )
    elif group == "load":
        from .load import (
            _cleanup_case_lease_receipts,
            _cleanup_course_fact,
            _cleanup_runtime_recovery_receipts,
        )

        if any(resource.kind == "container" for resource in resources):
            def cleanup_runtime_recovery() -> None:
                _cleanup_runtime_recovery_receipts(case_id, run_id)

            recovery_error_count = len(errors)
            await _capture_sync_cleanup(
                errors,
                "runtime_recovery",
                cleanup_runtime_recovery,
            )
            preserve_temporary = len(errors) != recovery_error_count

        database_resources = [
            resource for resource in resources if resource.kind == "database"
        ]
        if database_resources:
            database_scope = database_resources[0].name
            expected_prefix = "algorithm:course-task:"
            if not database_scope.startswith(expected_prefix):
                errors.append("postgresql: load cleanup database scope is invalid")
            else:
                task_id = database_scope.removeprefix(expected_prefix)
                await _capture_sync_cleanup(
                    errors,
                    "postgresql",
                    lambda: _cleanup_course_fact(task_id),
                )

        if case_id == "LOAD-015":
            def cleanup_load_lease_receipts() -> None:
                _cleanup_case_lease_receipts(run_id)

            lease_error_count = len(errors)
            await _capture_sync_cleanup(
                errors,
                "redis",
                cleanup_load_lease_receipts,
            )
            preserve_temporary = preserve_temporary or len(errors) != lease_error_count
    removed: list[str] = []
    if not preserve_temporary:
        await _capture_sync_cleanup(
            errors,
            "temporary_files",
            lambda: removed.extend(_cleanup_temporary_directories(temporary)),
        )
    prefix_path = Path(temporary.name)
    temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
    residual = sorted(
        entry.name
        for entry in temporary_root.iterdir()
        if entry.name.startswith(prefix_path.name)
    )
    return {
        "case_id": case_id,
        "group": group,
        "run_id": run_id,
        "status": "failed" if errors else "clean",
        "removed_temp_directories": removed,
        "residual_temp_directories": residual,
        "errors": errors,
    }


def cleanup_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument(
        "--group",
        required=True,
        choices=("deployment", "gpu", "registry", "infrastructure", "load"),
    )
    parser.add_argument("--case", required=True)
    parser.add_argument("--run-id", required=True)
    arguments = parser.parse_args(argv)
    try:
        result = asyncio.run(
            cleanup_foundation_resources(
                cast(FoundationGroup, arguments.group),
                arguments.case,
                arguments.run_id,
            )
        )
    except (OSError, RuntimeError, ValueError) as exc:
        result = {
            "case_id": arguments.case,
            "group": arguments.group,
            "run_id": arguments.run_id,
            "status": "failed",
            "removed_temp_directories": [],
            "residual_temp_directories": [],
            "errors": [f"{type(exc).__name__}: {exc}"],
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "clean" else 1


if __name__ == "__main__":
    raise SystemExit(cleanup_main())
