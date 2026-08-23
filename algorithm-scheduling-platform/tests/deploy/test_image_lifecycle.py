from __future__ import annotations

import copy
import hashlib
import json
import os
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from deploy.scripts import image_lifecycle

CURRENT = "sha256:" + "1" * 64
RUNNING = "sha256:" + "2" * 64
ROLLBACK = "sha256:" + "3" * 64
BASE = "sha256:" + "4" * 64
OLD = "sha256:" + "5" * 64
DANGLING = "sha256:" + "6" * 64
RETIRED = "sha256:" + "7" * 64
STOPPED_CONTAINER = "8" * 64
RUNNING_CONTAINER = "9" * 64


def _df_evidence(images: Sequence[dict[str, object]]) -> dict[str, object]:
    return {
        "command": list(image_lifecycle.DOCKER_DF_COMMAND),
        "raw_sha256": "f" * 64,
        "images": [
            {
                "image_id": image["image_id"],
                "unique_size": image["unique_size"],
                "unique_size_bytes": image["unique_size_bytes"],
            }
            for image in images
        ],
    }


def _inventory() -> dict[str, object]:
    inventory: dict[str, object] = {
        "schema_version": 1,
        "captured_at": "2026-08-23T00:00:00+00:00",
        "containers": [
            {
                "container_id": RUNNING_CONTAINER,
                "image_id": RUNNING,
                "name": "running",
                "state": "running",
                "running": True,
                "compose_project": "other",
                "compose_service": "running",
            },
            {
                "container_id": STOPPED_CONTAINER,
                "image_id": RETIRED,
                "name": "old-service",
                "state": "exited",
                "running": False,
                "compose_project": "algorithm-operators",
                "compose_service": "ocr-gpu0",
            },
        ],
        "images": [
            {
                "image_id": CURRENT,
                "repo_tags": ["current:v1", "current:latest"],
                "repo_digests": ["current@sha256:" + "a" * 64],
                "unique_size": "100B",
                "unique_size_bytes": 100,
                "revision": "a" * 40,
            },
            {
                "image_id": RUNNING,
                "repo_tags": ["running:v1"],
                "repo_digests": [],
                "unique_size": "200B",
                "unique_size_bytes": 200,
                "revision": "b" * 40,
            },
            {
                "image_id": ROLLBACK,
                "repo_tags": ["rollback:v1"],
                "repo_digests": [],
                "unique_size": "300B",
                "unique_size_bytes": 300,
                "revision": "c" * 40,
            },
            {
                "image_id": BASE,
                "repo_tags": ["ubuntu:22.04"],
                "repo_digests": [],
                "unique_size": "400B",
                "unique_size_bytes": 400,
                "revision": None,
            },
            {
                "image_id": OLD,
                "repo_tags": ["old:v1", "old:stable"],
                "repo_digests": ["old@sha256:" + "b" * 64],
                "unique_size": "500B",
                "unique_size_bytes": 500,
                "revision": "d" * 40,
            },
            {
                "image_id": DANGLING,
                "repo_tags": [],
                "repo_digests": [],
                "unique_size": "600B",
                "unique_size_bytes": 600,
                "revision": "d" * 40,
            },
            {
                "image_id": RETIRED,
                "repo_tags": ["retired:v1"],
                "repo_digests": [],
                "unique_size": "700B",
                "unique_size_bytes": 700,
                "revision": "e" * 40,
            },
        ],
    }
    images = cast(list[dict[str, object]], inventory["images"])
    inventory["docker_system_df"] = _df_evidence(images)
    return inventory


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        ("0B", 0),
        ("32B", 32),
        ("2.327kB", 2_327),
        ("104.9MB", 104_900_000),
        ("1.02GB", 1_020_000_000),
        ("2.8TB", 2_800_000_000_000),
        ("1PB", 1_000_000_000_000_000),
    ),
)
def test_docker_unique_size_uses_decimal_units(value: str, expected: int) -> None:
    assert image_lifecycle.parse_docker_size_bytes(value) == expected


@pytest.mark.parametrize(
    "value",
    (None, 104.9, "", "-1MB", "104.9MiB", "104.9 MB", "NaNMB", "0.1B"),
)
def test_docker_unique_size_rejects_unparseable_values(value: object) -> None:
    with pytest.raises(image_lifecycle.ImageLifecycleError, match="UniqueSize"):
        image_lifecycle.parse_docker_size_bytes(value)


def _df_json(rows: Sequence[dict[str, object]]) -> str:
    return json.dumps(
        {
            "Images": list(rows),
            "Containers": [],
            "LocalVolumes": [],
            "BuildCache": [],
        }
    )


def test_docker_df_requires_exactly_one_row_for_every_full_image_id() -> None:
    valid = {"ID": CURRENT, "UniqueSize": "104.9MB"}

    with pytest.raises(image_lifecycle.ImageLifecycleError, match="缺失"):
        image_lifecycle._docker_df_image_sizes(_df_json([valid]), (CURRENT, OLD))
    with pytest.raises(image_lifecycle.ImageLifecycleError, match="未知"):
        image_lifecycle._docker_df_image_sizes(
            _df_json([valid, {"ID": OLD, "UniqueSize": "1MB"}]),
            (CURRENT,),
        )
    with pytest.raises(image_lifecycle.ImageLifecycleError, match="重复"):
        image_lifecycle._docker_df_image_sizes(_df_json([valid, valid]), (CURRENT,))
    with pytest.raises(image_lifecycle.ImageLifecycleError, match="非完整镜像 ID"):
        image_lifecycle._docker_df_image_sizes(
            _df_json([{"ID": CURRENT[7:19], "UniqueSize": "1MB"}]),
            (CURRENT,),
        )


def test_docker_df_summary_excludes_verbose_container_and_cache_details() -> None:
    raw = json.dumps(
        {
            "Images": [{"ID": CURRENT, "UniqueSize": "104.9MB"}],
            "Containers": [{"Command": "python --token secret"}],
            "BuildCache": [{"Description": "/private/model/path"}],
        }
    )

    summary = image_lifecycle.summarize_docker_df(raw)

    assert summary["image_count"] == 1
    assert summary["unique_size_bytes_total"] == 104_900_000
    rendered = json.dumps(summary)
    assert "python --token secret" not in rendered
    assert "/private/model/path" not in rendered


def test_legacy_inspect_size_inventory_is_rejected() -> None:
    inventory = _inventory()
    inventory.pop("docker_system_df")
    images = cast(list[dict[str, object]], inventory["images"])
    for image in images:
        image["size_bytes"] = image.pop("unique_size_bytes")
        image.pop("unique_size")

    with pytest.raises(image_lifecycle.ImageLifecycleError, match="system df"):
        image_lifecycle.validate_inventory(inventory)


def test_prebuild_plan_protects_all_container_references_and_baselines() -> None:
    plan = image_lifecycle.build_cleanup_plan(
        _inventory(),
        stage="prebuild",
        release_tag="v1.0_260823",
        git_sha="a" * 40,
        current_image_ids=[CURRENT],
        rollback_image_ids=[ROLLBACK],
        base_image_ids=[BASE],
        allow_image_ids=[],
        retire_container_ids=[],
        retire_compose_identities=[],
        retired_release_shas=["d" * 40],
    )

    assert set(plan["protected_image_ids"]) == {
        CURRENT,
        RUNNING,
        ROLLBACK,
        BASE,
        RETIRED,
    }
    assert [item["image_id"] for item in plan["candidate_images"]] == [
        OLD,
        DANGLING,
    ]
    assert plan["estimated_reclaim_bytes"] == 1100
    assert plan["candidate_containers"] == []


def test_reclaim_estimate_sums_unique_size_instead_of_virtual_size() -> None:
    inventory = _inventory()
    images = cast(list[dict[str, object]], inventory["images"])
    by_id = {str(image["image_id"]): image for image in images}
    by_id[OLD]["unique_size"] = "104.9MB"
    by_id[OLD]["unique_size_bytes"] = 104_900_000
    by_id[DANGLING]["unique_size"] = "2.327kB"
    by_id[DANGLING]["unique_size_bytes"] = 2_327
    inventory["docker_system_df"] = _df_evidence(images)

    plan = image_lifecycle.build_cleanup_plan(
        inventory,
        stage="prebuild",
        release_tag="v1.0_260823",
        git_sha="a" * 40,
        current_image_ids=[CURRENT],
        rollback_image_ids=[ROLLBACK],
        base_image_ids=[BASE],
        allow_image_ids=[],
        retire_container_ids=[],
        retire_compose_identities=[],
        retired_release_shas=["d" * 40],
    )

    assert plan["estimated_reclaim_bytes"] == 104_902_327
    candidates = cast(list[dict[str, object]], plan["candidate_images"])
    assert [candidate["unique_size"] for candidate in candidates] == [
        "104.9MB",
        "2.327kB",
    ]
    assert all("size_bytes" not in candidate for candidate in candidates)


def test_postacceptance_allows_only_explicit_stopped_container_retirement() -> None:
    plan = image_lifecycle.build_cleanup_plan(
        _inventory(),
        stage="postacceptance",
        release_tag="v1.0_260823",
        git_sha="a" * 40,
        current_image_ids=[CURRENT],
        rollback_image_ids=[ROLLBACK],
        base_image_ids=[BASE],
        allow_image_ids=[],
        retire_container_ids=[STOPPED_CONTAINER],
        retire_compose_identities=["algorithm-operators/ocr-gpu0"],
        retired_release_shas=["d" * 40, "e" * 40],
        acceptance_status="PASS",
    )

    candidate = plan["candidate_containers"][0]
    assert {
        key: candidate[key]
        for key in (
            "container_id",
            "image_id",
            "compose_project",
            "compose_service",
            "state",
        )
    } == {
        "container_id": STOPPED_CONTAINER,
        "image_id": RETIRED,
        "compose_project": "algorithm-operators",
        "compose_service": "ocr-gpu0",
        "state": "exited",
    }
    assert len(candidate["before_snapshot_sha256"]) == 64
    assert RETIRED in {item["image_id"] for item in plan["candidate_images"]}

    with pytest.raises(image_lifecycle.ImageLifecycleError, match="仍在运行"):
        image_lifecycle.build_cleanup_plan(
            _inventory(),
            stage="postacceptance",
            release_tag="v1.0_260823",
            git_sha="a" * 40,
            current_image_ids=[CURRENT],
            rollback_image_ids=[ROLLBACK],
            base_image_ids=[BASE],
            allow_image_ids=[],
            retire_container_ids=[RUNNING_CONTAINER],
            retire_compose_identities=["algorithm-scheduling-platform/control-service"],
            retired_release_shas=["d" * 40, "e" * 40],
            acceptance_status="PASS",
        )


def test_cleanup_plan_requires_rollback_and_base_protection() -> None:
    common = {
        "stage": "prebuild",
        "release_tag": "v1.0_260823",
        "git_sha": "a" * 40,
        "current_image_ids": [CURRENT],
        "allow_image_ids": [],
        "retire_container_ids": [],
        "retire_compose_identities": [],
        "retired_release_shas": ["d" * 40],
    }
    with pytest.raises(image_lifecycle.ImageLifecycleError, match="回滚"):
        image_lifecycle.build_cleanup_plan(
            _inventory(),
            rollback_image_ids=[],
            base_image_ids=[BASE],
            **common,
        )
    with pytest.raises(image_lifecycle.ImageLifecycleError, match="基础镜像"):
        image_lifecycle.build_cleanup_plan(
            _inventory(),
            rollback_image_ids=[ROLLBACK],
            base_image_ids=[],
            **common,
        )


def test_postacceptance_rejects_unbound_container_or_revision() -> None:
    foreign = _inventory()
    containers = cast(list[dict[str, object]], foreign["containers"])
    containers[1]["compose_project"] = "unrelated-project"
    with pytest.raises(image_lifecycle.ImageLifecycleError, match="受控 Compose"):
        image_lifecycle.build_cleanup_plan(
            foreign,
            stage="postacceptance",
            release_tag="v1.0_260823",
            git_sha="a" * 40,
            current_image_ids=[CURRENT],
            rollback_image_ids=[ROLLBACK],
            base_image_ids=[BASE],
            allow_image_ids=[],
            retire_container_ids=[STOPPED_CONTAINER],
            retire_compose_identities=["algorithm-operators/ocr-gpu0"],
            retired_release_shas=["e" * 40],
            acceptance_status="PASS",
        )

    with pytest.raises(image_lifecycle.ImageLifecycleError, match="受控 Compose"):
        image_lifecycle.build_cleanup_plan(
            _inventory(),
            stage="postacceptance",
            release_tag="v1.0_260823",
            git_sha="a" * 40,
            current_image_ids=[CURRENT],
            rollback_image_ids=[ROLLBACK],
            base_image_ids=[BASE],
            allow_image_ids=[],
            retire_container_ids=[STOPPED_CONTAINER],
            retire_compose_identities=["algorithm-operators/vbas-gpu0"],
            retired_release_shas=["e" * 40],
            acceptance_status="PASS",
        )

    with pytest.raises(image_lifecycle.ImageLifecycleError, match="已退役 release"):
        image_lifecycle.build_cleanup_plan(
            _inventory(),
            stage="postacceptance",
            release_tag="v1.0_260823",
            git_sha="a" * 40,
            current_image_ids=[CURRENT],
            rollback_image_ids=[ROLLBACK],
            base_image_ids=[BASE],
            allow_image_ids=[],
            retire_container_ids=[STOPPED_CONTAINER],
            retire_compose_identities=["algorithm-operators/ocr-gpu0"],
            retired_release_shas=["d" * 40],
            acceptance_status="PASS",
        )


def test_cleanup_plan_is_atomic_and_write_once(tmp_path: Path) -> None:
    path = tmp_path / "cleanup-plan.json"
    payload = image_lifecycle.build_cleanup_plan(
        _inventory(),
        stage="prebuild",
        release_tag="v1.0_260823",
        git_sha="a" * 40,
        current_image_ids=[CURRENT],
        rollback_image_ids=[ROLLBACK],
        base_image_ids=[BASE],
        allow_image_ids=[],
        retire_container_ids=[],
        retire_compose_identities=[],
        retired_release_shas=["d" * 40],
    )

    digest = image_lifecycle.publish_plan(path, payload)

    assert len(digest) == 64
    assert json.loads(path.read_text(encoding="utf-8")) == payload
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(image_lifecycle.ImageLifecycleError, match="已经存在"):
        image_lifecycle.publish_plan(path, payload)


def test_execute_rejects_inventory_drift_before_any_delete(tmp_path: Path) -> None:
    plan_path = tmp_path / "cleanup-plan.json"
    plan = image_lifecycle.build_cleanup_plan(
        _inventory(),
        stage="prebuild",
        release_tag="v1.0_260823",
        git_sha="a" * 40,
        current_image_ids=[CURRENT],
        rollback_image_ids=[ROLLBACK],
        base_image_ids=[BASE],
        allow_image_ids=[],
        retire_container_ids=[],
        retire_compose_identities=[],
        retired_release_shas=["d" * 40],
    )
    digest = image_lifecycle.publish_plan(plan_path, plan)
    commands: list[tuple[str, ...]] = []
    drifted = copy.deepcopy(_inventory())
    containers = cast(list[dict[str, object]], drifted["containers"])
    containers[0]["state"] = "paused"

    with pytest.raises(image_lifecycle.ImageLifecycleError, match="状态漂移"):
        image_lifecycle.execute_plan(
            plan_path,
            approved_sha256=digest,
            live_inventory=drifted,
            command_runner=lambda command: commands.append(tuple(command)),
            result_path=tmp_path / "cleanup-result.json",
        )

    assert commands == []


@pytest.mark.parametrize(
    "bad_target",
    (
        "/data/result/x",
        "/models/x",
        "/repo/.git",
        "/reports/releases/x",
        "docker volume rm data",
        "*.img",
    ),
)
def test_runtime_guard_rejects_forbidden_targets(bad_target: str) -> None:
    with pytest.raises(image_lifecycle.ImageLifecycleError):
        image_lifecycle.validate_no_forbidden_targets({"target": bad_target})


def test_cleanup_commands_are_exact_and_never_use_prune_or_volumes() -> None:
    commands = image_lifecycle.deletion_commands(
        [STOPPED_CONTAINER],
        [OLD, DANGLING],
    )
    rendered = "\n".join(" ".join(command) for command in commands)

    assert commands == [
        ("docker", "container", "rm", STOPPED_CONTAINER),
        ("docker", "image", "rm", OLD),
        ("docker", "image", "rm", DANGLING),
    ]
    assert "prune" not in rendered
    assert "volume" not in rendered
    assert "down" not in rendered
    assert "-f" not in rendered


def test_inventory_captures_all_tags_digests_and_compose_identity() -> None:
    container_id = "a" * 64
    image_id = "sha256:" + "b" * 64

    def output(command: Sequence[str]) -> str:
        rendered = tuple(command)
        if rendered == ("docker", "ps", "-aq", "--no-trunc"):
            return container_id + "\n"
        if rendered == ("docker", "image", "ls", "--all", "--no-trunc", "--quiet"):
            return image_id + "\n" + image_id + "\n"
        if rendered == image_lifecycle.DOCKER_DF_COMMAND:
            return json.dumps(
                {
                    "Images": [
                        {
                            "ID": image_id,
                            "Repository": "algorithm-ocr",
                            "Tag": "v1",
                            "Size": "4.2GB",
                            "SharedSize": "4.095GB",
                            "UniqueSize": "104.9MB",
                        }
                    ],
                    "Containers": [],
                    "LocalVolumes": [],
                    "BuildCache": [],
                }
            )
        if rendered[:3] == ("docker", "container", "inspect"):
            return json.dumps(
                [
                    {
                        "Id": container_id,
                        "Image": image_id,
                        "Name": "/ocr-gpu0",
                        "Config": {
                            "Labels": {
                                "com.docker.compose.project": "algorithm-operators",
                                "com.docker.compose.service": "ocr-gpu0",
                            }
                        },
                        "State": {"Running": True, "Status": "running"},
                    }
                ]
            )
        if rendered[:3] == ("docker", "image", "inspect"):
            return json.dumps(
                [
                    {
                        "Id": image_id,
                        "RepoTags": ["algorithm-ocr:v1", "algorithm-ocr:stable"],
                        "RepoDigests": ["algorithm-ocr@sha256:" + "c" * 64],
                        "Size": 4_200_000_000,
                        "Config": {
                            "Labels": {
                                "org.opencontainers.image.revision": "d" * 40,
                                "org.opencontainers.image.version": "v1",
                            }
                        },
                    }
                ]
            )
        raise AssertionError(rendered)

    inventory = image_lifecycle.capture_inventory(output=output)

    assert inventory["containers"][0]["compose_service"] == "ocr-gpu0"
    assert inventory["images"] == [
        {
            "image_id": image_id,
            "repo_tags": ["algorithm-ocr:stable", "algorithm-ocr:v1"],
            "repo_digests": ["algorithm-ocr@sha256:" + "c" * 64],
            "unique_size": "104.9MB",
            "unique_size_bytes": 104_900_000,
            "revision": "d" * 40,
            "release_tag": "v1",
            "labels": {
                "org.opencontainers.image.revision": "d" * 40,
                "org.opencontainers.image.version": "v1",
            },
        }
    ]
    assert inventory["docker_system_df"] == {
        "command": list(image_lifecycle.DOCKER_DF_COMMAND),
        "raw_sha256": hashlib.sha256(
            output(image_lifecycle.DOCKER_DF_COMMAND).encode()
        ).hexdigest(),
        "images": [
            {
                "image_id": image_id,
                "unique_size": "104.9MB",
                "unique_size_bytes": 104_900_000,
            }
        ],
    }


def test_inventory_fails_closed_on_partial_inspect() -> None:
    container_id = "a" * 64
    image_id = "sha256:" + "b" * 64

    def output(command: Sequence[str]) -> str:
        rendered = tuple(command)
        if rendered == ("docker", "ps", "-aq", "--no-trunc"):
            return container_id + "\n"
        if rendered == ("docker", "image", "ls", "--all", "--no-trunc", "--quiet"):
            return image_id + "\n"
        if rendered == image_lifecycle.DOCKER_DF_COMMAND:
            return json.dumps(
                {"Images": [{"ID": image_id, "UniqueSize": "104.9MB"}]}
            )
        return "[]"

    with pytest.raises(image_lifecycle.ImageLifecycleError, match="inspect 结果不完整"):
        image_lifecycle.capture_inventory(output=output)


def test_execute_uses_only_full_ids_and_records_df_and_verification(tmp_path: Path) -> None:
    plan_path = tmp_path / "cleanup-plan.json"
    result_path = tmp_path / "cleanup-result.json"
    plan = image_lifecycle.build_cleanup_plan(
        _inventory(),
        stage="prebuild",
        release_tag="v1.0_260823",
        git_sha="a" * 40,
        current_image_ids=[CURRENT],
        rollback_image_ids=[ROLLBACK],
        base_image_ids=[BASE],
        allow_image_ids=[],
        retire_container_ids=[],
        retire_compose_identities=[],
        retired_release_shas=["d" * 40],
    )
    digest = image_lifecycle.publish_plan(plan_path, plan)
    commands: list[tuple[str, ...]] = []
    live = copy.deepcopy(_inventory())

    def run(command: Sequence[str]) -> None:
        commands.append(tuple(command))
        target_id = command[-1]
        images = cast(list[dict[str, object]], live["images"])
        live["images"] = [image for image in images if image["image_id"] != target_id]
        docker_df = cast(dict[str, object], live["docker_system_df"])
        df_images = cast(list[dict[str, object]], docker_df["images"])
        docker_df["images"] = [
            image for image in df_images if image["image_id"] != target_id
        ]

    def exists(kind: str, target_id: str) -> bool:
        key = "containers" if kind == "container" else "images"
        id_key = "container_id" if kind == "container" else "image_id"
        records = cast(list[dict[str, object]], live[key])
        return any(record[id_key] == target_id for record in records)

    result = image_lifecycle.execute_plan(
        plan_path,
        approved_sha256=digest,
        live_inventory=_inventory(),
        command_runner=run,
        result_path=result_path,
        inventory_loader=lambda: live,
        target_verifier=exists,
        docker_df_before={"raw_sha256": "a" * 64, "image_count": 7},
        docker_df_after=lambda: {"raw_sha256": "b" * 64, "image_count": 5},
    )

    assert commands == [
        ("docker", "image", "rm", OLD),
        ("docker", "image", "rm", DANGLING),
    ]
    assert result["status"] == "PASS"
    assert result["docker_system_df_before"] == {
        "raw_sha256": "a" * 64,
        "image_count": 7,
    }
    assert result["docker_system_df_after"] == {
        "raw_sha256": "b" * 64,
        "image_count": 5,
    }
    assert all(item["verified_absent"] is True for item in result["targets"])
    assert result_path.stat().st_mode & 0o777 == 0o600


def test_cleanup_completion_requires_stack_and_exact_seven_smokes() -> None:
    execution = {
        "stage": "postacceptance",
        "status": "AWAITING_REVALIDATION",
        "release_tag": "v1.0_260823",
        "git_sha": "a" * 40,
    }
    stack = {
        "status": "PASS",
        "git_sha": "a" * 40,
        "summary": {
            "infrastructure": 4,
            "platform_services": 4,
            "operator_instances": 21,
            "gpu_instances": 18,
            "cpu_instances": 3,
            "registered_instances": 21,
        },
    }
    smokes = [
        {
            "evidence_type": "operator_smoke",
            "operator_code": operator,
            "status": "PASS",
            "git_sha": "a" * 40,
            "release_tag": "v1.0_260823",
            "mock": False,
        }
        for operator in sorted(image_lifecycle.SMOKE_OPERATORS)
    ]

    completion = image_lifecycle.verify_cleanup_readiness(execution, stack, smokes)

    assert completion["status"] == "PASS"
    assert completion["checks"]["operator_smoke"] == 7
    with pytest.raises(image_lifecycle.ImageLifecycleError, match="7/7 Smoke"):
        image_lifecycle.verify_cleanup_readiness(execution, stack, smokes[:-1])


def test_cleanup_revalidation_evidence_must_be_newer_than_execution(tmp_path: Path) -> None:
    cutoff = datetime.now(UTC)
    evidence = tmp_path / "status.json"
    evidence.write_text("{}\n", encoding="utf-8")
    evidence.chmod(0o600)
    stale = (cutoff - timedelta(seconds=10)).timestamp()
    os.utime(evidence, (stale, stale))
    execution = {"completed_at": cutoff.isoformat()}

    with pytest.raises(image_lifecycle.ImageLifecycleError, match="早于清理完成时间"):
        image_lifecycle.verify_evidence_freshness(execution, [evidence])

    fresh = (cutoff + timedelta(seconds=10)).timestamp()
    os.utime(evidence, (fresh, fresh))
    image_lifecycle.verify_evidence_freshness(execution, [evidence])
