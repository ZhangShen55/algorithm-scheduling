from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .models import BenchmarkIdentity, JsonObject


def attempt_root(report_root: Path, identity: BenchmarkIdentity, case_id: str) -> Path:
    if not case_id or Path(case_id).name != case_id:
        raise ValueError("case_id 必须是单层非空安全名称")
    return (
        report_root
        / identity.campaign_id
        / case_id
        / f"attempt-{identity.attempt:03d}"
    )


def atomic_write_once_json(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"证据已存在，禁止覆盖: {path}")
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    payload = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        os.link(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def identity_document(identity: BenchmarkIdentity) -> JsonObject:
    return {
        "schema_version": 1,
        "evidence_type": "vbas_benchmark_identity",
        "campaign_id": identity.campaign_id,
        "attempt": identity.attempt,
        "seed": identity.seed,
        "git_sha": identity.git_sha,
        "image_ids": list(identity.image_ids),
        "config_digest": identity.config_digest,
    }


def build_attempt_summary(
    *,
    identity: BenchmarkIdentity,
    case_id: str,
    load: JsonObject,
    telemetry: JsonObject,
    fixture_manifest_sha256: str,
) -> JsonObject:
    load_status = load.get("status")
    assessments = telemetry.get("memory_assessment")
    stable = isinstance(assessments, dict) and all(
        isinstance(value, dict) and value.get("status") == "stable"
        for value in assessments.values()
    )
    passed = load_status == "passed" and stable and telemetry.get("collector_error") is None
    return {
        "schema_version": 1,
        "evidence_type": "vbas_benchmark_attempt_summary",
        "campaign_id": identity.campaign_id,
        "attempt": identity.attempt,
        "case_id": case_id,
        "status": "passed" if passed else "failed",
        "reason": (
            "负载、供料和显存稳定性门禁全部通过"
            if passed
            else "负载、供料或显存稳定性门禁未通过"
        ),
        "git_sha": identity.git_sha,
        "image_ids": list(identity.image_ids),
        "config_digest": identity.config_digest,
        "fixture_manifest_sha256": fixture_manifest_sha256,
        "load_summary": load.get("summary", {}),
        "memory_assessment": assessments if isinstance(assessments, dict) else {},
    }


def require_complete_attempt(document: dict[str, Any]) -> None:
    required = {
        "campaign_id",
        "attempt",
        "case_id",
        "status",
        "git_sha",
        "image_ids",
        "config_digest",
        "fixture_manifest_sha256",
        "load_summary",
        "memory_assessment",
    }
    missing = required - document.keys()
    if missing:
        raise ValueError(f"基准 attempt 证据缺少字段: {sorted(missing)}")
    if document["status"] not in {"passed", "failed", "interrupted"}:
        raise ValueError("基准 attempt 状态不合法")
