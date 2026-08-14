from __future__ import annotations

from collections.abc import Iterable, Sequence
import hashlib
import os
from pathlib import Path


REQUIRED_MODEL_FILES = ("inference.json", "inference.pdiparams", "inference.yml")


class ModelVerificationError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _absolute_without_resolving(path: str | Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _normalize_declared_name(value: str | Path) -> str:
    path = Path(value)
    if path.is_absolute():
        raise ModelVerificationError(f"模型清单包含非法路径：{value}")
    return Path(os.path.normpath(os.fspath(path))).as_posix()


def _required_declared_name(root: Path, path: str | Path) -> str:
    candidate_path = Path(path)
    absolute = _absolute_without_resolving(
        candidate_path if candidate_path.is_absolute() else root / candidate_path
    )
    try:
        relative = absolute.relative_to(root)
    except ValueError as error:
        raise ModelVerificationError(f"必需模型路径逃逸：{path}") from error
    return _normalize_declared_name(relative)


def _check_resolved_path(root: Path, candidate: Path, declared_name: str) -> None:
    try:
        resolved_root = root.resolve()
        resolved_candidate = candidate.resolve()
    except OSError as error:
        raise ModelVerificationError(
            f"检查模型路径失败：{declared_name}"
        ) from error
    if (
        resolved_candidate != resolved_root
        and resolved_root not in resolved_candidate.parents
    ):
        raise ModelVerificationError(
            f"模型清单包含非法路径：{declared_name}"
        )


def verify_manifest(
    models_root: Path,
    manifest_path: Path,
    required_paths: Iterable[str | Path] | None = None,
    *,
    exact: bool = False,
) -> list[Path]:
    root = _absolute_without_resolving(models_root)
    try:
        manifest_is_symlink = manifest_path.is_symlink()
        manifest_is_file = manifest_path.is_file()
    except OSError as error:
        raise ModelVerificationError(
            f"检查模型清单失败：{manifest_path}"
        ) from error
    if manifest_is_symlink:
        raise ModelVerificationError(
            f"模型清单不能是符号链接：{manifest_path}"
        )
    if not manifest_is_file:
        raise ModelVerificationError(f"模型清单不存在：{manifest_path}")

    required: dict[str, Path] | None = None
    if required_paths is not None:
        required = {}
        for path in required_paths:
            declared_name = _required_declared_name(root, path)
            candidate = root / declared_name
            if declared_name in required:
                raise ModelVerificationError(
                    f"必需模型路径重复：{declared_name}"
                )
            try:
                candidate_is_symlink = candidate.is_symlink()
            except OSError as error:
                raise ModelVerificationError(
                    f"检查模型文件失败：{declared_name}"
                ) from error
            if candidate_is_symlink:
                raise ModelVerificationError(
                    f"模型文件不能是符号链接：{declared_name}"
                )
            _check_resolved_path(root, candidate, declared_name)
            required[declared_name] = candidate

    verified: list[Path] = []
    matched: set[str] = set()
    declarations: set[str] = set()
    try:
        manifest_lines = manifest_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ModelVerificationError(
            f"读取模型清单失败：{manifest_path}"
        ) from error
    for line_number, raw_line in enumerate(
        manifest_lines,
        start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            expected, relative_name = line.split(maxsplit=1)
        except ValueError as error:
            raise ModelVerificationError(
                f"模型清单第 {line_number} 行格式错误"
            ) from error
        declared_name = _normalize_declared_name(relative_name.strip())
        if declared_name in declarations:
            raise ModelVerificationError(
                f"模型清单包含重复声明：{declared_name}"
            )
        declarations.add(declared_name)
        candidate = root / declared_name
        selected = required is None or declared_name in required
        if selected:
            try:
                candidate_is_symlink = candidate.is_symlink()
            except OSError as error:
                raise ModelVerificationError(
                    f"检查模型文件失败：{declared_name}"
                ) from error
            if candidate_is_symlink:
                raise ModelVerificationError(
                    f"模型文件不能是符号链接：{declared_name}"
                )
        _check_resolved_path(root, candidate, declared_name)
        if not selected:
            continue
        matched.add(declared_name)
        try:
            candidate_is_file = candidate.is_file()
        except OSError as error:
            raise ModelVerificationError(
                f"检查模型文件失败：{declared_name}"
            ) from error
        if not candidate_is_file:
            raise ModelVerificationError(f"模型文件不存在：{relative_name}")
        try:
            actual = _sha256(candidate)
        except (OSError, UnicodeError) as error:
            raise ModelVerificationError(
                f"读取模型文件失败：{declared_name}"
            ) from error
        if actual.lower() != expected.lower():
            raise ModelVerificationError(
                f"模型文件摘要不一致：{relative_name}"
            )
        verified.append(candidate)

    if required is not None:
        missing = sorted(required.keys() - matched)
        if missing:
            raise ModelVerificationError(
                f"模型清单缺少必需项：{', '.join(missing)}"
            )
    elif not verified:
        raise ModelVerificationError("模型清单中没有可验证文件")
    if exact:
        actual: set[str] = set()
        for candidate in root.rglob("*"):
            relative = candidate.relative_to(root).as_posix()
            try:
                candidate.lstat()
            except OSError as error:
                raise ModelVerificationError(
                    f"检查模型目录失败：{relative}"
                ) from error
            if candidate.is_symlink():
                raise ModelVerificationError(
                    f"模型目录只能包含普通文件：{relative}"
                )
            if candidate.is_dir():
                continue
            if not candidate.is_file():
                raise ModelVerificationError(
                    f"模型目录只能包含普通文件：{relative}"
                )
            actual.add(relative)
        absolute_manifest = _absolute_without_resolving(manifest_path)
        try:
            actual.discard(absolute_manifest.relative_to(root).as_posix())
        except ValueError:
            pass
        unlisted = sorted(actual.difference(declarations))
        if unlisted:
            raise ModelVerificationError(
                f"模型目录包含未声明文件：{', '.join(unlisted)}"
            )
    return verified


def verify_configured_models(
    required_model_dirs: Sequence[Path],
    *,
    configured_model_dirs: Sequence[Path] | None = None,
) -> list[Path]:
    audited_directories = configured_model_dirs or required_model_dirs
    for model_dir in audited_directories:
        if model_dir.is_symlink():
            raise ModelVerificationError(
                f"配置模型目录不能是符号链接：{model_dir}"
            )
    roots = {
        _absolute_without_resolving(model_dir).parent
        for model_dir in audited_directories
    }
    if len(roots) != 1:
        raise ModelVerificationError(
            "配置模型目录必须位于同一 models 根目录"
        )
    models_root = roots.pop()
    required_paths = [
        _absolute_without_resolving(model_dir) / file_name
        for model_dir in required_model_dirs
        for file_name in REQUIRED_MODEL_FILES
    ]
    return verify_manifest(
        models_root,
        models_root / "manifest.sha256",
        required_paths=required_paths,
    )
