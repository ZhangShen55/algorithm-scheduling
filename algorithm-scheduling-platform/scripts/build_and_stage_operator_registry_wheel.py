from __future__ import annotations

import email
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
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


def _forbidden_member(name: str) -> bool:
    path = PurePosixPath(name)
    parts = path.parts
    platform_roots = {
        "control_service",
        "online_gateway_service",
        "orchestrator_service",
        "vision_orchestrator_service",
    }
    if parts and parts[0] in platform_roots:
        return True
    if len(parts) >= 2 and parts[:2] in {
        ("packages", "platform_common"),
        ("packages", "platform_contracts"),
    }:
        return True
    filename = path.name.lower()
    return (
        filename in {".env", "credentials", "credentials.json", "secrets.json"}
        or filename.endswith((".key", ".pem", ".crt", ".p12", ".pfx"))
    )


def _validate_wheel(wheelhouse: Path) -> Path:
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
            names = archive.namelist()
            forbidden = sorted(name for name in names if _forbidden_member(name))
            if forbidden:
                raise WheelBuildError(f"wheel 包含禁止发布的文件: {forbidden[0]}")
            metadata_names = [
                name for name in names if name.endswith(".dist-info/METADATA")
            ]
            if len(metadata_names) != 1:
                raise WheelBuildError(
                    "wheel 必须包含唯一的 .dist-info/METADATA，"
                    f"实际为 {len(metadata_names)}"
                )
            metadata = email.message_from_bytes(archive.read(metadata_names[0]))
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
) -> PublishedWheel:
    package_root = package_root.resolve()
    dist_dir = dist_dir.resolve()
    workspace_root = workspace_root.resolve()
    dist_dir.mkdir(parents=True, exist_ok=True)
    _assert_dist_contains_only_expected_wheel(dist_dir)

    with tempfile.TemporaryDirectory(prefix="operator-registry-wheelhouse-") as raw_dir:
        wheelhouse = Path(raw_dir)
        builder(package_root, wheelhouse)
        wheel = _validate_wheel(wheelhouse)
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


def main() -> None:
    published = build_and_stage_registry_wheel()
    print(f"已构建: {published.dist_path} sha256={published.sha256}")
    for path in published.staged_paths:
        print(f"已暂存: {path} sha256={published.sha256}")


if __name__ == "__main__":
    main()
