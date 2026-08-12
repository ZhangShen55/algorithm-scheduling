from __future__ import annotations

import hashlib
import os
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from scripts import build_and_stage_operator_registry_wheel as wheel_pipeline
from scripts import stage_operator_registry_wheel
from scripts.build_and_stage_operator_registry_wheel import (
    EXPECTED_METADATA_NAME,
    EXPECTED_REQUIRES_PYTHON,
    EXPECTED_VERSION,
    EXPECTED_WHEEL_NAME,
    TARGET_PROJECTS,
    WheelBuildError,
    build_and_stage_registry_wheel,
)

PACKAGE_ROOT = (
    Path(__file__).resolve().parents[1] / "packages" / "operator_registry_client"
)


def _write_fake_wheel(
    wheelhouse: Path,
    *,
    filename: str = EXPECTED_WHEEL_NAME,
    name: str = EXPECTED_METADATA_NAME,
    version: str = EXPECTED_VERSION,
    requires_python: str = EXPECTED_REQUIRES_PYTHON,
    extra_member: str | None = None,
) -> Path:
    wheel = wheelhouse / filename
    metadata = (
        "Metadata-Version: 2.4\n"
        f"Name: {name}\n"
        f"Version: {version}\n"
        f"Requires-Python: {requires_python}\n"
        "Requires-Dist: fastapi<1,>=0.109\n"
        "Requires-Dist: httpx<1,>=0.25\n"
        "Requires-Dist: pydantic<3,>=2.5\n"
        "\n"
    )
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("packages/operator_registry_client/__init__.py", "")
        archive.writestr(
            "algorithm_operator_registry_client-0.1.0.dist-info/METADATA",
            metadata,
        )
        if extra_member is not None:
            archive.writestr(extra_member, "forbidden")
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


@pytest.mark.parametrize(
    "extra_member",
    (
        "packages/platform_common/config.py",
        "control_service/app/main.py",
        "packages/operator_registry_client/server.key",
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
        )

    assert {path.read_bytes() for path in existing_paths} == {b"old-wheel"}


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

    with pytest.raises(WheelBuildError, match="发布后.*hash 不一致"):
        build_and_stage_registry_wheel(
            package_root=package_root,
            dist_dir=dist_dir,
            workspace_root=workspace_root,
            target_projects=TARGET_PROJECTS,
            builder=_fake_builder(),
            replace=corrupt_fifth_destination,
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
