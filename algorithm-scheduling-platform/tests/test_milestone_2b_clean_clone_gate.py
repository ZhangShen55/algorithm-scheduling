from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import run_milestone_2b_clean_clone_gate as gate


def test_clean_clone_rejects_a_dirty_worktree(monkeypatch) -> None:
    responses = iter(("a" * 40, " M tracked.py"))
    monkeypatch.setattr(gate, "_git_output", lambda *args: next(responses))

    with pytest.raises(RuntimeError, match="working tree is dirty"):
        gate._assert_clean_clone("a" * 40)


def test_clean_clone_requires_controlled_local_profiles(monkeypatch) -> None:
    responses = iter(("a" * 40, "", "facerec/config.example.toml"))
    monkeypatch.setattr(gate, "_git_output", lambda *args: next(responses))

    with pytest.raises(RuntimeError, match="缺少受控本地配置模板"):
        gate._assert_clean_clone("a" * 40)


def test_gate_publishes_an_atomic_same_sha_summary(tmp_path: Path, monkeypatch) -> None:
    git_sha = "b" * 40
    release_root = tmp_path / "v1.0_260820" / git_sha
    release_root.mkdir(parents=True)
    monkeypatch.setattr(
        gate,
        "_assert_clean_clone",
        lambda expected: {
            "git_sha": expected,
            "worktree_clean": True,
            "tracked_file_count": 100,
            "controlled_local_profiles": [],
        },
    )
    monkeypatch.setattr(
        gate,
        "_commands",
        lambda: ((gate.PLATFORM_ROOT, ("python", "-m", "pytest"), 10.0),),
    )
    monkeypatch.setattr(
        gate,
        "_integration_commands",
        lambda: (("postgres_redis_integration", ("tests/pg.py",), 10.0),
                 ("kafka_integration", ("tests/kafka.py",), 10.0)),
    )
    monkeypatch.setattr(
        gate,
        "_run",
        lambda argv, **kwargs: {
            "argv": list(argv),
            "cwd": "algorithm-scheduling-platform",
            "returncode": 0,
            "elapsed_seconds": 1.0,
            "output_tail": "1 passed",
        },
    )
    monkeypatch.setattr(
        gate,
        "_run_integration_layer",
        lambda **kwargs: {
            "argv": list(kwargs["targets"]),
            "cwd": "algorithm-scheduling-platform",
            "returncode": 0,
            "elapsed_seconds": 1.0,
            "output_tail": "1 passed",
            "junit": {"tests": 1, "failures": 0, "errors": 0, "skipped": 0},
        },
    )

    first = gate.run_gate(release_root=release_root, expected_sha=git_sha)
    second = gate.run_gate(release_root=release_root, expected_sha=git_sha)

    assert first == second
    path = release_root / gate.EVIDENCE_PATH
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["git_sha"] == git_sha
    assert document["layers"]["operator_contract"] == {
        "status": "等待同 release 证据",
        "required_evidence": "24 实例注册与八算子 Smoke",
    }


@pytest.mark.parametrize(
    ("attributes", "message"),
    (
        ({"tests": "0", "failures": "0", "errors": "0", "skipped": "0"}, "没有实际执行"),
        ({"tests": "2", "failures": "0", "errors": "0", "skipped": "1"}, "零失败、零错误、零跳过"),
        ({"tests": "2", "failures": "1", "errors": "0", "skipped": "0"}, "零失败、零错误、零跳过"),
    ),
)
def test_junit_gate_rejects_empty_skipped_or_failed_layers(
    tmp_path: Path,
    attributes: dict[str, str],
    message: str,
) -> None:
    junit = tmp_path / "layer.xml"
    rendered = " ".join(f'{name}="{value}"' for name, value in attributes.items())
    junit.write_text(f"<testsuite {rendered}></testsuite>", encoding="utf-8")

    with pytest.raises(RuntimeError, match=message):
        gate._junit_counts(junit, layer="integration")


def test_junit_gate_accepts_only_executed_zero_skip_layer(tmp_path: Path) -> None:
    junit = tmp_path / "layer.xml"
    junit.write_text(
        '<testsuite tests="3" failures="0" errors="0" skipped="0"></testsuite>',
        encoding="utf-8",
    )

    assert gate._junit_counts(junit, layer="integration") == {
        "tests": 3,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
    }


def test_junit_gate_aggregates_pytest_testsuites_root(tmp_path: Path) -> None:
    junit = tmp_path / "layer.xml"
    junit.write_text(
        "<testsuites>"
        '<testsuite tests="2" failures="0" errors="0" skipped="0"></testsuite>'
        '<testsuite tests="3" failures="0" errors="0" skipped="0"></testsuite>'
        "</testsuites>",
        encoding="utf-8",
    )

    assert gate._junit_counts(junit, layer="integration") == {
        "tests": 5,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
    }


def test_junit_gate_rejects_skipped_child_suite(tmp_path: Path) -> None:
    junit = tmp_path / "layer.xml"
    junit.write_text(
        "<testsuites>"
        '<testsuite tests="2" failures="0" errors="0" skipped="0"></testsuite>'
        '<testsuite tests="1" failures="0" errors="0" skipped="1"></testsuite>'
        "</testsuites>",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="零失败、零错误、零跳过"):
        gate._junit_counts(junit, layer="integration")


def test_junit_gate_rejects_partial_testsuites_summary(tmp_path: Path) -> None:
    junit = tmp_path / "layer.xml"
    junit.write_text(
        '<testsuites tests="1">'
        '<testsuite tests="1" failures="0" errors="0" skipped="0"></testsuite>'
        "</testsuites>",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="汇总字段不完整"):
        gate._junit_counts(junit, layer="integration")
