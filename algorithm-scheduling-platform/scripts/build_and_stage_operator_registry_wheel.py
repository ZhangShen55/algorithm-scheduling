from __future__ import annotations

import argparse
import csv
import email
import hashlib
import io
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from base64 import urlsafe_b64encode
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PLATFORM_ROOT.parent
PACKAGE_ROOT = PLATFORM_ROOT / "packages" / "operator_registry_client"
DIST_DIR = PACKAGE_ROOT / "dist"

EXPECTED_METADATA_NAME = "algorithm-operator-registry-client"
EXPECTED_VERSION = "0.1.0"
EXPECTED_REQUIRES_PYTHON = ">=3.10"
EXPECTED_WHEEL_NAME = "algorithm_operator_registry_client-0.1.0-py3-none-any.whl"
EXPECTED_RUNTIME_REQUIREMENTS = frozenset(
    {
        "fastapi<1,>=0.109",
        "httpx<1,>=0.25",
        "pydantic<3,>=2.5",
    }
)
DIST_INFO_DIRECTORY = "algorithm_operator_registry_client-0.1.0.dist-info"
EXPECTED_DIST_INFO_MEMBERS = frozenset(
    {
        f"{DIST_INFO_DIRECTORY}/METADATA",
        f"{DIST_INFO_DIRECTORY}/WHEEL",
        f"{DIST_INFO_DIRECTORY}/top_level.txt",
        f"{DIST_INFO_DIRECTORY}/RECORD",
    }
)
TARGET_PROJECTS = (
    "asr_offline",
    "asr_online",
    "ppt_slice",
    "ocr",
    "text_analysis",
    "vbas",
    "facerec",
    "screen_det",
)

Builder = Callable[[Path, Path], None]
Replace = Callable[[str | os.PathLike[str], str | os.PathLike[str]], None]


class WheelBuildError(RuntimeError):
    """Raised when the registry wheel output cannot be safely published."""


@dataclass(frozen=True)
class PublishedWheel:
    dist_path: Path
    staged_paths: tuple[Path, ...]
    sha256: str


def _tracked_source_files(package_root: Path) -> tuple[str, ...]:
    completed = subprocess.run(
        ["git", "-C", str(package_root), "ls-files", "-z", "--", "."],
        check=True,
        capture_output=True,
        timeout=10,
    )
    return tuple(
        sorted(
            path.decode("utf-8")
            for path in completed.stdout.split(b"\0")
            if path
        )
    )


def _validate_source_path(relative_path: str) -> PurePosixPath:
    path = PurePosixPath(relative_path)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} or part.startswith(".") for part in path.parts)
        or "\\" in relative_path
    ):
        raise WheelBuildError(f"不允许的 tracked 源文件路径: {relative_path!r}")
    allowed = (
        relative_path in {"README.md", "pyproject.toml"}
        or path.suffix == ".py"
        or path.name == "py.typed"
    )
    if not allowed:
        raise WheelBuildError(f"不允许的 tracked 源文件: {relative_path}")
    return path


def _prepare_clean_source(
    package_root: Path,
    clean_source: Path,
    source_files: Sequence[str],
) -> tuple[str, ...]:
    if not source_files:
        raise WheelBuildError("Git 索引中没有 registry client 源文件")
    clean_source.mkdir()
    validated: list[str] = []
    for relative_path in source_files:
        path = _validate_source_path(relative_path)
        source = package_root.joinpath(*path.parts)
        if source.is_symlink():
            raise WheelBuildError(f"tracked 源文件不得为符号链接: {relative_path}")
        if not source.is_file() or not stat.S_ISREG(source.stat().st_mode):
            raise WheelBuildError(f"tracked 源文件必须是普通文件: {relative_path}")
        destination = clean_source.joinpath(*path.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        destination.chmod(0o644)
        validated.append(path.as_posix())
    required = {"README.md", "pyproject.toml", "__init__.py"}
    missing = required.difference(validated)
    if missing:
        raise WheelBuildError("tracked 源文件缺少必需项: " + ", ".join(sorted(missing)))
    return tuple(sorted(validated))


def _build_wheel(package_root: Path, wheelhouse: Path) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "SOURCE_DATE_EPOCH": "315532800",
        }
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--no-index",
            "--wheel-dir",
            str(wheelhouse),
            str(package_root),
        ],
        check=True,
        cwd=package_root.parent,
        env=environment,
    )


def _expected_wheel_members(source_files: Sequence[str]) -> frozenset[str]:
    package_members = {
        f"packages/operator_registry_client/{relative_path}"
        for relative_path in source_files
        if relative_path.endswith(".py") or PurePosixPath(relative_path).name == "py.typed"
    }
    return frozenset(package_members) | EXPECTED_DIST_INFO_MEMBERS


def _validate_record(archive: zipfile.ZipFile, expected_members: frozenset[str]) -> None:
    record_name = f"{DIST_INFO_DIRECTORY}/RECORD"
    try:
        rows = list(
            csv.reader(
                io.StringIO(archive.read(record_name).decode("utf-8")),
                strict=True,
            )
        )
    except (UnicodeDecodeError, csv.Error) as exc:
        raise WheelBuildError("wheel RECORD 不是有效 UTF-8 CSV") from exc
    if any(len(row) != 3 for row in rows):
        raise WheelBuildError("wheel RECORD 每行必须恰好包含 3 列")
    record_names = [row[0] for row in rows]
    if len(record_names) != len(set(record_names)):
        raise WheelBuildError("wheel RECORD 包含重复成员")
    if set(record_names) != set(expected_members):
        raise WheelBuildError("wheel RECORD 成员集合不匹配")
    for member_name, recorded_hash, recorded_size in rows:
        if member_name == record_name:
            if recorded_hash or recorded_size:
                raise WheelBuildError("wheel RECORD 自身的 hash 和 size 必须为空")
            continue
        content = archive.read(member_name)
        digest = urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
        if recorded_hash != f"sha256={digest}":
            raise WheelBuildError(f"wheel RECORD hash 不匹配: {member_name}")
        if recorded_size != str(len(content)):
            raise WheelBuildError(f"wheel RECORD size 不匹配: {member_name}")


def _validate_wheel(wheelhouse: Path, source_files: Sequence[str]) -> Path:
    artifacts = sorted(path for path in wheelhouse.iterdir() if path.is_file())
    wheels = [path for path in artifacts if path.suffix == ".whl"]
    if len(wheels) != 1 or len(artifacts) != 1:
        raise WheelBuildError(
            f"临时 wheelhouse 必须恰好生成 1 个 wheel，实际为 {len(wheels)}"
        )

    wheel = wheels[0]
    if wheel.name != EXPECTED_WHEEL_NAME:
        raise WheelBuildError(
            f"wheel 文件名不符合固定制品名: {wheel.name!r} != {EXPECTED_WHEEL_NAME!r}"
        )

    try:
        with zipfile.ZipFile(wheel) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)):
                raise WheelBuildError("wheel 包含重复 ZIP 成员")
            unsafe = [
                entry.filename
                for entry in entries
                if entry.is_dir()
                or PurePosixPath(entry.filename).is_absolute()
                or ".." in PurePosixPath(entry.filename).parts
                or "\\" in entry.filename
                or stat.S_IFMT(entry.external_attr >> 16) == stat.S_IFLNK
            ]
            if unsafe:
                raise WheelBuildError(f"wheel 包含不安全的 ZIP 成员: {unsafe[0]}")
            expected_members = _expected_wheel_members(source_files)
            if set(names) != set(expected_members):
                extras = sorted(set(names).difference(expected_members))
                missing = sorted(expected_members.difference(names))
                raise WheelBuildError(
                    "wheel 包含禁止发布的文件或缺少预期文件: "
                    f"extras={extras}, missing={missing}"
                )
            metadata = email.message_from_bytes(
                archive.read(f"{DIST_INFO_DIRECTORY}/METADATA")
            )
            _validate_record(archive, expected_members)
    except zipfile.BadZipFile as exc:
        raise WheelBuildError(f"wheel 不是有效的 ZIP 制品: {wheel}") from exc

    expected_metadata = {
        "Name": EXPECTED_METADATA_NAME,
        "Version": EXPECTED_VERSION,
        "Requires-Python": EXPECTED_REQUIRES_PYTHON,
    }
    for key, expected in expected_metadata.items():
        actual = metadata[key]
        if actual != expected:
            raise WheelBuildError(f"METADATA {key} 不匹配: {actual!r} != {expected!r}")
    requirements = metadata.get_all("Requires-Dist", [])
    if len(requirements) != len(EXPECTED_RUNTIME_REQUIREMENTS) or set(
        requirements
    ) != set(EXPECTED_RUNTIME_REQUIREMENTS):
        raise WheelBuildError(
            "METADATA Requires-Dist 不匹配: "
            f"{requirements!r} != {sorted(EXPECTED_RUNTIME_REQUIREMENTS)!r}"
        )
    return wheel


def _assert_dist_contains_only_expected_wheel(dist_dir: Path) -> None:
    existing_wheels = sorted(
        path.name
        for path in dist_dir.iterdir()
        if path.is_file() and path.suffix == ".whl"
    )
    unexpected = [name for name in existing_wheels if name != EXPECTED_WHEEL_NAME]
    if unexpected:
        raise WheelBuildError(
            "正式 dist 含有非预期 wheel，拒绝猜测或批量删除: "
            + ", ".join(unexpected)
        )


def _temporary_sibling(destination: Path, label: str) -> Path:
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{destination.name}.{label}-",
        suffix=".tmp",
        dir=destination.parent,
    )
    os.close(descriptor)
    return Path(raw_path)


def _prepare_publication(
    wheel: Path,
    destinations: Sequence[Path],
    expected_hash: str,
) -> dict[Path, Path]:
    prepared: dict[Path, Path] = {}
    try:
        for destination in destinations:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = _temporary_sibling(destination, "new")
            prepared[destination] = temporary
            shutil.copyfile(wheel, temporary)
            temporary.chmod(0o644)
            actual_hash = hashlib.sha256(temporary.read_bytes()).hexdigest()
            if actual_hash != expected_hash:
                raise WheelBuildError(
                    f"写入前 hash 校验失败: {temporary}: {actual_hash} != {expected_hash}"
                )
        return prepared
    except BaseException:
        for temporary in prepared.values():
            temporary.unlink(missing_ok=True)
        raise


def _publish_with_rollback(
    prepared: dict[Path, Path],
    *,
    expected_hash: str,
    replace: Replace,
) -> None:
    backups: dict[Path, Path | None] = {}
    try:
        for destination in prepared:
            if destination.exists():
                backup = _temporary_sibling(destination, "backup")
                shutil.copy2(destination, backup)
                backups[destination] = backup
            else:
                backups[destination] = None

        for destination, temporary in prepared.items():
            replace(temporary, destination)
        final_hashes = {
            hashlib.sha256(destination.read_bytes()).hexdigest()
            for destination in prepared
        }
        if final_hashes != {expected_hash}:
            raise WheelBuildError(
                "发布后 dist 与算子 wheel hash 不一致: "
                + ", ".join(sorted(final_hashes))
            )
    except BaseException:
        for destination, backup_path in backups.items():
            if backup_path is None:
                destination.unlink(missing_ok=True)
            else:
                os.replace(backup_path, destination)
        raise
    finally:
        for temporary in prepared.values():
            temporary.unlink(missing_ok=True)
        for backup_path in backups.values():
            if backup_path is not None:
                backup_path.unlink(missing_ok=True)


def build_and_stage_registry_wheel(
    *,
    package_root: Path = PACKAGE_ROOT,
    dist_dir: Path = DIST_DIR,
    workspace_root: Path = WORKSPACE_ROOT,
    target_projects: Sequence[str] = TARGET_PROJECTS,
    builder: Builder = _build_wheel,
    replace: Replace = os.replace,
    source_files: Sequence[str] | None = None,
) -> PublishedWheel:
    package_root = package_root.resolve()
    dist_dir = dist_dir.resolve()
    workspace_root = workspace_root.resolve()
    dist_dir.mkdir(parents=True, exist_ok=True)
    _assert_dist_contains_only_expected_wheel(dist_dir)

    selected_source_files = (
        tuple(source_files)
        if source_files is not None
        else _tracked_source_files(package_root)
    )
    with tempfile.TemporaryDirectory(prefix="operator-registry-build-") as raw_dir:
        build_root = Path(raw_dir)
        clean_source = build_root / "source"
        wheelhouse = build_root / "wheelhouse"
        wheelhouse.mkdir()
        validated_source_files = _prepare_clean_source(
            package_root,
            clean_source,
            selected_source_files,
        )
        builder(clean_source, wheelhouse)
        wheel = _validate_wheel(wheelhouse, validated_source_files)
        wheel_hash = hashlib.sha256(wheel.read_bytes()).hexdigest()
        dist_path = dist_dir / EXPECTED_WHEEL_NAME
        staged_paths = tuple(
            workspace_root / project / "wheel" / EXPECTED_WHEEL_NAME
            for project in target_projects
        )
        destinations = (dist_path, *staged_paths)
        prepared = _prepare_publication(wheel, destinations, wheel_hash)
        _publish_with_rollback(
            prepared,
            expected_hash=wheel_hash,
            replace=replace,
        )

    final_hashes = {
        hashlib.sha256(destination.read_bytes()).hexdigest()
        for destination in destinations
    }
    if final_hashes != {wheel_hash}:
        raise WheelBuildError(
            "发布后 dist 与算子 wheel hash 不一致: " + ", ".join(sorted(final_hashes))
        )
    return PublishedWheel(
        dist_path=dist_path,
        staged_paths=staged_paths,
        sha256=wheel_hash,
    )


def main(arguments: Sequence[str] = ()) -> None:
    parser = argparse.ArgumentParser(
        description="Build, validate and stage the operator registry wheel.",
    )
    parser.parse_args(arguments)
    published = build_and_stage_registry_wheel()
    print(f"已构建: {published.dist_path} sha256={published.sha256}")
    for path in published.staged_paths:
        print(f"已暂存: {path} sha256={published.sha256}")


if __name__ == "__main__":
    main(sys.argv[1:])
