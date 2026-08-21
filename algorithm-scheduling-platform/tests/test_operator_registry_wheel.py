from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import textwrap
import zipfile
from base64 import urlsafe_b64encode
from collections.abc import Callable
from pathlib import Path

import pytest

from scripts import build_and_stage_operator_registry_wheel as wheel_pipeline
from scripts import stage_operator_registry_wheel
from scripts.build_and_stage_operator_registry_wheel import (
    EXPECTED_METADATA_NAME,
    EXPECTED_PACKAGE_MODULES,
    EXPECTED_REQUIRES_PYTHON,
    EXPECTED_RUNTIME_REQUIREMENTS,
    EXPECTED_VERSION,
    EXPECTED_WHEEL_NAME,
    TARGET_PROJECTS,
    WheelBuildError,
    build_and_stage_registry_wheel,
)

PACKAGE_ROOT = (
    Path(__file__).resolve().parents[1] / "packages" / "operator_registry_client"
)
EXPECTED_SOURCE_FILES = (
    "README.md",
    "__init__.py",
    "client.py",
    "config.py",
    "lifecycle.py",
    "logging.py",
    "ops.py",
    "pyproject.toml",
    "runtime.py",
    "validation.py",
)
EXPECTED_PACKAGE_MEMBERS = {
    f"packages/operator_registry_client/{name}"
    for name in EXPECTED_SOURCE_FILES
    if name.endswith(".py")
}
DIST_INFO = "algorithm_operator_registry_client-0.2.0.dist-info"
EXPECTED_DIST_INFO_MEMBERS = {
    f"{DIST_INFO}/METADATA",
    f"{DIST_INFO}/WHEEL",
    f"{DIST_INFO}/top_level.txt",
    f"{DIST_INFO}/RECORD",
}


def _write_fake_wheel(
    wheelhouse: Path,
    *,
    filename: str = EXPECTED_WHEEL_NAME,
    name: str = EXPECTED_METADATA_NAME,
    version: str = EXPECTED_VERSION,
    requires_python: str = EXPECTED_REQUIRES_PYTHON,
    extra_member: str | None = None,
    requirements: tuple[str, ...] = tuple(sorted(EXPECTED_RUNTIME_REQUIREMENTS)),
    omit_record_for: str | None = None,
    corrupt_record_hash_for: str | None = None,
) -> Path:
    wheel = wheelhouse / filename
    metadata = "".join(
        (
            "Metadata-Version: 2.4\n",
            f"Name: {name}\n",
            f"Version: {version}\n",
            f"Requires-Python: {requires_python}\n",
            *(f"Requires-Dist: {requirement}\n" for requirement in requirements),
            "\n",
        )
    )
    members = {
        **{name: b"" for name in EXPECTED_PACKAGE_MEMBERS},
        f"{DIST_INFO}/METADATA": metadata.encode(),
        f"{DIST_INFO}/WHEEL": b"Wheel-Version: 1.0\nTag: py3-none-any\n",
        f"{DIST_INFO}/top_level.txt": b"packages\n",
    }
    if extra_member is not None:
        members[extra_member] = b"forbidden"
    record_lines = []
    for member_name, content in sorted(members.items()):
        if member_name == omit_record_for:
            continue
        digest = urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
        if member_name == corrupt_record_hash_for:
            digest = "invalid-record-hash"
        record_lines.append(
            f"{member_name},sha256={digest},{len(content)}\n"
        )
    record_lines.append(f"{DIST_INFO}/RECORD,,\n")
    members[f"{DIST_INFO}/RECORD"] = "".join(record_lines).encode()
    with zipfile.ZipFile(wheel, "w") as archive:
        for member_name, content in members.items():
            archive.writestr(member_name, content)
    return wheel


def _fake_builder(**wheel_kwargs: str) -> Callable[[Path, Path], None]:
    def build(_package_root: Path, wheelhouse: Path) -> None:
        _write_fake_wheel(wheelhouse, **wheel_kwargs)

    return build


def _pipeline_paths(root: Path) -> tuple[Path, Path, Path]:
    package_root = root / "package"
    dist_dir = package_root / "dist"
    workspace_root = root / "workspace"
    package_root.mkdir(parents=True)
    for relative_path in EXPECTED_SOURCE_FILES:
        (package_root / relative_path).write_text(relative_path, encoding="utf-8")
    return package_root, dist_dir, workspace_root


def _run_pipeline(
    root: Path,
    *,
    builder: Callable[[Path, Path], None],
    replace: Callable[[str | os.PathLike[str], str | os.PathLike[str]], None]
    | None = None,
):
    package_root, dist_dir, workspace_root = _pipeline_paths(root)
    kwargs = {}
    if replace is not None:
        kwargs["replace"] = replace
    result = build_and_stage_registry_wheel(
        package_root=package_root,
        dist_dir=dist_dir,
        workspace_root=workspace_root,
        target_projects=TARGET_PROJECTS,
        builder=builder,
        source_files=EXPECTED_SOURCE_FILES,
        **kwargs,
    )
    return result, dist_dir, workspace_root


def test_registry_client_real_pip_build_is_validated_and_staged(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    workspace_root = tmp_path / "workspace"

    result = build_and_stage_registry_wheel(
        package_root=PACKAGE_ROOT,
        dist_dir=dist_dir,
        workspace_root=workspace_root,
        target_projects=TARGET_PROJECTS,
    )

    assert result.dist_path == dist_dir / EXPECTED_WHEEL_NAME
    assert result.sha256 == hashlib.sha256(result.dist_path.read_bytes()).hexdigest()
    assert len(result.staged_paths) == 8
    assert {path.read_bytes() for path in result.staged_paths} == {
        result.dist_path.read_bytes()
    }
    with zipfile.ZipFile(result.dist_path) as archive:
        assert set(archive.namelist()) == (
            EXPECTED_PACKAGE_MEMBERS | EXPECTED_DIST_INFO_MEMBERS
        )


def test_builder_receives_only_tracked_allowlisted_files_in_a_clean_tree(
    tmp_path: Path,
) -> None:
    package_root, dist_dir, workspace_root = _pipeline_paths(tmp_path)
    (package_root / "__pycache__").mkdir()
    (package_root / "__pycache__/runtime.cpython-312.pyc").write_bytes(b"bytecode")
    (package_root / "admin.py").write_text("SECRET = True\n", encoding="utf-8")
    observed_source: Path | None = None

    def inspect_clean_source(clean_source: Path, wheelhouse: Path) -> None:
        nonlocal observed_source
        observed_source = clean_source
        assert clean_source != package_root
        assert {
            path.relative_to(clean_source).as_posix()
            for path in clean_source.rglob("*")
            if path.is_file()
        } == set(EXPECTED_SOURCE_FILES)
        _write_fake_wheel(wheelhouse)

    build_and_stage_registry_wheel(
        package_root=package_root,
        dist_dir=dist_dir,
        workspace_root=workspace_root,
        target_projects=TARGET_PROJECTS,
        builder=inspect_clean_source,
        source_files=EXPECTED_SOURCE_FILES,
    )

    assert observed_source is not None
    assert not observed_source.exists()


def test_default_source_allowlist_comes_from_tracked_package_files() -> None:
    assert wheel_pipeline._tracked_source_files(PACKAGE_ROOT) == EXPECTED_SOURCE_FILES
    assert EXPECTED_PACKAGE_MODULES == (
        "__init__.py",
        "client.py",
        "config.py",
        "lifecycle.py",
        "logging.py",
        "ops.py",
        "runtime.py",
        "validation.py",
    )


def test_source_contract_rejects_a_sixth_tracked_python_module(tmp_path: Path) -> None:
    package_root, dist_dir, workspace_root = _pipeline_paths(tmp_path)
    (package_root / "admin.py").write_text("SECRET = True\n", encoding="utf-8")

    with pytest.raises(WheelBuildError, match="源码合同不匹配.*admin.py"):
        build_and_stage_registry_wheel(
            package_root=package_root,
            dist_dir=dist_dir,
            workspace_root=workspace_root,
            target_projects=TARGET_PROJECTS,
            builder=_fake_builder(),
            source_files=(*EXPECTED_SOURCE_FILES, "admin.py"),
        )


def test_tracked_source_allowlist_rejects_symlinks_and_non_package_files(
    tmp_path: Path,
) -> None:
    package_root, dist_dir, workspace_root = _pipeline_paths(tmp_path)
    (package_root / "linked.py").symlink_to(package_root / "client.py")

    with pytest.raises(WheelBuildError, match="符号链接|不是普通文件"):
        build_and_stage_registry_wheel(
            package_root=package_root,
            dist_dir=dist_dir,
            workspace_root=workspace_root,
            target_projects=TARGET_PROJECTS,
            builder=_fake_builder(),
            source_files=(*EXPECTED_SOURCE_FILES, "linked.py"),
        )

    (package_root / "linked.py").unlink()
    (package_root / "admin.json").write_text("{}", encoding="utf-8")
    with pytest.raises(WheelBuildError, match="不允许的 tracked 源文件"):
        build_and_stage_registry_wheel(
            package_root=package_root,
            dist_dir=dist_dir,
            workspace_root=workspace_root,
            target_projects=TARGET_PROJECTS,
            builder=_fake_builder(),
            source_files=(*EXPECTED_SOURCE_FILES, "admin.json"),
        )


def test_real_build_ignores_source_bytecode_and_untracked_python(tmp_path: Path) -> None:
    first_dist = tmp_path / "first-dist"
    second_dist = tmp_path / "second-dist"
    first = build_and_stage_registry_wheel(
        package_root=PACKAGE_ROOT,
        dist_dir=first_dist,
        workspace_root=tmp_path / "first-workspace",
        target_projects=TARGET_PROJECTS,
    )
    subprocess.run(
        [sys.executable, "-m", "compileall", "-q", str(PACKAGE_ROOT)],
        check=True,
    )
    unrelated = PACKAGE_ROOT / "unrelated_internal_admin.py"
    unrelated.write_text("SHOULD_NOT_SHIP = True\n", encoding="utf-8")
    try:
        second = build_and_stage_registry_wheel(
            package_root=PACKAGE_ROOT,
            dist_dir=second_dist,
            workspace_root=tmp_path / "second-workspace",
            target_projects=TARGET_PROJECTS,
        )
    finally:
        unrelated.unlink()

    assert first.sha256 == second.sha256
    with zipfile.ZipFile(first.dist_path) as first_archive, zipfile.ZipFile(
        second.dist_path
    ) as second_archive:
        assert first_archive.namelist() == second_archive.namelist()
        assert not any(
            "__pycache__" in name or name.endswith(".pyc")
            for name in first_archive.namelist()
        )


@pytest.mark.parametrize(
    "script_name",
    (
        "build_and_stage_operator_registry_wheel.py",
        "stage_operator_registry_wheel.py",
    ),
)
def test_script_entrypoints_run_without_editable_install(
    tmp_path: Path,
    script_name: str,
) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / script_name
    environment = os.environ.copy()
    environment["PYTHONPATH"] = ""

    completed = subprocess.run(
        [sys.executable, "-S", str(script), "--help"],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "registry wheel" in completed.stdout


def test_real_builder_disables_indexes_and_sets_a_reproducible_epoch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs) -> None:
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        wheelhouse = Path(command[command.index("--wheel-dir") + 1])
        _write_fake_wheel(wheelhouse)

    monkeypatch.setattr(wheel_pipeline.subprocess, "run", fake_run)

    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    wheel_pipeline._build_wheel(tmp_path, wheelhouse)

    command = captured["command"]
    environment = captured["environment"]
    assert "--no-deps" in command
    assert "--no-build-isolation" in command
    assert "--no-index" in command
    assert environment["SOURCE_DATE_EPOCH"] == "315532800"


def test_build_rejects_an_empty_wheelhouse(tmp_path: Path) -> None:
    def build_nothing(_package_root: Path, _wheelhouse: Path) -> None:
        return None

    with pytest.raises(WheelBuildError, match="恰好生成 1 个 wheel，实际为 0"):
        _run_pipeline(tmp_path, builder=build_nothing)


def test_build_rejects_multiple_wheels(tmp_path: Path) -> None:
    def build_two(_package_root: Path, wheelhouse: Path) -> None:
        _write_fake_wheel(wheelhouse)
        _write_fake_wheel(wheelhouse, filename="unrelated-1.0.0-py3-none-any.whl")

    with pytest.raises(WheelBuildError, match="恰好生成 1 个 wheel，实际为 2"):
        _run_pipeline(tmp_path, builder=build_two)


def test_build_rejects_the_wrong_wheel_filename(tmp_path: Path) -> None:
    builder = _fake_builder(filename="registry_client-0.1.0-py3-none-any.whl")

    with pytest.raises(WheelBuildError, match="wheel 文件名不符合固定制品名"):
        _run_pipeline(tmp_path, builder=builder)


def test_publish_rejects_unexpected_existing_dist_wheels_without_deleting_them(
    tmp_path: Path,
) -> None:
    package_root, dist_dir, workspace_root = _pipeline_paths(tmp_path)
    dist_dir.mkdir()
    unexpected = dist_dir / "algorithm_operator_registry_client-0.0.9-py3-none-any.whl"
    unexpected.write_bytes(b"old-release")

    with pytest.raises(WheelBuildError, match="正式 dist 含有非预期 wheel"):
        build_and_stage_registry_wheel(
            package_root=package_root,
            dist_dir=dist_dir,
            workspace_root=workspace_root,
            target_projects=TARGET_PROJECTS,
            builder=_fake_builder(),
        )

    assert unexpected.read_bytes() == b"old-release"


@pytest.mark.parametrize(
    ("wheel_kwargs", "message"),
    (
        ({"name": "wrong-name"}, "Name 不匹配"),
        ({"version": "9.9.9"}, "Version 不匹配"),
        ({"requires_python": ">=3.11"}, "Requires-Python 不匹配"),
    ),
)
def test_build_rejects_wrong_metadata(
    tmp_path: Path,
    wheel_kwargs: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(WheelBuildError, match=message):
        _run_pipeline(tmp_path, builder=_fake_builder(**wheel_kwargs))


def test_build_rejects_changed_runtime_dependencies(tmp_path: Path) -> None:
    with pytest.raises(WheelBuildError, match="Requires-Dist 不匹配"):
        _run_pipeline(
            tmp_path,
            builder=_fake_builder(requirements=("fastapi>=0.109",)),
        )


@pytest.mark.parametrize(
    ("wheel_kwargs", "message"),
    (
        (
            {"omit_record_for": "packages/operator_registry_client/runtime.py"},
            "RECORD 成员集合不匹配",
        ),
        (
            {"corrupt_record_hash_for": "packages/operator_registry_client/runtime.py"},
            "RECORD hash 不匹配",
        ),
    ),
)
def test_build_rejects_incomplete_or_invalid_record(
    tmp_path: Path,
    wheel_kwargs: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(WheelBuildError, match=message):
        _run_pipeline(tmp_path, builder=_fake_builder(**wheel_kwargs))


@pytest.mark.parametrize(
    "extra_member",
    (
        "packages/platform_common/config.py",
        "control_service/app/main.py",
        "packages/operator_registry_client/server.key",
        "packages/operator_registry_client/admin.py",
    ),
)
def test_build_rejects_platform_or_secret_files(
    tmp_path: Path,
    extra_member: str,
) -> None:
    with pytest.raises(WheelBuildError, match="包含禁止发布的文件"):
        _run_pipeline(
            tmp_path,
            builder=_fake_builder(extra_member=extra_member),
        )


def test_builder_failure_preserves_existing_dist_and_targets(tmp_path: Path) -> None:
    package_root, dist_dir, workspace_root = _pipeline_paths(tmp_path)
    existing_paths = [dist_dir / EXPECTED_WHEEL_NAME]
    existing_paths.extend(
        workspace_root / project / "wheel" / EXPECTED_WHEEL_NAME
        for project in TARGET_PROJECTS
    )
    for path in existing_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"old-wheel")

    def fail_build(_package_root: Path, _wheelhouse: Path) -> None:
        raise RuntimeError("builder exploded")

    with pytest.raises(RuntimeError, match="builder exploded"):
        build_and_stage_registry_wheel(
            package_root=package_root,
            dist_dir=dist_dir,
            workspace_root=workspace_root,
            target_projects=TARGET_PROJECTS,
            builder=fail_build,
            source_files=EXPECTED_SOURCE_FILES,
        )

    assert {path.read_bytes() for path in existing_paths} == {b"old-wheel"}


def test_source_change_during_build_is_rejected_before_publish(tmp_path: Path) -> None:
    package_root, dist_dir, workspace_root = _pipeline_paths(tmp_path)
    existing_dist = dist_dir / EXPECTED_WHEEL_NAME
    existing_dist.parent.mkdir()
    existing_dist.write_bytes(b"old-wheel")

    def mutate_after_build(_clean_source: Path, wheelhouse: Path) -> None:
        _write_fake_wheel(wheelhouse)
        (package_root / "runtime.py").write_text("changed", encoding="utf-8")

    with pytest.raises(WheelBuildError, match="构建期间 tracked 源码发生变化"):
        build_and_stage_registry_wheel(
            package_root=package_root,
            dist_dir=dist_dir,
            workspace_root=workspace_root,
            target_projects=TARGET_PROJECTS,
            builder=mutate_after_build,
            source_files=EXPECTED_SOURCE_FILES,
        )

    assert existing_dist.read_bytes() == b"old-wheel"


def test_real_git_index_rejects_a_new_tracked_python_module(tmp_path: Path) -> None:
    package_root, dist_dir, workspace_root = _pipeline_paths(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=package_root, check=True)
    subprocess.run(["git", "add", "."], cwd=package_root, check=True)
    (package_root / "admin.py").write_text("SECRET = True\n", encoding="utf-8")
    subprocess.run(["git", "add", "admin.py"], cwd=package_root, check=True)

    with pytest.raises(WheelBuildError, match="源码合同不匹配.*admin.py"):
        build_and_stage_registry_wheel(
            package_root=package_root,
            dist_dir=dist_dir,
            workspace_root=workspace_root,
            target_projects=TARGET_PROJECTS,
            builder=_fake_builder(),
        )


def test_publication_lock_serializes_processes_and_is_private(tmp_path: Path) -> None:
    lock_path = tmp_path / "registry-wheel.lock"
    log_path = tmp_path / "critical.log"
    script = textwrap.dedent(
        f"""
        import time
        from pathlib import Path
        from scripts.build_and_stage_operator_registry_wheel import _publication_lock
        lock_path = Path({str(lock_path)!r})
        log_path = Path({str(log_path)!r})
        with _publication_lock(lock_path):
            with log_path.open("a", encoding="utf-8") as stream:
                stream.write("enter\\n")
                stream.flush()
            time.sleep(0.2)
            with log_path.open("a", encoding="utf-8") as stream:
                stream.write("exit\\n")
        """
    )
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    processes = [
        subprocess.Popen([sys.executable, "-c", script], env=environment)
        for _ in range(2)
    ]

    assert [process.wait(timeout=5) for process in processes] == [0, 0]
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        "enter",
        "exit",
        "enter",
        "exit",
    ]
    assert lock_path.stat().st_mode & 0o777 == 0o600


def test_two_process_publications_do_not_mix_artifacts(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    destinations = [tmp_path / f"target-{index}.whl" for index in range(9)]
    for destination in destinations:
        destination.write_bytes(b"old-wheel")
    log_path = tmp_path / "publish.log"
    script = textwrap.dedent(
        f"""
        import hashlib
        import sys
        import time
        from pathlib import Path
        from scripts.build_and_stage_operator_registry_wheel import (
            _publication_lock, _publish_artifact_transaction, _recover_transaction,
        )
        artifact = Path(sys.argv[1])
        label = sys.argv[2]
        dist_dir = Path({str(dist_dir)!r})
        destinations = tuple(Path(path) for path in {[str(path) for path in destinations]!r})
        entered = False
        def record_replace(_destination):
            global entered
            if not entered:
                entered = True
                with Path({str(log_path)!r}).open("a", encoding="utf-8") as stream:
                    stream.write(label + ":enter\\n")
                time.sleep(0.1)
        with _publication_lock(dist_dir / ".operator-registry-wheel.lock"):
            _recover_transaction(dist_dir)
            _publish_artifact_transaction(
                artifact,
                destinations,
                dist_dir=dist_dir,
                expected_hash=hashlib.sha256(artifact.read_bytes()).hexdigest(),
                after_replace=record_replace,
            )
            with Path({str(log_path)!r}).open("a", encoding="utf-8") as stream:
                stream.write(label + ":exit\\n")
        """
    )
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    artifacts = []
    for label in ("A", "B"):
        artifact = tmp_path / f"{label}.whl"
        artifact.write_bytes(f"wheel-{label}".encode())
        artifacts.append((artifact, label))
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(artifact), label],
            env=environment,
        )
        for artifact, label in artifacts
    ]

    assert [process.wait(timeout=10) for process in processes] == [0, 0]
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert lines in (
        ["A:enter", "A:exit", "B:enter", "B:exit"],
        ["B:enter", "B:exit", "A:enter", "A:exit"],
    )
    assert {path.read_bytes() for path in destinations} in ({b"wheel-A"}, {b"wheel-B"})


def test_interrupted_publication_is_rolled_back_on_next_lock_holder(
    tmp_path: Path,
) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    artifact = tmp_path / "new.whl"
    artifact.write_bytes(b"new-wheel")
    destinations = [tmp_path / f"target-{index}.whl" for index in range(9)]
    for destination in destinations:
        destination.write_bytes(b"old-wheel")
    script = textwrap.dedent(
        f"""
        import os
        from pathlib import Path
        from scripts.build_and_stage_operator_registry_wheel import (
            _publication_lock, _publish_artifact_transaction, _recover_transaction,
        )
        dist_dir = Path({str(dist_dir)!r})
        destinations = {[str(path) for path in destinations]!r}
        replacements = 0
        def crash_after_four(_destination):
            global replacements
            replacements += 1
            if replacements == 4:
                os._exit(73)
        with _publication_lock(dist_dir / ".operator-registry-wheel.lock"):
            _recover_transaction(dist_dir)
            _publish_artifact_transaction(
                Path({str(artifact)!r}),
                tuple(Path(path) for path in destinations),
                dist_dir=dist_dir,
                expected_hash={hashlib.sha256(b"new-wheel").hexdigest()!r},
                after_replace=crash_after_four,
            )
        """
    )
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}

    crashed = subprocess.run([sys.executable, "-c", script], env=environment, check=False)
    assert crashed.returncode == 73
    assert len({path.read_bytes() for path in destinations}) > 1

    with wheel_pipeline._publication_lock(dist_dir / ".operator-registry-wheel.lock"):
        wheel_pipeline._recover_transaction(dist_dir)

    assert {path.read_bytes() for path in destinations} == {b"old-wheel"}
    assert not (dist_dir / ".operator-registry-wheel.transaction.json").exists()
    assert not list(tmp_path.rglob("*.registry-wheel-*.tmp"))


def test_cleanup_failure_keeps_committed_journal_for_retry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    temporary = tmp_path / ".target.whl.registry-wheel-0.new.tmp"
    temporary.write_bytes(b"new")
    journal = {
        "version": 1,
        "phase": "committed",
        "new_hash": hashlib.sha256(b"new").hexdigest(),
        "targets": [
            {
                "destination": str(tmp_path / "target.whl"),
                "temporary": str(temporary),
                "backup": None,
                "old_exists": False,
                "old_hash": None,
                "prepared": True,
                "published": True,
            }
        ],
    }
    wheel_pipeline._write_journal(dist_dir, journal)
    original_unlink = Path.unlink

    def fail_selected_unlink(path: Path, *args, **kwargs) -> None:
        if path == temporary:
            raise OSError("cleanup failed")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_selected_unlink)
    with pytest.raises(WheelBuildError, match="临时文件清理失败"):
        wheel_pipeline._recover_transaction(dist_dir)

    journal_path = dist_dir / ".operator-registry-wheel.transaction.json"
    assert journal_path.exists()
    assert temporary.exists()

    monkeypatch.setattr(Path, "unlink", original_unlink)
    wheel_pipeline._recover_transaction(dist_dir)
    assert not journal_path.exists()
    assert not temporary.exists()


@pytest.mark.parametrize("failure_index", range(1, 10))
def test_each_publication_replace_failure_rolls_back_all_targets(
    tmp_path: Path,
    failure_index: int,
) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    artifact = tmp_path / "new.whl"
    artifact.write_bytes(b"new-wheel")
    destinations = tuple(tmp_path / f"target-{index}.whl" for index in range(9))
    for destination in destinations:
        destination.write_bytes(b"old-wheel")
    calls = 0

    def fail_selected_replace(source, destination) -> None:
        nonlocal calls
        calls += 1
        if calls == failure_index:
            raise OSError(f"replace {failure_index} failed")
        os.replace(source, destination)

    with pytest.raises(OSError, match=f"replace {failure_index} failed"):
        wheel_pipeline._publish_artifact_transaction(
            artifact,
            destinations,
            dist_dir=dist_dir,
            expected_hash=hashlib.sha256(b"new-wheel").hexdigest(),
            replace=fail_selected_replace,
        )

    assert {path.read_bytes() for path in destinations} == {b"old-wheel"}
    assert not (dist_dir / ".operator-registry-wheel.transaction.json").exists()


@pytest.mark.parametrize("failure_index", range(1, 10))
def test_each_rollback_replace_failure_preserves_recovery_evidence(
    tmp_path: Path,
    failure_index: int,
) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    artifact = tmp_path / "new.whl"
    artifact.write_bytes(b"new-wheel")
    destinations = tuple(tmp_path / f"target-{index}.whl" for index in range(9))
    for destination in destinations:
        destination.write_bytes(b"old-wheel")
    replacements = 0

    def interrupt_after_first(destination: Path) -> None:
        if destination == destinations[0]:
            raise KeyboardInterrupt

    def fail_selected_rollback(source, destination) -> None:
        nonlocal replacements
        replacements += 1
        if replacements == failure_index:
            raise OSError(f"rollback {failure_index} failed")
        os.replace(source, destination)

    with pytest.raises(WheelBuildError, match="事务回滚未完成"):
        wheel_pipeline._publish_artifact_transaction(
            artifact,
            destinations,
            dist_dir=dist_dir,
            expected_hash=hashlib.sha256(b"new-wheel").hexdigest(),
            after_replace=interrupt_after_first,
            recover_replace=fail_selected_rollback,
        )

    journal = dist_dir / ".operator-registry-wheel.transaction.json"
    assert journal.exists()
    assert len(list(tmp_path.rglob("*.backup.tmp"))) == 9

    wheel_pipeline._recover_transaction(dist_dir)
    assert {path.read_bytes() for path in destinations} == {b"old-wheel"}
    assert not journal.exists()


def test_publish_failure_rolls_back_dist_and_all_operator_targets(tmp_path: Path) -> None:
    package_root, dist_dir, workspace_root = _pipeline_paths(tmp_path)
    destinations = [dist_dir / EXPECTED_WHEEL_NAME]
    destinations.extend(
        workspace_root / project / "wheel" / EXPECTED_WHEEL_NAME
        for project in TARGET_PROJECTS
    )
    for destination in destinations:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"old-wheel")

    replacements = 0

    def fail_once_on_fifth_replace(
        source: str | os.PathLike[str],
        destination: str | os.PathLike[str],
    ) -> None:
        nonlocal replacements
        replacements += 1
        if replacements == 5:
            raise OSError("injected target write failure")
        os.replace(source, destination)

    with pytest.raises(OSError, match="injected target write failure"):
        build_and_stage_registry_wheel(
            package_root=package_root,
            dist_dir=dist_dir,
            workspace_root=workspace_root,
            target_projects=TARGET_PROJECTS,
            builder=_fake_builder(),
            replace=fail_once_on_fifth_replace,
            source_files=EXPECTED_SOURCE_FILES,
        )

    assert {destination.read_bytes() for destination in destinations} == {b"old-wheel"}


def test_post_replace_hash_mismatch_rolls_back_every_destination(tmp_path: Path) -> None:
    package_root, dist_dir, workspace_root = _pipeline_paths(tmp_path)
    destinations = [dist_dir / EXPECTED_WHEEL_NAME]
    destinations.extend(
        workspace_root / project / "wheel" / EXPECTED_WHEEL_NAME
        for project in TARGET_PROJECTS
    )
    for destination in destinations:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"old-wheel")

    replacements = 0

    def corrupt_fifth_destination(
        source: str | os.PathLike[str],
        destination: str | os.PathLike[str],
    ) -> None:
        nonlocal replacements
        replacements += 1
        os.replace(source, destination)
        if replacements == 5:
            Path(destination).write_bytes(b"corrupted-wheel")

    with pytest.raises(WheelBuildError, match="发布后.*hash"):
        build_and_stage_registry_wheel(
            package_root=package_root,
            dist_dir=dist_dir,
            workspace_root=workspace_root,
            target_projects=TARGET_PROJECTS,
            builder=_fake_builder(),
            replace=corrupt_fifth_destination,
            source_files=EXPECTED_SOURCE_FILES,
        )

    assert {destination.read_bytes() for destination in destinations} == {b"old-wheel"}


def test_successful_publish_sets_identical_hashes_permissions_and_is_idempotent(
    tmp_path: Path,
) -> None:
    first, dist_dir, workspace_root = _run_pipeline(
        tmp_path,
        builder=_fake_builder(),
    )
    destinations = [first.dist_path, *first.staged_paths]
    first_hashes = {
        hashlib.sha256(destination.read_bytes()).hexdigest()
        for destination in destinations
    }

    second = build_and_stage_registry_wheel(
        package_root=tmp_path / "package",
        dist_dir=dist_dir,
        workspace_root=workspace_root,
        target_projects=TARGET_PROJECTS,
        builder=_fake_builder(),
        source_files=EXPECTED_SOURCE_FILES,
    )

    assert first_hashes == {first.sha256} == {second.sha256}
    assert {
        hashlib.sha256(destination.read_bytes()).hexdigest()
        for destination in [second.dist_path, *second.staged_paths]
    } == {second.sha256}
    assert {
        destination.stat().st_mode & 0o777
        for destination in [second.dist_path, *second.staged_paths]
    } == {0o644}


def test_legacy_stage_entrypoint_delegates_to_build_and_stage(monkeypatch) -> None:
    called = False

    def build_and_stage() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(
        stage_operator_registry_wheel,
        "build_and_stage_registry_wheel",
        build_and_stage,
    )

    stage_operator_registry_wheel.main()

    assert called
