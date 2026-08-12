#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import stat
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

MANIFEST_NAME = "model-assets.manifest.json"
DEFINITION_PATH = Path("algorithm-scheduling-platform/deploy/model-assets.json")
JOURNAL_NAME = ".model-assets-transaction.json"
LOCK_NAME = ".model-assets.lock"
FORBIDDEN_SUFFIXES = {".key", ".pem", ".p12", ".pfx"}
FORBIDDEN_PARTS = {"models-encrypted", "secrets"}
POLLUTION_NAMES = {".DS_Store"}
POLLUTION_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


class AssetError(RuntimeError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssetError(f"JSON root must be an object: {path.name}")
    return value


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _remove_tree(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)
    _fsync_directory(path.parent)


def _safe_relative_path(value: Any, *, field: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise AssetError(f"{field} must be a non-empty string")
    if "\\" in value or any(part in {"", ".", ".."} for part in value.split("/")):
        raise AssetError(f"{field} must use canonical POSIX path text")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value:
        raise AssetError(f"{field} must use canonical POSIX path text")
    lowered = {part.lower() for part in path.parts}
    if lowered.intersection(FORBIDDEN_PARTS) or path.suffix.lower() in FORBIDDEN_SUFFIXES:
        raise AssetError(f"{field} refers to forbidden encrypted or secret material")
    return path


def _definitions(workspace: Path) -> dict[str, tuple[str, ...]]:
    document = _read_json(workspace / DEFINITION_PATH)
    if document.get("schema_version") != 1 or not isinstance(document.get("assets"), list):
        raise AssetError("unsupported model asset definition")
    result: dict[str, tuple[str, ...]] = {}
    for item in document["assets"]:
        if not isinstance(item, dict):
            raise AssetError("invalid model asset definition entry")
        target = _safe_relative_path(item.get("target"), field="target").as_posix()
        sentinels = item.get("required_sentinels")
        if not isinstance(sentinels, list) or not sentinels:
            raise AssetError(f"required_sentinels must be non-empty for {target}")
        result[target] = tuple(
            _safe_relative_path(value, field="required sentinel").as_posix()
            for value in sentinels
        )
    return result


def _manifest(
    source: Path, definitions: dict[str, tuple[str, ...]]
) -> dict[str, dict[str, tuple[int, str]]]:
    document = _read_json(source / MANIFEST_NAME)
    if document.get("schema_version") != 1 or not isinstance(document.get("assets"), list):
        raise AssetError("unsupported model asset manifest")
    result: dict[str, dict[str, tuple[int, str]]] = {}
    for item in document["assets"]:
        if not isinstance(item, dict):
            raise AssetError("invalid model asset manifest entry")
        target = _safe_relative_path(item.get("target"), field="target").as_posix()
        if target in result:
            raise AssetError(f"duplicate model target: {target}")
        files = item.get("files")
        if not isinstance(files, list) or not files:
            raise AssetError(f"files must be non-empty for {target}")
        entries: dict[str, tuple[int, str]] = {}
        for entry in files:
            if not isinstance(entry, dict):
                raise AssetError(f"invalid file entry for {target}")
            relative = _safe_relative_path(entry.get("path"), field="file path").as_posix()
            size = entry.get("bytes")
            digest = entry.get("sha256")
            if (
                not isinstance(size, int)
                or size < 0
                or not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise AssetError(f"invalid size or hash metadata for {target}")
            if relative in entries:
                raise AssetError(f"duplicate file entry for {target}")
            entries[relative] = (size, digest)
        result[target] = entries
    missing = sorted(set(definitions).difference(result))
    extra = sorted(set(result).difference(definitions))
    if missing:
        raise AssetError("missing model roots in manifest")
    if extra:
        raise AssetError("extra model roots in manifest")
    for target, sentinels in definitions.items():
        missing_sentinels = sorted(set(sentinels).difference(result[target]))
        if missing_sentinels:
            raise AssetError(f"missing required sentinel files for {target}")
    return result


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _actual_files(root: Path) -> dict[str, Path]:
    if not root.is_dir() or root.is_symlink():
        raise AssetError("missing or non-directory model root")
    result: dict[str, Path] = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if stat.S_ISLNK(mode):
            raise AssetError("symlink is forbidden in model assets")
        if not stat.S_ISREG(mode):
            raise AssetError("model assets must contain regular files only")
        result[relative] = path
    return result


def generate_manifest(source: Path, workspace: Path) -> None:
    _source_outside_worktree(source)
    definitions = _definitions(workspace)
    assets: list[dict[str, Any]] = []
    for target, sentinels in definitions.items():
        root = source / target
        actual = _actual_files(root)
        for relative in actual:
            path = PurePosixPath(relative)
            if path.name in POLLUTION_NAMES or set(path.parts).intersection(POLLUTION_PARTS):
                raise AssetError("model source contains cache or platform pollution")
            _safe_relative_path(relative, field="file path")
        missing_sentinels = sorted(set(sentinels).difference(actual))
        if missing_sentinels:
            raise AssetError(f"missing required sentinel files for {target}")
        files: list[dict[str, Any]] = [
            {
                "path": relative,
                "bytes": actual[relative].stat().st_size,
                "sha256": _hash(actual[relative]),
            }
            for relative in sorted(actual)
        ]
        assets.append({"target": target, "files": files})
        print(
            f"model-assets: indexed target={target} files={len(files)} "
            f"bytes={sum(int(item['bytes']) for item in files)}"
        )
    _atomic_json(source / MANIFEST_NAME, {"schema_version": 1, "assets": assets})
    print("model-assets: PASS: external manifest generated")


def _verify_tree(root: Path, expected: dict[str, tuple[int, str]]) -> tuple[int, int]:
    actual = _actual_files(root)
    missing = sorted(set(expected).difference(actual))
    extra = sorted(set(actual).difference(expected))
    if missing:
        raise AssetError("missing files in model root")
    if extra:
        raise AssetError("extra files in model root")
    total = 0
    for relative, (size, digest) in expected.items():
        path = actual[relative]
        if path.stat().st_size != size:
            raise AssetError("model file byte count mismatch")
        if _hash(path) != digest:
            raise AssetError("model file hash mismatch")
        total += size
    return len(expected), total


def _source_outside_worktree(source: Path) -> None:
    current = source
    while True:
        if (current / ".git").exists():
            raise AssetError("model source must be outside every Git worktree")
        if current.parent == current:
            return
        current = current.parent


def _journal_path(workspace: Path) -> Path:
    return workspace / JOURNAL_NAME


def _recover(workspace: Path, definitions: dict[str, tuple[str, ...]]) -> None:
    path = _journal_path(workspace)
    if not path.exists():
        return
    journal = _read_json(path)
    entries = journal.get("entries")
    transaction_id = journal.get("transaction_id")
    if (
        not isinstance(entries, list)
        or not isinstance(transaction_id, str)
        or len(transaction_id) != 32
        or any(character not in "0123456789abcdef" for character in transaction_id)
        or journal.get("phase") not in {"prepared", "committed"}
    ):
        raise AssetError("invalid model asset transaction journal")
    expected_targets = set(definitions)
    actual_targets = {
        entry.get("target") for entry in entries if isinstance(entry, dict)
    }
    if len(entries) != len(definitions) or actual_targets != expected_targets:
        raise AssetError("invalid model asset transaction journal target set")
    committed = journal.get("phase") == "committed"
    for entry in reversed(entries):
        if not isinstance(entry, dict):
            raise AssetError("invalid model asset transaction entry")
        target = workspace / str(entry["target"])
        stage = Path(str(entry["stage"]))
        backup = Path(str(entry["backup"]))
        expected_stage = target.parent / f".{target.name}.model-stage-{transaction_id}"
        expected_backup = target.parent / f".{target.name}.model-backup-{transaction_id}"
        if stage != expected_stage or backup != expected_backup:
            raise AssetError("invalid model asset transaction journal paths")
        if committed:
            if backup.exists():
                _remove_tree(backup)
            if stage.exists():
                _remove_tree(stage)
            continue
        # A process can die between either rename and the next journal fsync.
        # An existing backup proves the target originally existed; restore it
        # regardless of the last persisted switched flag.
        had_original = bool(entry.get("had_original"))
        if backup.exists() and target.exists():
            _remove_tree(target)
        if backup.exists():
            os.replace(backup, target)
            _fsync_directory(target.parent)
        elif not had_original and target.exists() and not stage.exists():
            _remove_tree(target)
        if stage.exists():
            _remove_tree(stage)
    path.unlink()
    _fsync_directory(workspace)


def _copy_tree(source_root: Path, stage: Path, files: dict[str, tuple[int, str]]) -> None:
    stage.mkdir(mode=0o700)
    for relative in sorted(files):
        source = source_root / relative
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target, follow_symlinks=False)
        with target.open("rb") as stream:
            os.fsync(stream.fileno())
    for directory in sorted(
        (path for path in stage.rglob("*") if path.is_dir()), reverse=True
    ):
        _fsync_directory(directory)
    _fsync_directory(stage)
    _fsync_directory(stage.parent)


@contextmanager
def _lock(workspace: Path) -> Iterator[None]:
    path = workspace / LOCK_NAME
    with path.open("a+") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        yield


def stage(source: Path, workspace: Path) -> None:
    _source_outside_worktree(source)
    definitions = _definitions(workspace)
    expected = _manifest(source, definitions)
    summaries: dict[str, tuple[int, int]] = {}
    for target, entries in expected.items():
        summaries[target] = _verify_tree(source / target, entries)
    with _lock(workspace):
        _recover(workspace, definitions)
        unchanged = True
        for target, entries in expected.items():
            try:
                _verify_tree(workspace / target, entries)
            except AssetError:
                unchanged = False
                break
        if unchanged:
            print("model-assets: PASS: assets already match manifest")
            return
        transaction_id = uuid.uuid4().hex
        journal: dict[str, Any] = {
            "phase": "prepared",
            "transaction_id": transaction_id,
            "entries": [],
        }
        fail_after_stages = int(os.environ.get("MODEL_ASSET_TEST_FAIL_AFTER_STAGES", "0"))
        prepared_stages: list[Path] = []
        try:
            for stage_index, (target, entries) in enumerate(expected.items(), start=1):
                destination = workspace / target
                destination.parent.mkdir(parents=True, exist_ok=True)
                stage_path = destination.parent / (
                    f".{destination.name}.model-stage-{transaction_id}"
                )
                backup_path = destination.parent / (
                    f".{destination.name}.model-backup-{transaction_id}"
                )
                prepared_stages.append(stage_path)
                _copy_tree(source / target, stage_path, entries)
                _verify_tree(stage_path, entries)
                journal["entries"].append(
                    {
                        "target": target,
                        "stage": str(stage_path),
                        "backup": str(backup_path),
                        "had_original": destination.exists(),
                        "switched": False,
                    }
                )
                if fail_after_stages == stage_index:
                    raise AssetError("injected failure while preparing model stages")
        except Exception:
            for stage_path in prepared_stages:
                if stage_path.exists():
                    _remove_tree(stage_path)
            raise
        _atomic_json(_journal_path(workspace), journal)
        interrupt_spec = os.environ.get("MODEL_ASSET_TEST_INTERRUPT_AT", "")

        def interrupt(stage_name: str, switch_index: int) -> None:
            if interrupt_spec == f"{stage_name}:{switch_index}":
                raise AssetError(
                    f"injected interruption {stage_name} during model root switch"
                )

        for index, entry in enumerate(journal["entries"], start=1):
            destination = workspace / entry["target"]
            backup_path = Path(entry["backup"])
            stage_path = Path(entry["stage"])
            if destination.exists():
                os.replace(destination, backup_path)
                _fsync_directory(destination.parent)
            interrupt("after_backup", index)
            os.replace(stage_path, destination)
            _fsync_directory(destination.parent)
            interrupt("after_replace", index)
            entry["switched"] = True
            _atomic_json(_journal_path(workspace), journal)
            interrupt("after_journal", index)
        journal["phase"] = "committed"
        _atomic_json(_journal_path(workspace), journal)
        _recover(workspace, definitions)
    for target, (count, size) in summaries.items():
        print(f"model-assets: staged target={target} files={count} bytes={size}")
    print("model-assets: PASS")


def verify(source: Path, workspace: Path) -> None:
    _source_outside_worktree(source)
    definitions = _definitions(workspace)
    expected = _manifest(source, definitions)
    for target, entries in expected.items():
        count, size = _verify_tree(workspace / target, entries)
        print(f"model-assets: verified target={target} files={count} bytes={size}")
    print("model-assets: PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("generate", "stage", "verify"))
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    arguments = parser.parse_args()
    source = arguments.source.resolve(strict=True)
    workspace = arguments.workspace.resolve(strict=True)
    if source == workspace or workspace in source.parents:
        raise AssetError("model source must be outside the destination workspace")
    if arguments.command == "generate":
        generate_manifest(source, workspace)
    elif arguments.command == "stage":
        stage(source, workspace)
    else:
        verify(source, workspace)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssetError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"model-assets: FAIL: {error}", file=sys.stderr)
        raise SystemExit(1) from error
