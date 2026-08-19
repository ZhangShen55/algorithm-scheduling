#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from scripts.aggregate_milestone_2b_cases import publish_json_once
from scripts.milestone_2b_case_runners.evidence import release_identity

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PLATFORM_ROOT.parent
EVIDENCE_PATH = Path("preflight/clean-clone-validation.json")


def _run(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: float,
    environment: dict[str, str] | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    completed = subprocess.run(
        tuple(argv),
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout_seconds,
    )
    elapsed = time.monotonic() - started
    output = (completed.stdout + completed.stderr).strip()
    if completed.returncode != 0:
        raise RuntimeError(f"clean clone command failed ({' '.join(argv)}):\n{output[-16000:]}")
    return {
        "argv": list(argv),
        "cwd": str(cwd.relative_to(WORKSPACE_ROOT)),
        "returncode": completed.returncode,
        "elapsed_seconds": round(elapsed, 3),
        "output_tail": output[-16000:],
    }


def _git_output(*args: str) -> str:
    completed = subprocess.run(
        ("git", *args),
        cwd=WORKSPACE_ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "git command failed")
    return completed.stdout.strip()


def _assert_clean_clone(expected_sha: str) -> dict[str, Any]:
    actual_sha = _git_output("rev-parse", "HEAD")
    if actual_sha != expected_sha:
        raise RuntimeError(
            f"clean clone HEAD mismatch: expected={expected_sha}, actual={actual_sha}"
        )
    status = _git_output("status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise RuntimeError(f"clean clone working tree is dirty:\n{status}")
    tracked = _git_output("ls-files")
    required_profiles = {
        "facerec/config.example.toml",
        "ocr/config.toml.example",
        "text_analysis/config.example.toml",
    }
    tracked_paths = set(tracked.splitlines())
    missing = sorted(required_profiles - tracked_paths)
    if missing:
        raise RuntimeError(f"clean clone 缺少受控本地配置模板: {missing}")
    return {
        "git_sha": actual_sha,
        "worktree_clean": True,
        "tracked_file_count": len(tracked_paths),
        "controlled_local_profiles": sorted(required_profiles),
    }


def _commands() -> tuple[tuple[Path, tuple[str, ...], float], ...]:
    python = str(PLATFORM_ROOT / ".venv/bin/python")
    return (
        (PLATFORM_ROOT, (python, "-m", "pytest", "-q"), 1800),
        (
            WORKSPACE_ROOT / "control_service",
            (python, "-m", "pytest", "-q", "tests"),
            300,
        ),
        (
            WORKSPACE_ROOT / "orchestrator_service",
            (python, "-m", "pytest", "-q", "tests"),
            300,
        ),
        (
            WORKSPACE_ROOT / "vision_orchestrator_service",
            (python, "-m", "pytest", "-q", "tests"),
            300,
        ),
        (
            WORKSPACE_ROOT / "online_gateway_service",
            (python, "-m", "pytest", "-q", "tests"),
            300,
        ),
        (
            PLATFORM_ROOT,
            (python, "-m", "pytest", "-q", "tests/test_operator_config_authority.py"),
            300,
        ),
        (
            WORKSPACE_ROOT,
            (
                python,
                "-m",
                "compileall",
                "-q",
                "control_service/app",
                "orchestrator_service/app",
                "vision_orchestrator_service/app",
                "online_gateway_service/app",
            ),
            300,
        ),
    )


def _integration_commands() -> tuple[tuple[str, tuple[str, ...], float], ...]:
    return (
        (
            "postgres_redis_integration",
            (
                "tests/integration/test_redis_operator_registry.py",
                "tests/integration/test_control_service_foundation.py",
                "tests/integration/test_course_repository.py",
            ),
            900,
        ),
        (
            "kafka_integration",
            (
                "tests/integration/test_kafka_runtime.py",
                "tests/integration/test_milestone_2a_runtime.py",
            ),
            1200,
        ),
    )


def _junit_counts(path: Path, *, layer: str) -> dict[str, int]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as error:
        raise RuntimeError(f"{layer} JUnit 证据不可读") from error
    if root.tag not in {"testsuite", "testsuites"}:
        raise RuntimeError(f"{layer} JUnit 根节点无效: {root.tag}")

    count_names = ("tests", "failures", "errors", "skipped")

    def element_counts(element: ET.Element) -> dict[str, int]:
        counts: dict[str, int] = {}
        for name in count_names:
            raw = element.attrib.get(name)
            if raw is None:
                raise RuntimeError(f"{layer} JUnit {element.tag} 缺少 {name}")
            try:
                value = int(raw)
            except ValueError as error:
                raise RuntimeError(f"{layer} JUnit {name} 不是整数") from error
            if value < 0:
                raise RuntimeError(f"{layer} JUnit {name} 不能为负数")
            counts[name] = value
        return counts

    if root.tag == "testsuite" or all(name in root.attrib for name in count_names):
        counts = element_counts(root)
    else:
        if any(name in root.attrib for name in count_names):
            raise RuntimeError(f"{layer} JUnit testsuites 汇总字段不完整")
        suites = tuple(child for child in root if child.tag == "testsuite")
        if not suites:
            raise RuntimeError(f"{layer} 没有实际执行任何测试")
        counts = {name: 0 for name in count_names}
        for suite in suites:
            child_counts = element_counts(suite)
            for name in count_names:
                counts[name] += child_counts[name]
    if counts["tests"] <= 0:
        raise RuntimeError(f"{layer} 没有实际执行任何测试")
    if any(counts[name] != 0 for name in ("failures", "errors", "skipped")):
        raise RuntimeError(f"{layer} 必须零失败、零错误、零跳过: {counts}")
    return counts


def _run_integration_layer(
    *,
    layer: str,
    targets: Sequence[str],
    timeout_seconds: float,
    environment: dict[str, str],
    junit_directory: Path,
) -> dict[str, Any]:
    junit_path = junit_directory / f"{layer}.xml"
    command = (
        str(PLATFORM_ROOT / ".venv/bin/python"),
        "-m",
        "pytest",
        "-q",
        *targets,
        f"--junitxml={junit_path}",
    )
    result = _run(
        command,
        cwd=PLATFORM_ROOT,
        timeout_seconds=timeout_seconds,
        environment=environment,
    )
    result["junit"] = _junit_counts(junit_path, layer=layer)
    return result


def run_gate(*, release_root: Path, expected_sha: str) -> dict[str, Any]:
    release_tag, release_sha = release_identity(release_root)
    if release_sha != expected_sha:
        raise RuntimeError("release root SHA 与 expected SHA 不一致")
    source = _assert_clean_clone(expected_sha)
    environment = dict(os.environ)
    environment["EXPECTED_GIT_SHA"] = expected_sha
    command_results = [
        _run(
            argv,
            cwd=cwd,
            timeout_seconds=timeout,
            environment=environment,
        )
        for cwd, argv, timeout in _commands()
    ]
    with tempfile.TemporaryDirectory(prefix="milestone-2b-clean-clone-junit-") as raw:
        junit_directory = Path(raw)
        integration_results = {
            layer: _run_integration_layer(
                layer=layer,
                targets=targets,
                timeout_seconds=timeout,
                environment=environment,
                junit_directory=junit_directory,
            )
            for layer, targets, timeout in _integration_commands()
        }
    document = {
        "schema_version": 1,
        "evidence_type": "milestone_2b_clean_clone_validation",
        "release_tag": release_tag,
        "git_sha": expected_sha,
        "source": source,
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "layers": {
            "static": {"status": "通过", "command_indexes": [6]},
            "unit": {"status": "通过", "command_indexes": [0, 1, 2, 3, 4, 5]},
            "postgres_redis_integration": {
                "status": "通过",
                "junit": integration_results["postgres_redis_integration"]["junit"],
            },
            "kafka_integration": {
                "status": "通过",
                "junit": integration_results["kafka_integration"]["junit"],
            },
            "service_runtime": {
                "status": "等待同 release 证据",
                "required_evidence": "stage45 readiness",
            },
            "operator_contract": {
                "status": "等待同 release 证据",
                "required_evidence": "24 实例注册与八算子 Smoke",
            },
        },
        "commands": command_results,
        "integration_commands": integration_results,
    }
    publish_json_once(
        release_root=release_root,
        relative_path=EVIDENCE_PATH,
        document=document,
    )
    return document


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--release-root", type=_path, required=True)
    parser.add_argument("--expected-git-sha", required=True)
    args = parser.parse_args(argv)
    if len(args.expected_git_sha) != 40 or any(
        character not in "0123456789abcdef" for character in args.expected_git_sha
    ):
        parser.error("expected-git-sha must be 40 lowercase hexadecimal characters")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    document = run_gate(
        release_root=args.release_root,
        expected_sha=args.expected_git_sha,
    )
    print(
        json.dumps(
            {
                "status": "通过",
                "git_sha": document["git_sha"],
                "command_count": len(document["commands"]),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
