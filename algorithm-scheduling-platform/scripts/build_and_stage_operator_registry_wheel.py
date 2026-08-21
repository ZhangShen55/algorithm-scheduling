from __future__ import annotations

import argparse
import csv
import email
import fcntl
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from base64 import urlsafe_b64encode
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TypedDict, cast

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PLATFORM_ROOT.parent
if str(PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATFORM_ROOT))

from deploy.scripts.operator_topology import CURRENT_TOPOLOGY  # noqa: E402

PACKAGE_ROOT = PLATFORM_ROOT / "packages" / "operator_registry_client"
DIST_DIR = PACKAGE_ROOT / "dist"

def _canonical_dependency(requirement: str) -> str:
    requirement = requirement.replace('"', "'")
    for index, character in enumerate(requirement):
        if character in "<>=!~":
            name = requirement[:index]
            specifiers = requirement[index:].split(",")
            return name + ",".join(sorted(specifiers))
    return requirement


with (PACKAGE_ROOT / "pyproject.toml").open("rb") as _pyproject_stream:
    _PROJECT_METADATA = tomllib.load(_pyproject_stream)["project"]

EXPECTED_METADATA_NAME = str(_PROJECT_METADATA["name"])
EXPECTED_VERSION = str(_PROJECT_METADATA["version"])
EXPECTED_REQUIRES_PYTHON = str(_PROJECT_METADATA["requires-python"])
_NORMALIZED_DISTRIBUTION_NAME = EXPECTED_METADATA_NAME.replace("-", "_")
EXPECTED_WHEEL_NAME = (
    f"{_NORMALIZED_DISTRIBUTION_NAME}-{EXPECTED_VERSION}-py3-none-any.whl"
)
EXPECTED_PACKAGE_MODULES = (
    "__init__.py",
    "client.py",
    "config.py",
    "lifecycle.py",
    "logging.py",
    "ops.py",
    "runtime.py",
    "validation.py",
)
EXPECTED_SOURCE_FILES = tuple(
    sorted(("README.md", "pyproject.toml", *EXPECTED_PACKAGE_MODULES))
)
EXPECTED_RUNTIME_REQUIREMENTS = frozenset(
    _canonical_dependency(str(requirement))
    for requirement in _PROJECT_METADATA["dependencies"]
)
DIST_INFO_DIRECTORY = f"{_NORMALIZED_DISTRIBUTION_NAME}-{EXPECTED_VERSION}.dist-info"
EXPECTED_DIST_INFO_MEMBERS = frozenset(
    {
        f"{DIST_INFO_DIRECTORY}/METADATA",
        f"{DIST_INFO_DIRECTORY}/WHEEL",
        f"{DIST_INFO_DIRECTORY}/top_level.txt",
        f"{DIST_INFO_DIRECTORY}/RECORD",
    }
)
TARGET_PROJECTS = tuple(entry.project_directory for entry in CURRENT_TOPOLOGY.operators)

Builder = Callable[[Path, Path], None]
Replace = Callable[[str | os.PathLike[str], str | os.PathLike[str]], None]
AfterReplace = Callable[[Path], None]


class TransactionTarget(TypedDict):
    destination: str
    temporary: str
    backup: str | None
    old_exists: bool
    old_hash: str | None
    prepared: bool
    published: bool


class TransactionJournal(TypedDict):
    version: int
    phase: str
    new_hash: str
    targets: list[TransactionTarget]


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
    if set(validated) != set(EXPECTED_SOURCE_FILES) or len(validated) != len(
        EXPECTED_SOURCE_FILES
    ):
        extras = sorted(set(validated).difference(EXPECTED_SOURCE_FILES))
        missing = sorted(set(EXPECTED_SOURCE_FILES).difference(validated))
        raise WheelBuildError(
            "registry client 源码合同不匹配；如需新增模块请显式更新合同: "
            f"extras={extras}, missing={missing}"
        )
    return tuple(sorted(validated))


def _source_snapshot(package_root: Path, source_files: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for relative_path in sorted(source_files):
        source = package_root / relative_path
        if source.is_symlink() or not source.is_file():
            raise WheelBuildError(f"tracked 源文件不是普通文件: {relative_path}")
        content = source.read_bytes()
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(content)).encode("ascii"))
        digest.update(b"\0")
        digest.update(content)
    return digest.hexdigest()


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
    requirements = [
        _canonical_dependency(requirement)
        for requirement in metadata.get_all("Requires-Dist", [])
    ]
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


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_bytes_durable(path: Path, content: bytes, mode: int = 0o600) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        mode,
    )
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@contextmanager
def _publication_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        lock_path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise WheelBuildError(f"发布锁不是普通文件: {lock_path}")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _journal_path(dist_dir: Path) -> Path:
    return dist_dir / ".operator-registry-wheel.transaction.json"


def _write_journal(dist_dir: Path, journal: TransactionJournal) -> None:
    path = _journal_path(dist_dir)
    temporary = dist_dir / ".operator-registry-wheel.transaction.new.tmp"
    temporary.unlink(missing_ok=True)
    _write_bytes_durable(
        temporary,
        (json.dumps(journal, sort_keys=True, separators=(",", ":")) + "\n").encode(),
    )
    os.replace(temporary, path)
    _fsync_directory(dist_dir)


def _load_journal(dist_dir: Path) -> TransactionJournal | None:
    path = _journal_path(dist_dir)
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise WheelBuildError(f"事务 journal 不是普通文件: {path}")
    try:
        journal = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WheelBuildError(f"事务 journal 无法解析: {path}") from exc
    if (
        not isinstance(journal, dict)
        or journal.get("version") != 1
        or journal.get("phase")
        not in {"preparing", "prepared", "publishing", "committed", "rolled_back"}
        or not isinstance(journal.get("new_hash"), str)
        or not isinstance(journal.get("targets"), list)
    ):
        raise WheelBuildError(f"事务 journal 格式不受支持: {path}")
    targets = journal["targets"]
    if len(targets) > 9:
        raise WheelBuildError(f"事务 journal 目标数量非法: {len(targets)}")
    destinations: set[str] = set()
    for index, item in enumerate(targets):
        if not isinstance(item, dict):
            raise WheelBuildError(f"事务 journal 目标格式非法: index={index}")
        required_keys = {
            "destination",
            "temporary",
            "backup",
            "old_exists",
            "old_hash",
            "prepared",
            "published",
        }
        if set(item) != required_keys:
            raise WheelBuildError(f"事务 journal 目标字段非法: index={index}")
        destination = Path(item["destination"])
        temporary = Path(item["temporary"])
        backup_value = item["backup"]
        expected_temporary = destination.parent / (
            f".{destination.name}.registry-wheel-{index}.new.tmp"
        )
        expected_backup = destination.parent / (
            f".{destination.name}.registry-wheel-{index}.backup.tmp"
        )
        if (
            not destination.is_absolute()
            or str(destination) in destinations
            or temporary != expected_temporary
            or (backup_value is not None and Path(backup_value) != expected_backup)
        ):
            raise WheelBuildError(f"事务 journal 路径非法: index={index}")
        destinations.add(str(destination))
    return cast(TransactionJournal, journal)


def _cleanup_transaction(dist_dir: Path, journal: TransactionJournal) -> None:
    errors: list[str] = []
    for item in journal["targets"]:
        for key in ("temporary", "backup"):
            raw_path = item.get(key)
            if raw_path:
                try:
                    Path(str(raw_path)).unlink(missing_ok=True)
                    _fsync_directory(Path(str(raw_path)).parent)
                except OSError as exc:
                    errors.append(f"{raw_path}: {exc}")
    if errors:
        raise WheelBuildError("事务临时文件清理失败: " + "; ".join(errors))
    _journal_path(dist_dir).unlink(missing_ok=True)
    _fsync_directory(dist_dir)


def _recover_transaction(
    dist_dir: Path,
    *,
    replace: Replace = os.replace,
) -> None:
    journal = _load_journal(dist_dir)
    if journal is None:
        return
    if journal.get("phase") in {"committed", "rolled_back"}:
        _cleanup_transaction(dist_dir, journal)
        return

    errors: list[str] = []
    for item in journal["targets"]:
        destination = Path(str(item["destination"]))
        backup_value = item.get("backup")
        if not item.get("prepared"):
            continue
        try:
            if item["old_exists"]:
                backup = Path(str(backup_value))
                if not backup.is_file() or _hash_file(backup) != item["old_hash"]:
                    raise WheelBuildError(f"事务备份缺失或 hash 错误: {backup}")
                restore = destination.parent / f".{destination.name}.registry-wheel-restore.tmp"
                restore.unlink(missing_ok=True)
                _write_bytes_durable(restore, backup.read_bytes(), 0o644)
                replace(restore, destination)
            else:
                destination.unlink(missing_ok=True)
            _fsync_directory(destination.parent)
        except (OSError, WheelBuildError) as exc:
            errors.append(f"{destination}: {exc}")
    if errors:
        raise WheelBuildError("事务回滚未完成，已保留 journal 与备份: " + "; ".join(errors))

    for item in journal["targets"]:
        if not item.get("prepared"):
            continue
        destination = Path(str(item["destination"]))
        if item["old_exists"]:
            if not destination.is_file() or _hash_file(destination) != item["old_hash"]:
                raise WheelBuildError(f"事务回滚后二次校验失败: {destination}")
        elif destination.exists():
            raise WheelBuildError(f"事务回滚后目标应不存在: {destination}")
    journal["phase"] = "rolled_back"
    _write_journal(dist_dir, journal)
    _cleanup_transaction(dist_dir, journal)


def _publish_artifact_transaction(
    artifact: Path,
    destinations: Sequence[Path],
    *,
    dist_dir: Path,
    expected_hash: str,
    replace: Replace = os.replace,
    after_replace: AfterReplace | None = None,
    recover_replace: Replace = os.replace,
) -> None:
    targets: list[TransactionTarget] = []
    journal: TransactionJournal = {
        "version": 1,
        "phase": "preparing",
        "new_hash": expected_hash,
        "targets": targets,
    }
    try:
        for index, destination in enumerate(destinations):
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.parent / (
                f".{destination.name}.registry-wheel-{index}.new.tmp"
            )
            backup = destination.parent / (
                f".{destination.name}.registry-wheel-{index}.backup.tmp"
            )
            temporary.unlink(missing_ok=True)
            backup.unlink(missing_ok=True)
            old_exists = destination.exists()
            old_hash: str | None = None
            if old_exists:
                if destination.is_symlink() or not destination.is_file():
                    raise WheelBuildError(f"发布目标不是普通文件: {destination}")
                old_hash = _hash_file(destination)
            item: TransactionTarget = {
                "destination": str(destination),
                "temporary": str(temporary),
                "backup": str(backup) if old_exists else None,
                "old_exists": old_exists,
                "old_hash": old_hash,
                "prepared": False,
                "published": False,
            }
            targets.append(item)
            _write_journal(dist_dir, journal)
            _write_bytes_durable(temporary, artifact.read_bytes(), 0o644)
            if old_exists:
                _write_bytes_durable(backup, destination.read_bytes(), 0o600)
            item["prepared"] = True
            _write_journal(dist_dir, journal)
        journal["phase"] = "prepared"
        _write_journal(dist_dir, journal)
        for item in targets:
            destination = Path(str(item["destination"]))
            temporary = Path(str(item["temporary"]))
            replace(temporary, destination)
            destination.chmod(0o644)
            _fsync_directory(destination.parent)
            item["published"] = True
            journal["phase"] = "publishing"
            _write_journal(dist_dir, journal)
            if after_replace is not None:
                after_replace(destination)
        for destination in destinations:
            if (
                not destination.is_file()
                or destination.stat().st_mode & 0o777 != 0o644
                or _hash_file(destination) != expected_hash
            ):
                raise WheelBuildError(f"发布后 hash 或权限校验失败: {destination}")
        journal["phase"] = "committed"
        _write_journal(dist_dir, journal)
        _cleanup_transaction(dist_dir, journal)
    except BaseException:
        if _journal_path(dist_dir).exists():
            _recover_transaction(dist_dir, replace=recover_replace)
        else:
            for item in targets:
                for key in ("temporary", "backup"):
                    raw_path = item.get(key)
                    if raw_path:
                        Path(str(raw_path)).unlink(missing_ok=True)
        raise


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
    source_snapshot = _source_snapshot(package_root, selected_source_files)
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
        lock_path = dist_dir / ".operator-registry-wheel.lock"
        with _publication_lock(lock_path):
            _recover_transaction(dist_dir)
            if _source_snapshot(package_root, selected_source_files) != source_snapshot:
                raise WheelBuildError("构建期间 tracked 源码发生变化，拒绝发布制品")
            if _hash_file(wheel) != wheel_hash:
                raise WheelBuildError("进入发布锁后 wheel hash 发生变化")
            _publish_artifact_transaction(
                wheel,
                destinations,
                dist_dir=dist_dir,
                expected_hash=wheel_hash,
                replace=replace,
            )

    final_hashes = {
        hashlib.sha256(destination.read_bytes()).hexdigest()
        for destination in destinations
    }
    if final_hashes != {wheel_hash} or any(
        destination.stat().st_mode & 0o777 != 0o644 for destination in destinations
    ):
        raise WheelBuildError(
            "发布后 dist 与算子 wheel hash 或权限不一致: "
            + ", ".join(sorted(final_hashes))
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
