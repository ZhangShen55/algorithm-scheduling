from __future__ import annotations

import copy
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


def _inventory() -> dict[str, object]:
    return {
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
                "size_bytes": 100,
                "revision": "a" * 40,
            },
            {
                "image_id": RUNNING,
                "repo_tags": ["running:v1"],
                "repo_digests": [],
                "size_bytes": 200,
                "revision": "b" * 40,
            },
            {
                "image_id": ROLLBACK,
                "repo_tags": ["rollback:v1"],
                "repo_digests": [],
                "size_bytes": 300,
                "revision": "c" * 40,
            },
            {
                "image_id": BASE,
                "repo_tags": ["ubuntu:22.04"],
                "repo_digests": [],
                "size_bytes": 400,
                "revision": None,
            },
            {
                "image_id": OLD,
                "repo_tags": ["old:v1", "old:stable"],
                "repo_digests": ["old@sha256:" + "b" * 64],
                "size_bytes": 500,
                "revision": "d" * 40,
            },
            {
                "image_id": DANGLING,
                "repo_tags": [],
                "repo_digests": [],
                "size_bytes": 600,
                "revision": "d" * 40,
            },
            {
                "image_id": RETIRED,
                "repo_tags": ["retired:v1"],
                "repo_digests": [],
                "size_bytes": 700,
                "revision": "e" * 40,
            },
        ],
    }


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
            retired_release_shas=["d" * 40, "e" * 40],
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
                        "Size": 123,
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
            "size_bytes": 123,
            "revision": "d" * 40,
            "release_tag": "v1",
            "labels": {
                "org.opencontainers.image.revision": "d" * 40,
                "org.opencontainers.image.version": "v1",
            },
        }
    ]


def test_inventory_fails_closed_on_partial_inspect() -> None:
    container_id = "a" * 64
    image_id = "sha256:" + "b" * 64

    def output(command: Sequence[str]) -> str:
        rendered = tuple(command)
        if rendered == ("docker", "ps", "-aq", "--no-trunc"):
            return container_id + "\n"
        if rendered == ("docker", "image", "ls", "--all", "--no-trunc", "--quiet"):
            return image_id + "\n"
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
        docker_df_before="before",
        docker_df_after=lambda: "after",
    )

    assert commands == [
        ("docker", "image", "rm", OLD),
        ("docker", "image", "rm", DANGLING),
    ]
    assert result["status"] == "PASS"
    assert result["docker_system_df_before"] == "before"
    assert result["docker_system_df_after"] == "after"
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
