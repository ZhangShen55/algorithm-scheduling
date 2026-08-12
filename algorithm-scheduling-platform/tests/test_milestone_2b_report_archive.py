from __future__ import annotations

import os
import re
import signal
import stat
import subprocess
import time
from pathlib import Path

import pytest

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
PLATFORM_ROOT = WORKSPACE_ROOT / "algorithm-scheduling-platform"
REPORTS_ROOT = PLATFORM_ROOT / "deploy" / "reports"
PREPARE_REPORT_DIRECTORY = (
    PLATFORM_ROOT / "deploy" / "scripts" / "prepare-report-directory"
)
RELEASE_TAG = "v1.0_260812"
GIT_SHA = "a" * 40
EVIDENCE_CATEGORIES = {
    "preflight",
    "container-maintenance",
    "image-build",
    "gpu-instances",
    "registration",
    "smoke",
    "negative",
    "load",
    "recovery",
    "summary",
}


def _run_prepare(
    reports_root: Path,
    restricted_root: Path,
    *extra: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(PREPARE_REPORT_DIRECTORY),
            "--release-tag",
            RELEASE_TAG,
            "--git-sha",
            GIT_SHA,
            "--reports-root",
            str(reports_root),
            "--restricted-root",
            str(restricted_root),
            *extra,
        ],
        cwd=PLATFORM_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _prepare_command(
    reports_root: Path,
    restricted_root: Path,
    manifest: Path,
) -> list[str]:
    return [
        str(PREPARE_REPORT_DIRECTORY),
        "--release-tag",
        RELEASE_TAG,
        "--git-sha",
        GIT_SHA,
        "--reports-root",
        str(reports_root),
        "--restricted-root",
        str(restricted_root),
        "--external-manifest",
        str(manifest),
    ]


def _archived_manifest(restricted_root: Path) -> Path:
    return (
        restricted_root
        / "milestone-2b/releases"
        / RELEASE_TAG
        / GIT_SHA
        / "model-assets.manifest.json"
    )


def _wait_for_path(path: Path, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if path.exists():
            return
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(f"process exited before marker: {stdout=} {stderr=}")
        time.sleep(0.01)
    process.kill()
    process.wait(timeout=5)
    raise AssertionError(f"timed out waiting for {path}")


def _git_check_ignore(path: str) -> bool:
    completed = subprocess.run(
        ["git", "check-ignore", "-q", path],
        cwd=WORKSPACE_ROOT,
        check=False,
    )
    return completed.returncode == 0


def _private_manifest(root: Path, content: str = '{"schema_version": 1}\n') -> Path:
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    manifest = root / "model-assets.manifest.json"
    manifest.write_text(content, encoding="utf-8")
    manifest.chmod(0o600)
    return manifest


def test_empty_clone_keeps_report_directory_structure() -> None:
    expected = {
        "algorithm-scheduling-platform/deploy/reports/.gitkeep",
        "algorithm-scheduling-platform/deploy/reports/README.md",
        "algorithm-scheduling-platform/deploy/reports/milestone-2b/.gitkeep",
    }
    candidates = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", *expected],
        cwd=WORKSPACE_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    assert set(candidates.stdout.splitlines()) == expected
    assert all((WORKSPACE_ROOT / path).is_file() for path in expected)


@pytest.mark.parametrize(
    "relative_path",
    (
        "algorithm-scheduling-platform/deploy/reports/milestone-2b/releases/"
        f"{RELEASE_TAG}/{GIT_SHA}/preflight/preflight.json",
        "algorithm-scheduling-platform/deploy/reports/milestone-2b/releases/"
        f"{RELEASE_TAG}/{GIT_SHA}/image-build/build.log",
        "algorithm-scheduling-platform/deploy/reports/milestone-2b/releases/"
        f"{RELEASE_TAG}/{GIT_SHA}/container-maintenance/containers.snapshot.jsonl",
        "algorithm-scheduling-platform/deploy/reports/milestone-2b/releases/"
        f"{RELEASE_TAG}/{GIT_SHA}/container-maintenance/paused-containers.ledger.jsonl",
        "algorithm-scheduling-platform/deploy/reports/milestone-2b/releases/"
        f"{RELEASE_TAG}/{GIT_SHA}/restricted/model-assets.manifest.json",
        "algorithm-scheduling-platform/deploy/reports/milestone-2b/releases/"
        f"{RELEASE_TAG}/{GIT_SHA}/restricted/secret-metadata.json",
        "algorithm-scheduling-platform/deploy/reports/.prepare-report-directory.tmp",
    ),
)
def test_runtime_evidence_is_ignored(relative_path: str) -> None:
    assert _git_check_ignore(relative_path), relative_path


@pytest.mark.parametrize(
    "relative_path",
    (
        "algorithm-scheduling-platform/deploy/reports/.gitkeep",
        "algorithm-scheduling-platform/deploy/reports/README.md",
        "algorithm-scheduling-platform/deploy/reports/milestone-2b/.gitkeep",
        "algorithm-scheduling-platform/deploy/model-assets.json",
    ),
)
def test_repository_structure_is_not_ignored(relative_path: str) -> None:
    assert not _git_check_ignore(relative_path), relative_path


def test_report_readme_has_no_literal_secret_or_model_digest() -> None:
    content = (REPORTS_ROOT / "README.md").read_text(encoding="utf-8")

    assert "BEGIN PRIVATE KEY" not in content
    assert re.search(r"(?i)(password|密码)\s*[:=]\s*\S+", content) is None
    assert re.search(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", content.lower()) is None


def test_prepare_creates_release_tree_with_private_permissions(tmp_path: Path) -> None:
    reports_root = tmp_path / "reports"
    restricted_root = tmp_path / "restricted"
    reports_root.mkdir()
    restricted_root.mkdir()

    completed = _run_prepare(reports_root, restricted_root)

    assert completed.returncode == 0, completed.stderr
    release_root = reports_root / "milestone-2b/releases" / RELEASE_TAG / GIT_SHA
    assert {path.name for path in release_root.iterdir()} == EVIDENCE_CATEGORIES
    assert stat.S_IMODE(release_root.stat().st_mode) == 0o700
    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o700
        for path in release_root.iterdir()
    )
    restricted_release = restricted_root / "milestone-2b/releases" / RELEASE_TAG / GIT_SHA
    assert stat.S_IMODE(restricted_release.stat().st_mode) == 0o700


def test_prepare_is_idempotent_and_preserves_existing_evidence(tmp_path: Path) -> None:
    reports_root = tmp_path / "reports"
    restricted_root = tmp_path / "restricted"
    reports_root.mkdir()
    restricted_root.mkdir()
    first = _run_prepare(reports_root, restricted_root)
    assert first.returncode == 0, first.stderr
    evidence = (
        reports_root
        / "milestone-2b/releases"
        / RELEASE_TAG
        / GIT_SHA
        / "smoke/result.json"
    )
    evidence.write_text('{"preserve": true}\n', encoding="utf-8")

    second = _run_prepare(reports_root, restricted_root)

    assert second.returncode == 0, second.stderr
    assert evidence.read_text(encoding="utf-8") == '{"preserve": true}\n'


@pytest.mark.parametrize("invalid_tag", ("../escape", "nested/tag", ".", ""))
def test_prepare_rejects_release_tag_path_traversal(
    tmp_path: Path, invalid_tag: str
) -> None:
    reports_root = tmp_path / "reports"
    restricted_root = tmp_path / "restricted"
    reports_root.mkdir()
    restricted_root.mkdir()

    completed = subprocess.run(
        [
            str(PREPARE_REPORT_DIRECTORY),
            "--release-tag",
            invalid_tag,
            "--git-sha",
            GIT_SHA,
            "--reports-root",
            str(reports_root),
            "--restricted-root",
            str(restricted_root),
        ],
        cwd=PLATFORM_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0


def test_prepare_rejects_symlink_and_non_directory_roots(tmp_path: Path) -> None:
    real_reports = tmp_path / "real-reports"
    real_reports.mkdir()
    linked_reports = tmp_path / "linked-reports"
    linked_reports.symlink_to(real_reports, target_is_directory=True)
    restricted_root = tmp_path / "restricted"
    restricted_root.mkdir()

    symlink_result = _run_prepare(linked_reports, restricted_root)
    non_directory = tmp_path / "not-a-directory"
    non_directory.write_text("preserve", encoding="utf-8")
    file_result = _run_prepare(non_directory, restricted_root)

    assert symlink_result.returncode != 0
    assert file_result.returncode != 0
    assert non_directory.read_text(encoding="utf-8") == "preserve"


def test_prepare_rejects_symlink_inside_release_path(tmp_path: Path) -> None:
    reports_root = tmp_path / "reports"
    restricted_root = tmp_path / "restricted"
    reports_root.mkdir()
    restricted_root.mkdir()
    releases = reports_root / "milestone-2b/releases"
    releases.mkdir(parents=True)
    external = tmp_path / "external"
    external.mkdir()
    (releases / RELEASE_TAG).symlink_to(external, target_is_directory=True)

    completed = _run_prepare(reports_root, restricted_root)

    assert completed.returncode != 0
    assert list(external.iterdir()) == []


@pytest.mark.parametrize("root_name", ("reports", "restricted"))
def test_prepare_rejects_symlink_in_root_parent_path(
    tmp_path: Path, root_name: str
) -> None:
    actual_parent = tmp_path / "actual-parent"
    actual_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(actual_parent, target_is_directory=True)
    unsafe_root = linked_parent / root_name
    unsafe_root.mkdir()
    reports_root = unsafe_root if root_name == "reports" else tmp_path / "reports"
    restricted_root = (
        unsafe_root if root_name == "restricted" else tmp_path / "restricted"
    )
    reports_root.mkdir(exist_ok=True)
    restricted_root.mkdir(exist_ok=True)

    completed = _run_prepare(reports_root, restricted_root)

    assert completed.returncode != 0
    assert not (unsafe_root / "milestone-2b").exists()


@pytest.mark.parametrize("root_name", ("reports", "restricted"))
def test_prepare_rejects_parent_traversal_in_root_argument(
    tmp_path: Path, root_name: str
) -> None:
    hop = tmp_path / "hop"
    hop.mkdir()
    reports_root = tmp_path / "reports"
    restricted_root = tmp_path / "restricted"
    reports_root.mkdir()
    restricted_root.mkdir()
    unsafe = hop / ".." / root_name

    completed = _run_prepare(
        unsafe if root_name == "reports" else reports_root,
        unsafe if root_name == "restricted" else restricted_root,
    )

    assert completed.returncode != 0
    assert not (reports_root / "milestone-2b").exists()
    assert not (restricted_root / "milestone-2b").exists()


def test_prepare_archives_external_manifest_as_private_file(tmp_path: Path) -> None:
    reports_root = tmp_path / "reports"
    restricted_root = tmp_path / "restricted"
    reports_root.mkdir()
    restricted_root.mkdir()
    manifest = _private_manifest(tmp_path / "asset-source")

    completed = _run_prepare(
        reports_root,
        restricted_root,
        "--external-manifest",
        str(manifest),
    )

    assert completed.returncode == 0, completed.stderr
    archived = (
        restricted_root
        / "milestone-2b/releases"
        / RELEASE_TAG
        / GIT_SHA
        / "model-assets.manifest.json"
    )
    assert archived.read_bytes() == manifest.read_bytes()
    assert stat.S_IMODE(archived.stat().st_mode) == 0o600
    assert "schema_version" not in completed.stdout


def test_prepare_does_not_overwrite_a_different_archived_manifest(
    tmp_path: Path,
) -> None:
    reports_root = tmp_path / "reports"
    restricted_root = tmp_path / "restricted"
    reports_root.mkdir()
    restricted_root.mkdir()
    original = _private_manifest(
        tmp_path / "original-source", '{"release": "first"}\n'
    )
    replacement = _private_manifest(
        tmp_path / "replacement-source", '{"release": "second"}\n'
    )
    first = _run_prepare(
        reports_root,
        restricted_root,
        "--external-manifest",
        str(original),
    )
    assert first.returncode == 0, first.stderr

    second = _run_prepare(
        reports_root,
        restricted_root,
        "--external-manifest",
        str(replacement),
    )

    assert second.returncode != 0
    archived = (
        restricted_root
        / "milestone-2b/releases"
        / RELEASE_TAG
        / GIT_SHA
        / "model-assets.manifest.json"
    )
    assert archived.read_bytes() == original.read_bytes()


def test_manifest_archive_recovers_after_process_is_killed_during_write(
    tmp_path: Path,
) -> None:
    reports_root = tmp_path / "reports"
    restricted_root = tmp_path / "restricted"
    reports_root.mkdir()
    restricted_root.mkdir()
    payload = '{"payload": "' + ("x" * 2_000_000) + '"}\n'
    manifest = _private_manifest(tmp_path / "asset-source", payload)
    pause_marker = tmp_path / "paused"
    environment = dict(**os.environ)
    environment["REPORT_ARCHIVE_TEST_PAUSE_AFTER_TEMP_WRITE"] = str(pause_marker)
    process = subprocess.Popen(
        _prepare_command(reports_root, restricted_root, manifest),
        cwd=PLATFORM_ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _wait_for_path(pause_marker, process)

    process.send_signal(signal.SIGKILL)
    process.wait(timeout=5)
    assert process.returncode != 0
    assert not _archived_manifest(restricted_root).exists()

    resumed = _run_prepare(
        reports_root,
        restricted_root,
        "--external-manifest",
        str(manifest),
    )

    assert resumed.returncode == 0, resumed.stderr
    assert _archived_manifest(restricted_root).read_bytes() == manifest.read_bytes()
    release_root = _archived_manifest(restricted_root).parent
    assert not list(release_root.glob(".model-assets.manifest.json.archive-*.tmp"))


def test_concurrent_same_manifest_archives_are_both_idempotent(tmp_path: Path) -> None:
    reports_root = tmp_path / "reports"
    restricted_root = tmp_path / "restricted"
    reports_root.mkdir()
    restricted_root.mkdir()
    manifest = _private_manifest(tmp_path / "asset-source", '{"same": true}\n')
    command = _prepare_command(reports_root, restricted_root, manifest)

    processes = [
        subprocess.Popen(
            command,
            cwd=PLATFORM_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(2)
    ]
    results = [process.communicate(timeout=10) for process in processes]

    assert [process.returncode for process in processes] == [0, 0], results
    assert _archived_manifest(restricted_root).read_bytes() == manifest.read_bytes()


def test_concurrent_different_manifests_have_one_winner(tmp_path: Path) -> None:
    reports_root = tmp_path / "reports"
    restricted_root = tmp_path / "restricted"
    reports_root.mkdir()
    restricted_root.mkdir()
    first = _private_manifest(tmp_path / "first-source", '{"winner": "first"}\n')
    second = _private_manifest(tmp_path / "second-source", '{"winner": "second"}\n')
    processes = [
        subprocess.Popen(
            _prepare_command(reports_root, restricted_root, manifest),
            cwd=PLATFORM_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for manifest in (first, second)
    ]
    results = [process.communicate(timeout=10) for process in processes]

    assert sorted(process.returncode for process in processes) == [0, 2], results
    failed = next(
        result
        for process, result in zip(processes, results, strict=True)
        if process.returncode
    )
    assert "不同" in failed[1]
    assert _archived_manifest(restricted_root).read_bytes() in {
        first.read_bytes(),
        second.read_bytes(),
    }


def test_prepare_rejects_symlink_manifest(tmp_path: Path) -> None:
    reports_root = tmp_path / "reports"
    restricted_root = tmp_path / "restricted"
    reports_root.mkdir()
    restricted_root.mkdir()
    manifest = _private_manifest(tmp_path / "asset-source", "{}\n")
    symlink = tmp_path / "manifest-link.json"
    symlink.symlink_to(manifest)

    completed = _run_prepare(
        reports_root,
        restricted_root,
        "--external-manifest",
        str(symlink),
    )

    assert completed.returncode != 0
    assert not (
        restricted_root
        / "milestone-2b/releases"
        / RELEASE_TAG
        / GIT_SHA
        / "model-assets.manifest.json"
    ).exists()


def test_prepare_rejects_external_manifest_inside_git_worktree(tmp_path: Path) -> None:
    reports_root = tmp_path / "reports"
    restricted_root = tmp_path / "restricted"
    reports_root.mkdir()
    restricted_root.mkdir()
    source_root = PLATFORM_ROOT / "deploy/reports/manifest-source-test"
    manifest = _private_manifest(source_root)

    try:
        completed = _run_prepare(
            reports_root,
            restricted_root,
            "--external-manifest",
            str(manifest),
        )
    finally:
        manifest.unlink()
        source_root.rmdir()

    assert completed.returncode != 0
    assert not (reports_root / "milestone-2b").exists()
    assert not (restricted_root / "milestone-2b").exists()


def test_prepare_rejects_external_manifest_mode_0644(tmp_path: Path) -> None:
    reports_root = tmp_path / "reports"
    restricted_root = tmp_path / "restricted"
    reports_root.mkdir()
    restricted_root.mkdir()
    manifest = _private_manifest(tmp_path / "asset-source")
    manifest.chmod(0o644)

    completed = _run_prepare(
        reports_root,
        restricted_root,
        "--external-manifest",
        str(manifest),
    )

    assert completed.returncode != 0
    assert not (restricted_root / "milestone-2b").exists()


@pytest.mark.parametrize("parent_mode", (0o755, 0o777))
def test_prepare_rejects_non_private_manifest_parent(
    tmp_path: Path, parent_mode: int
) -> None:
    reports_root = tmp_path / "reports"
    restricted_root = tmp_path / "restricted"
    reports_root.mkdir()
    restricted_root.mkdir()
    source_root = tmp_path / "asset-source"
    manifest = _private_manifest(source_root)
    source_root.chmod(parent_mode)

    completed = _run_prepare(
        reports_root,
        restricted_root,
        "--external-manifest",
        str(manifest),
    )

    assert completed.returncode != 0
    assert not (restricted_root / "milestone-2b").exists()


def test_prepare_rejects_symlink_in_manifest_parent_chain(tmp_path: Path) -> None:
    reports_root = tmp_path / "reports"
    restricted_root = tmp_path / "restricted"
    reports_root.mkdir()
    restricted_root.mkdir()
    actual_parent = tmp_path / "actual-parent"
    actual_parent.mkdir(mode=0o700)
    actual_parent.chmod(0o700)
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(actual_parent, target_is_directory=True)
    manifest = linked_parent / "model-assets.manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")
    manifest.chmod(0o600)

    completed = _run_prepare(
        reports_root,
        restricted_root,
        "--external-manifest",
        str(manifest),
    )

    assert completed.returncode != 0
    assert not (restricted_root / "milestone-2b").exists()


def test_prepare_rejects_restricted_archive_inside_git_worktree(
    tmp_path: Path,
) -> None:
    reports_root = tmp_path / "reports"
    reports_root.mkdir()
    restricted_root = PLATFORM_ROOT / "deploy/reports/restricted-test"

    completed = _run_prepare(reports_root, restricted_root)

    assert completed.returncode != 0
    assert not restricted_root.exists()


@pytest.mark.parametrize("marker_kind", ("directory", "file"))
@pytest.mark.parametrize("path_kind", ("source", "restricted"))
def test_prepare_rejects_any_git_worktree_ancestor(
    tmp_path: Path, marker_kind: str, path_kind: str
) -> None:
    git_worktree = tmp_path / "other-worktree"
    git_worktree.mkdir(mode=0o700)
    if marker_kind == "directory":
        (git_worktree / ".git").mkdir()
    else:
        (git_worktree / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    source_root = (
        git_worktree / "asset-source"
        if path_kind == "source"
        else tmp_path / "asset-source"
    )
    manifest = _private_manifest(source_root)
    reports_root = tmp_path / "reports"
    reports_root.mkdir()
    restricted_root = (
        git_worktree / "restricted"
        if path_kind == "restricted"
        else tmp_path / "restricted"
    )
    restricted_root.mkdir(mode=0o700)
    restricted_root.chmod(0o700)

    completed = _run_prepare(
        reports_root,
        restricted_root,
        "--external-manifest",
        str(manifest),
    )

    assert completed.returncode != 0
    assert not (reports_root / "milestone-2b").exists()
    assert not (restricted_root / "milestone-2b").exists()


def test_prepare_script_does_not_contain_destructive_commands() -> None:
    source = PREPARE_REPORT_DIRECTORY.read_text(encoding="utf-8")

    for forbidden in ("rmtree", "docker system prune", "down -v"):
        assert forbidden not in source
    assert "fcntl.flock" in source
    assert "os.O_NOFOLLOW" in source
    assert "0o700" in source
    assert "0o600" in source
