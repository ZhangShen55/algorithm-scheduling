from __future__ import annotations

import json
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .catalog import (
    CampaignCatalog,
    CaseExecution,
    CaseSpec,
    FixtureManifest,
    LoadProfile,
    default_catalog,
    default_load_profile,
    validate_case_executions,
)
from .core import NorthboundTargets, derive_campaign_id
from .report import atomic_write_report

_SHA = re.compile(r"[0-9a-f]{40}")
_RELEASE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_SENTINEL_FIXTURE = "external-fixture-manifest"


class CampaignPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: int = Field(default=1, ge=1)
    campaign_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    release_tag: str
    git_sha: str
    seed: int
    created_at: str
    control_origin: str
    gateway_origin: str
    catalog: CampaignCatalog
    fixture_manifest: FixtureManifest
    load_profile: LoadProfile

    @model_validator(mode="after")
    def validate_authority(self) -> Self:
        if _SHA.fullmatch(self.git_sha) is None:
            raise ValueError("Campaign plan git_sha 必须是完整 SHA")
        if _RELEASE.fullmatch(self.release_tag) is None:
            raise ValueError("Campaign plan release_tag 不是安全单段标识")
        NorthboundTargets(
            control_origin=self.control_origin,
            gateway_origin=self.gateway_origin,
        )
        fixture_ids = {item.fixture_id for item in self.fixture_manifest.fixtures}
        missing = sorted(
            {
                fixture_id
                for case in self.catalog.cases
                for fixture_id in case.fixture_ids
                if fixture_id != _SENTINEL_FIXTURE and fixture_id not in fixture_ids
            }
        )
        if missing:
            raise ValueError("fixture manifest 缺少目录必需项: " + ", ".join(missing))
        return self

    @property
    def targets(self) -> NorthboundTargets:
        return NorthboundTargets(
            control_origin=self.control_origin,
            gateway_origin=self.gateway_origin,
        )


def build_campaign_plan(
    *,
    release_tag: str,
    git_sha: str,
    seed: int,
    control_origin: str,
    gateway_origin: str,
    fixture_manifest: FixtureManifest,
    catalog: CampaignCatalog | None = None,
    load_profile: LoadProfile | None = None,
) -> CampaignPlan:
    campaign_id = derive_campaign_id(f"{release_tag}-{git_sha}", seed)
    return CampaignPlan(
        campaign_id=campaign_id,
        release_tag=release_tag,
        git_sha=git_sha,
        seed=seed,
        created_at=datetime.now(UTC).isoformat(),
        control_origin=control_origin,
        gateway_origin=gateway_origin,
        catalog=catalog or default_catalog(),
        fixture_manifest=fixture_manifest,
        load_profile=load_profile or default_load_profile(),
    )


def _read_regular(path: Path, *, max_bytes: int = 4 * 1024 * 1024) -> bytes:
    metadata = os.lstat(path)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"配置必须是普通文件且不能是符号链接: {path}")
    if metadata.st_uid != os.getuid():
        raise PermissionError(f"配置必须归当前 UID 所有: {path}")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise PermissionError(f"配置权限必须精确为 0600: {path}")
    if metadata.st_nlink != 1:
        raise PermissionError(f"配置必须是单硬链接文件: {path}")
    if metadata.st_size > max_bytes:
        raise ValueError(f"配置超过 {max_bytes} 字节: {path}")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise ValueError(f"配置在打开期间发生替换: {path}")
        if (
            opened.st_uid != metadata.st_uid
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_nlink != 1
        ):
            raise PermissionError(f"配置在打开期间的身份或权限不合法: {path}")
        content = os.read(descriptor, max_bytes + 1)
        after = os.fstat(descriptor)
        if (
            len(content) > max_bytes
            or after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
            or after.st_ctime_ns != opened.st_ctime_ns
        ):
            raise ValueError(f"配置在读取期间发生修改或超限: {path}")
        return content
    finally:
        os.close(descriptor)


def load_fixture_manifest(path: Path) -> FixtureManifest:
    content = _read_regular(path)
    try:
        if path.suffix.lower() in {".yaml", ".yml"}:
            document = yaml.safe_load(content)
        else:
            document = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError, yaml.YAMLError) as error:
        raise ValueError(f"fixture manifest 无法解析: {path}") from error
    return FixtureManifest.model_validate(document)


def load_campaign_plan(path: Path) -> CampaignPlan:
    try:
        document = json.loads(_read_regular(path))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Campaign plan 无法解析: {path}") from error
    return CampaignPlan.model_validate(document)


def publish_campaign_plan(path: Path, plan: CampaignPlan) -> None:
    atomic_write_report(
        path,
        json.dumps(
            plan.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    _read_regular(path)


def execution_path(release_root: Path, plan: CampaignPlan, case_id: str) -> Path:
    case = next((item for item in plan.catalog.cases if item.case_id == case_id), None)
    if case is None:
        raise ValueError(f"未知 Campaign 用例: {case_id}")
    path = release_root / case.evidence_path
    resolved_root = release_root.resolve()
    resolved_parent = path.parent.resolve()
    if resolved_root not in (resolved_parent, *resolved_parent.parents):
        raise ValueError("用例证据路径逃逸 release 根目录")
    return path


def read_case_executions(release_root: Path, plan: CampaignPlan) -> list[CaseExecution]:
    executions: list[CaseExecution] = []
    for case in plan.catalog.cases:
        path = execution_path(release_root, plan, case.case_id)
        if not path.exists():
            continue
        document = read_case_evidence(release_root, plan, case)
        executions.append(
            CaseExecution.model_validate(
                {
                    "case_id": case.case_id,
                    "status": document.get("status"),
                    "evidence_path": case.evidence_path,
                }
            )
        )
    return executions


def read_case_evidence(
    release_root: Path,
    plan: CampaignPlan,
    case: CaseSpec,
) -> dict[str, Any]:
    path = execution_path(release_root, plan, case.case_id)
    try:
        document: Any = json.loads(_read_regular(path))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"用例证据无法解析: {path}") from error
    if type(document) is not dict:
        raise ValueError(f"用例证据顶层必须是对象: {path}")
    expected_identity = {
        "schema_version": 1,
        "evidence_type": "extreme_load_campaign_case",
        "campaign_id": plan.campaign_id,
        "release_tag": plan.release_tag,
        "git_sha": plan.git_sha,
        "case_id": case.case_id,
        "phase": case.phase.value,
    }
    mismatched = [
        key for key, expected in expected_identity.items() if document.get(key) != expected
    ]
    if mismatched:
        raise ValueError(
            f"用例证据身份不属于当前 Campaign: {path}; "
            + ",".join(mismatched)
        )
    return document


def validate_release_executions(release_root: Path, plan: CampaignPlan) -> dict[str, object]:
    executions = read_case_executions(release_root, plan)
    return validate_case_executions(plan.catalog, executions).model_dump(mode="json")
