from __future__ import annotations

import json
import os
import re
import secrets
import stat
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.aggregate_milestone_2b_cases import (
    CASE_EVIDENCE_NAME_PATTERN,
    _publication_directory_descriptor,
    _same_filesystem_object,
    publish_json_once,
)
from scripts.milestone_2b_case_catalog import CaseDefinition
from scripts.milestone_2b_report_contract import DECLARATION_CATEGORY_BY_CASE_ID

from .base import CaseContext
from .safety import validate_case_evidence_authority

GIT_SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
CLAIM_NAME = ".case-runner.claim"


def release_identity(release_root: Path) -> tuple[str, str]:
    if not release_root.is_absolute():
        raise ValueError("release root must be absolute")
    release_tag = release_root.parent.name
    git_sha = release_root.name
    if not release_tag or release_tag in {".", ".."}:
        raise ValueError("release root must include a release tag")
    if GIT_SHA_PATTERN.fullmatch(git_sha) is None:
        raise ValueError("release root must end with a 40-character Git SHA")
    return release_tag, git_sha


def publish_case_evidence(
    *,
    context: CaseContext,
    case: CaseDefinition,
    name: str,
    payload: Mapping[str, Any],
) -> Path:
    case_id, category = _authoritative_case_identity(case)
    validate_case_evidence_authority(
        context=context,
        case_id=case_id,
        category=category,
    )
    if CASE_EVIDENCE_NAME_PATTERN.fullmatch(name) is None:
        raise ValueError("case evidence name is not safe")
    if type(payload) is not dict or not payload:
        raise ValueError("case evidence payload must be a non-empty object")
    if any(type(key) is not str for key in payload):
        raise ValueError("case evidence payload contains a non-string field name")
    release_tag, git_sha = release_identity(context.release_root)
    relative_path = _case_evidence_directory(category, case_id) / name
    publish_json_once(
        release_root=context.release_root,
        relative_path=relative_path,
        document={
            "schema_version": 3,
            "evidence_type": "case_evidence",
            "case_id": case_id,
            "release_tag": release_tag,
            "git_sha": git_sha,
            "recorded_at": datetime.now(UTC).isoformat(),
            "payload": dict(payload),
        },
    )
    return relative_path


def publish_framework_failure_evidence(
    *,
    context: CaseContext,
    case: CaseDefinition,
    reason: str,
) -> Path:
    for _ in range(16):
        name = f"framework-failure-{secrets.token_hex(16)}.json"
        try:
            return publish_case_evidence(
                context=context,
                case=case,
                name=name,
                payload={"event": "case_runner_failure", "reason": reason},
            )
        except ValueError as exc:
            if "already exists with different bytes" not in str(exc):
                raise
    raise ValueError("failed to reserve unique framework failure evidence")


def claim_case_once(*, context: CaseContext, case: CaseDefinition) -> bool:
    case_id, category = _authoritative_case_identity(case)
    release_tag, git_sha = release_identity(context.release_root)
    document = {
        "schema_version": 1,
        "case_id": case_id,
        "run_id": context.run_id,
        "target": context.target,
        "release_tag": release_tag,
        "git_sha": git_sha,
        "claimed_at": datetime.now(UTC).isoformat(),
        "nonce": secrets.token_hex(16),
    }
    payload = (
        json.dumps(
            document,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
    )
    with _publication_directory_descriptor(
        context.release_root, _case_evidence_directory(category, case_id)
    ) as publication:
        parent_descriptor, verify_directories = publication
        try:
            descriptor = os.open(
                CLAIM_NAME,
                flags,
                0o600,
                dir_fd=parent_descriptor,
            )
        except FileExistsError:
            _require_existing_claim(parent_descriptor)
            return False
        except OSError as exc:
            raise ValueError(f"failed to claim case {case_id}") from exc

        try:
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    raise ValueError(f"short write while claiming case {case_id}")
                offset += written
            os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
            opened = os.fstat(descriptor)
            named = os.stat(
                CLAIM_NAME,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            _require_claim_metadata(opened)
            _require_claim_metadata(named)
            if not _same_filesystem_object(opened, named):
                raise ValueError(f"case claim changed while opening: {case_id}")
            verify_directories()
            os.fsync(parent_descriptor)
            named_after = os.stat(
                CLAIM_NAME,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            opened_after = os.fstat(descriptor)
            _require_claim_metadata(named_after)
            _require_claim_metadata(opened_after)
            if not _same_filesystem_object(named_after, opened_after):
                raise ValueError(f"case claim changed while writing: {case_id}")
            verify_directories()
        except OSError as exc:
            raise ValueError(f"failed to persist case claim {case_id}") from exc
        finally:
            os.close(descriptor)
    return True


def _case_evidence_directory(category: str, case_id: str) -> Path:
    return Path(category) / "evidence" / case_id


def _authoritative_case_identity(case: CaseDefinition) -> tuple[str, str]:
    if type(case) is not CaseDefinition:
        raise ValueError("case type must be CaseDefinition")
    case_id = case.case_id
    category = case.category
    if type(case_id) is not str or type(category) is not str:
        raise ValueError("case identity must use plain strings")
    expected = DECLARATION_CATEGORY_BY_CASE_ID.get(case_id)
    if expected != category:
        raise ValueError(f"case {case_id} category does not match report authority")
    return case_id, category


def _require_existing_claim(parent_descriptor: int) -> None:
    try:
        metadata = os.stat(
            CLAIM_NAME,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except OSError as exc:
        raise ValueError("failed to inspect existing case claim") from exc
    _require_claim_metadata(metadata)


def _require_claim_metadata(metadata: os.stat_result) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("case claim must be a regular file")
    if metadata.st_uid != os.getuid():
        raise ValueError("case claim must be owned by the current UID")
    if metadata.st_nlink != 1:
        raise ValueError("case claim must have exactly one directory entry")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise ValueError("case claim must have mode 0600")
