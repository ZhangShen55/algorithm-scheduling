from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GIT_SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
PROCESS_TIMEOUT_SECONDS = 30
CONTROL_SERVICE_URL = "http://control-service:18100"
LEGACY_ENVIRONMENT = {
    "PLATFORM_REGISTRATION_ENABLED": "legacy-must-not-win",
    "PLATFORM_CONTROL_SERVICE_URL": "http://legacy-environment.invalid:9999",
    "PLATFORM_HEARTBEAT_INTERVAL_SECONDS": "901.5",
    "PLATFORM_DECLARED_CAPACITY": "997",
    "REQUIRE_GPU": "legacy-must-not-win",
}


@dataclass(frozen=True, slots=True)
class OperatorProfile:
    operator_code: str
    project_directory: str
    local_config_name: str
    deploy_config_name: str
    default_capacity: int
    deploy_require_gpu: bool


OPERATOR_PROFILES = (
    OperatorProfile(
        "asr_offline", "asr_offline", "config.toml", "asr_offline.gpu.toml", 4, True
    ),
    OperatorProfile(
        "asr_online", "asr_online", "config.toml", "asr_online.gpu.toml", 10, True
    ),
    OperatorProfile(
        "facerec",
        "facerec",
        "config.example.toml",
        "facerec.gpu.toml",
        128,
        True,
    ),
    OperatorProfile(
        "ocr", "ocr", "config.toml.example", "ocr.gpu.toml", 256, True
    ),
    OperatorProfile(
        "screen_det", "screen_det", "config.toml", "screen_det.gpu.toml", 128, True
    ),
    OperatorProfile(
        "ppt_slice", "ppt_slice", "config.toml", "ppt_slice.cpu.toml", 10, False
    ),
    OperatorProfile(
        "vbas", "vbas", "config.toml", "vbas.gpu.toml", 128, True
    ),
    OperatorProfile(
        "text_analysis",
        "text_analysis",
        "config.example.toml",
        "text_analysis.cpu.toml",
        256,
        False,
    ),
)


PROBE_SOURCE = r'''
from __future__ import annotations

import importlib
import json
import os
import sys
import tomllib
import types
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from packages.operator_registry_client import load_operator_deployment_settings


def install_toml_compatibility_modules():
    try:
        import tomli  # noqa: F401
    except ModuleNotFoundError:
        sys.modules["tomli"] = tomllib

    try:
        import toml  # noqa: F401
    except ModuleNotFoundError:
        module = types.ModuleType("toml")

        def load(path):
            with Path(path).open("rb") as source:
                return tomllib.load(source)

        module.load = load
        sys.modules["toml"] = module


def install_screen_det_model_protection_stub():
    module = types.ModuleType("app.core.model_protection")

    @dataclass(frozen=True)
    class ModelProtectionConfig:
        enabled: bool = False
        encrypted_model_root: str = "/run/screen-det/models-encrypted"
        key_file: str = "/run/screen-det/models-encrypted/model.key"
        decrypted_temp_root: str = "/dev/shm/screen-det-models"
        cleanup_after_load: bool = True

    module.ModelProtectionConfig = ModelProtectionConfig
    sys.modules["app.core.model_protection"] = module


def load_actual_operator_settings(operator_code, config_path, default_capacity):
    install_toml_compatibility_modules()
    if operator_code in {
        "asr_offline",
        "asr_online",
        "facerec",
        "ppt_slice",
        "text_analysis",
    }:
        module = importlib.import_module("app.core.config")
        deployment = module.operator_deployment
    elif operator_code == "ocr":
        module = importlib.import_module("app.core.settings")
        # This probe checks configuration authority, not separately delivered models.
        with patch.object(Path, "is_dir", return_value=True):
            deployment = module.load_settings().operator_deployment
    elif operator_code == "screen_det":
        install_screen_det_model_protection_stub()
        module = importlib.import_module("app.core.config")
        deployment = module.get_settings().operator_deployment
    elif operator_code == "vbas":
        module = importlib.import_module("app.core.config_loader")
        selected = module.resolve_config_path()
        raw_config = module.load_config(selected)
        if not isinstance(raw_config, dict):
            raise SystemExit("VBas config loader did not return a mapping")
        deployment = load_operator_deployment_settings(
            selected,
            default_capacity=default_capacity,
        )
    else:
        raise SystemExit("unknown operator profile")
    return deployment

config_path = Path(sys.argv[1]).resolve()
default_capacity = int(sys.argv[2])
expected_environment = json.loads(sys.argv[3])
operator_code = sys.argv[4]
if not isinstance(expected_environment, dict) or not all(
    isinstance(name, str) and isinstance(value, str)
    for name, value in expected_environment.items()
):
    raise SystemExit("legacy environment contract is invalid")
if {name: os.environ.get(name) for name in expected_environment} != expected_environment:
    raise SystemExit("legacy environment was not injected into the child process")
if Path(os.environ.get("CONFIG_PATH", "")).resolve() != config_path:
    raise SystemExit("CONFIG_PATH does not select the requested TOML")

deployment = load_actual_operator_settings(
    operator_code,
    config_path,
    default_capacity,
)
print(json.dumps({
    "child_pid": os.getpid(),
    "config_path": str(config_path),
    "legacy_environment_injected": True,
    "legacy_environment_names": sorted(expected_environment),
    "settings": {
        "registration_enabled": deployment.platform.registration_enabled,
        "control_service_url": deployment.platform.control_service_url,
        "heartbeat_interval_seconds": deployment.platform.heartbeat_interval_seconds,
        "max_concurrent_requests": deployment.platform.max_concurrent_requests,
        "require_gpu": deployment.runtime.require_gpu,
    },
}, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
'''.strip()


class AuthorityProbeError(RuntimeError):
    pass


def _expected_settings(profile: OperatorProfile, mode: str) -> dict[str, Any]:
    if mode == "root":
        return {
            "registration_enabled": False,
            "control_service_url": "",
            "heartbeat_interval_seconds": 5.0,
            "max_concurrent_requests": profile.default_capacity,
            "require_gpu": False,
        }
    if mode == "controlled":
        return {
            "registration_enabled": True,
            "control_service_url": CONTROL_SERVICE_URL,
            "heartbeat_interval_seconds": 5.0,
            "max_concurrent_requests": profile.default_capacity,
            "require_gpu": profile.deploy_require_gpu,
        }
    raise AuthorityProbeError(f"未知配置模式: {mode}")


def _config_path(
    workspace_root: Path,
    platform_root: Path,
    profile: OperatorProfile,
    mode: str,
) -> Path:
    if mode == "root":
        path = (
            workspace_root
            / profile.project_directory
            / profile.local_config_name
        )
    elif mode == "controlled":
        path = platform_root / "deploy/config/operators" / profile.deploy_config_name
    else:
        raise AuthorityProbeError(f"未知配置模式: {mode}")
    resolved = path.resolve()
    if path.is_symlink() or not resolved.is_file():
        raise AuthorityProbeError(f"算子配置文件不存在或不安全: {path}")
    try:
        resolved.relative_to(workspace_root)
    except ValueError as error:
        raise AuthorityProbeError(f"算子配置文件越过工作区: {path}") from error
    return resolved


def _parse_child_output(
    completed: subprocess.CompletedProcess[str],
    *,
    config_path: Path,
    expected_settings: dict[str, Any],
) -> dict[str, Any]:
    if completed.returncode != 0:
        raise AuthorityProbeError("算子配置子进程失败")
    lines = completed.stdout.splitlines()
    if len(lines) != 1:
        raise AuthorityProbeError("算子配置子进程必须只输出一行 JSON")
    try:
        payload = json.loads(lines[0])
    except json.JSONDecodeError as error:
        raise AuthorityProbeError("算子配置子进程输出不是合法 JSON") from error
    if not isinstance(payload, dict):
        raise AuthorityProbeError("算子配置子进程输出必须是对象")
    expected_keys = {
        "child_pid",
        "config_path",
        "legacy_environment_injected",
        "legacy_environment_names",
        "settings",
    }
    if set(payload) != expected_keys:
        raise AuthorityProbeError("算子配置子进程输出字段不符合合同")
    child_pid = payload.get("child_pid")
    if type(child_pid) is not int or child_pid <= 0:
        raise AuthorityProbeError("算子配置子进程 PID 无效")
    if payload.get("config_path") != str(config_path):
        raise AuthorityProbeError("算子配置子进程读取了错误的 TOML")
    if payload.get("legacy_environment_injected") is not True:
        raise AuthorityProbeError("算子配置子进程未确认旧环境变量注入")
    if payload.get("legacy_environment_names") != sorted(LEGACY_ENVIRONMENT):
        raise AuthorityProbeError("算子配置子进程旧环境变量集合不完整")
    if payload.get("settings") != expected_settings:
        raise AuthorityProbeError("旧环境变量覆盖了 TOML 或 TOML 权威值不符合合同")
    return payload


def _run_process_probe(
    *,
    project_root: Path,
    config_path: Path,
    profile: OperatorProfile,
    mode: str,
) -> dict[str, Any]:
    expected_settings = _expected_settings(profile, mode)
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.update(LEGACY_ENVIRONMENT)
    environment["CONFIG_PATH"] = str(config_path)
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                PROBE_SOURCE,
                str(config_path),
                str(profile.default_capacity),
                json.dumps(LEGACY_ENVIRONMENT, sort_keys=True, separators=(",", ":")),
                profile.operator_code,
            ],
            cwd=project_root,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=PROCESS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise AuthorityProbeError("算子配置子进程超时") from error
    payload = _parse_child_output(
        completed,
        config_path=config_path,
        expected_settings=expected_settings,
    )
    return {
        "operator_code": profile.operator_code,
        "mode": mode,
        "config_path": str(config_path),
        "child_pid": payload["child_pid"],
        "legacy_environment_injected": True,
        "legacy_environment_names": sorted(LEGACY_ENVIRONMENT),
        "settings": payload["settings"],
    }


def _git_head(workspace_root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(workspace_root), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
        timeout=PROCESS_TIMEOUT_SECONDS,
    )
    value = completed.stdout.strip()
    if completed.returncode != 0 or GIT_SHA_PATTERN.fullmatch(value) is None:
        raise AuthorityProbeError("无法取得工作区完整 Git SHA")
    return value


def run_authority_probe(workspace_root: Path, *, git_sha: str) -> dict[str, Any]:
    root = workspace_root.expanduser().resolve()
    platform_root = root / "algorithm-scheduling-platform"
    if not platform_root.is_dir() or platform_root.is_symlink():
        raise AuthorityProbeError("算法调度平台目录不存在或不安全")
    if GIT_SHA_PATTERN.fullmatch(git_sha) is None:
        raise AuthorityProbeError("Git SHA 必须是 40 位小写十六进制")
    if _git_head(root) != git_sha:
        raise AuthorityProbeError("工作区 HEAD 与要求的 Git SHA 不一致")

    results: list[dict[str, Any]] = []
    for profile in OPERATOR_PROFILES:
        project_root = root / profile.project_directory
        if not project_root.is_dir() or project_root.is_symlink():
            raise AuthorityProbeError(
                f"算子项目目录不存在或不安全: {profile.project_directory}"
            )
        for mode in ("root", "controlled"):
            results.append(
                _run_process_probe(
                    project_root=project_root,
                    config_path=_config_path(root, platform_root, profile, mode),
                    profile=profile,
                    mode=mode,
                )
            )
    return {
        "schema_version": 1,
        "evidence_type": "operator_config_authority",
        "status": "PASS",
        "git_sha": git_sha,
        "created_at": datetime.now(UTC).isoformat(),
        "operator_count": len(OPERATOR_PROFILES),
        "process_count": len(results),
        "legacy_environment_names": sorted(LEGACY_ENVIRONMENT),
        "profiles": [asdict(profile) for profile in OPERATOR_PROFILES],
        "results": results,
    }


def _validate_authority_evidence(
    payload: object,
    *,
    workspace_root: Path,
    git_sha: str,
) -> dict[str, Any]:
    if type(payload) is not dict:
        raise AuthorityProbeError("已有算子配置权威证据必须是 JSON 对象")
    document = dict(payload)
    expected_fields = {
        "schema_version",
        "evidence_type",
        "status",
        "git_sha",
        "created_at",
        "operator_count",
        "process_count",
        "legacy_environment_names",
        "profiles",
        "results",
    }
    if set(document) != expected_fields:
        raise AuthorityProbeError("已有算子配置权威证据字段不符合合同")
    if (
        document.get("schema_version") != 1
        or document.get("evidence_type") != "operator_config_authority"
        or document.get("status") != "PASS"
        or document.get("git_sha") != git_sha
        or document.get("operator_count") != len(OPERATOR_PROFILES)
        or document.get("process_count") != len(OPERATOR_PROFILES) * 2
        or document.get("legacy_environment_names") != sorted(LEGACY_ENVIRONMENT)
        or document.get("profiles")
        != [asdict(profile) for profile in OPERATOR_PROFILES]
    ):
        raise AuthorityProbeError("已有算子配置权威证据身份或汇总不符合合同")
    created_at = document.get("created_at")
    try:
        parsed_created_at = datetime.fromisoformat(str(created_at))
    except ValueError as error:
        raise AuthorityProbeError("已有算子配置权威证据时间无效") from error
    if parsed_created_at.tzinfo is None:
        raise AuthorityProbeError("已有算子配置权威证据时间缺少时区")

    results = document.get("results")
    if not isinstance(results, list):
        raise AuthorityProbeError("已有算子配置权威证据结果不是数组")
    observed: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in results:
        if type(raw) is not dict:
            raise AuthorityProbeError("已有算子配置权威证据结果项不是对象")
        row = dict(raw)
        if set(row) != {
            "operator_code",
            "mode",
            "config_path",
            "child_pid",
            "legacy_environment_injected",
            "legacy_environment_names",
            "settings",
        }:
            raise AuthorityProbeError("已有算子配置权威证据结果字段不符合合同")
        key = (str(row.get("operator_code")), str(row.get("mode")))
        if key in observed:
            raise AuthorityProbeError("已有算子配置权威证据包含重复探针")
        observed[key] = row

    root = workspace_root.expanduser().resolve()
    platform_root = root / "algorithm-scheduling-platform"
    for profile in OPERATOR_PROFILES:
        for mode in ("root", "controlled"):
            key = (profile.operator_code, mode)
            observed_row = observed.get(key)
            expected_path = _config_path(root, platform_root, profile, mode)
            if (
                observed_row is None
                or observed_row.get("config_path") != str(expected_path)
                or type(observed_row.get("child_pid")) is not int
                or int(observed_row["child_pid"]) <= 0
                or observed_row.get("legacy_environment_injected") is not True
                or observed_row.get("legacy_environment_names")
                != sorted(LEGACY_ENVIRONMENT)
                or observed_row.get("settings") != _expected_settings(profile, mode)
            ):
                raise AuthorityProbeError(
                    f"已有算子配置权威证据探针不符合合同: {profile.operator_code}/{mode}"
                )
    if len(observed) != len(OPERATOR_PROFILES) * 2:
        raise AuthorityProbeError("已有算子配置权威证据探针集合不完整")
    return document


def _reuse_existing_evidence(
    path: Path,
    *,
    workspace_root: Path,
    git_sha: str,
) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise AuthorityProbeError(f"已有证据文件不存在或不安全: {path}")
    metadata = path.stat()
    if stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_nlink != 1:
        raise AuthorityProbeError("已有证据文件必须为 0600 且只有一个硬链接")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AuthorityProbeError("已有证据文件不是合法 JSON") from error
    if _git_head(workspace_root) != git_sha:
        raise AuthorityProbeError("工作区 HEAD 与已有证据 Git SHA 不一致")
    return _validate_authority_evidence(
        payload,
        workspace_root=workspace_root,
        git_sha=git_sha,
    )


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise AuthorityProbeError(f"证据文件已存在: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        content = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()
        view = memoryview(content)
        while view:
            view = view[os.write(descriptor, view) :]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    default_workspace = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(
        description="以独立进程验证八算子 TOML 配置权威和旧环境变量失效合同",
        allow_abbrev=False,
    )
    parser.add_argument("--workspace-root", type=Path, default=default_workspace)
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parse_args(argv)
    try:
        workspace_root = arguments.workspace_root.expanduser().resolve()
        output = (
            arguments.output.expanduser().resolve()
            if arguments.output is not None
            else None
        )
        if output is not None and (output.exists() or output.is_symlink()):
            _reuse_existing_evidence(
                output,
                workspace_root=workspace_root,
                git_sha=arguments.git_sha,
            )
            print(output)
            return 0
        payload = run_authority_probe(
            workspace_root,
            git_sha=arguments.git_sha,
        )
        if output is None:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            _atomic_json(output, payload)
            print(output)
    except (AuthorityProbeError, OSError) as error:
        print(f"operator config authority verification failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
