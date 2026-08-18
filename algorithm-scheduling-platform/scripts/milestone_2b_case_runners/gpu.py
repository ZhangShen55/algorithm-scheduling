from __future__ import annotations

import argparse
import copy
import json
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from deploy.scripts import preflight_checks
from scripts.aggregate_milestone_2b_cases import (
    OperatorInstance,
    _load_release_json,
    load_operator_inventory,
    validate_gpu_pair,
)
from scripts.milestone_2b_case_catalog import CaseDefinition

from .base import CaseContext, CaseOutcome
from .deployment import (
    FoundationCaseSpec,
    _spec,
    run_foundation_case,
    run_foundation_cleanup,
)
from .safety import ResourceSpec


def _gpu_spec(
    title: str,
    expected: str,
    *,
    controlled: bool = False,
) -> FoundationCaseSpec:
    return _spec(
        title,
        expected,
        safety="canonical_runtime",
        timeout_seconds=180,
        mode="controlled_input" if controlled else "canonical_runtime",
    )


CASE_SPECS: Mapping[str, FoundationCaseSpec] = {
    "GPU-001": _gpu_spec("CUDA 容器看不到任何 GPU", "停止 GPU 算子部署", controlled=True),
    "GPU-002": _gpu_spec(
        "GPU0 Profile 容器能看到三张卡",
        "绑定失败，不允许启动算子",
        controlled=True,
    ),
    "GPU-003": _gpu_spec("GPU1 实例实际绑定物理 GPU0", "GPU 标签和 PID 对账失败"),
    "GPU-004": _gpu_spec("容器内 torch.cuda.device_count()!=1", "readiness 失败"),
    "GPU-005": _gpu_spec(
        "配置要求 GPU 但框架报告 CUDA 不可用",
        "启动 fail-fast，不回退 CPU",
    ),
    "GPU-006": _gpu_spec("模型参数或 Paddle 设备仍在 CPU", "readiness 或真实推理验收失败"),
    "GPU-007": _gpu_spec("真实推理时 nvidia-smi 无对应 PID", "不得认定 GPU 推理通过"),
    "GPU-008": _gpu_spec("nvidia-smi Process name 仍显示 python", "进程命名验收失败"),
    "GPU-009": _gpu_spec("Process name 与算子名不一致", "进程命名验收失败"),
    "GPU-010": _gpu_spec("asr_offline 名称出现在错误 GPU index", "物理卡绑定验收失败"),
    "GPU-011": _gpu_spec("容器停止后 CUDA PID 仍存在", "清理失败，暂停后续验证"),
    "GPU-012": _gpu_spec("模型启动时 GPU OOM", "保留日志和显存证据，不回退 CPU"),
    "GPU-013": _gpu_spec("六个模型可启动但并发推理 OOM", "记录峰值并暂停拓扑确认"),
    "GPU-014": _gpu_spec("GPU 温度、功耗或错误状态异常", "停止升压并记录硬件状态"),
    "GPU-015": _gpu_spec(
        "GPU2 3090 行为与 4090 D 不一致",
        "单独记录兼容性，不以其他卡结果代替",
    ),
    "GPU-016": _gpu_spec("GPU 进程 PID 无法映射回容器", "GPU 证据不完整，验收失败"),
    "GPU-017": _gpu_spec(
        "GPU 配置写成 cuda:1 但容器只暴露一张卡",
        "服务应失败，不静默改设备",
    ),
    "GPU-018": _gpu_spec("control 注册 GPU 标签与实际物理卡不同", "实例不可进入验收结果"),
    "GPU-019": _gpu_spec("一个 GPU 容器意外加载两份模型进程", "判为 worker 或启动方式违规"),
    "GPU-020": _gpu_spec(
        "GPU 实例执行 CPU 高负载但 GPU 利用率始终为零",
        "调查并判定未证明 GPU 推理",
    ),
}

_TARGET_CONTAINERS = {
    "GPU-001": "m2b-controlled-gpu-runtime",
    "GPU-002": "m2b-controlled-gpu-runtime",
    "GPU-003": "asr-offline-gpu1",
    "GPU-004": "asr-offline-gpu0",
    "GPU-005": "asr-offline-gpu0",
    "GPU-006": "ocr-gpu0",
    "GPU-007": "facerec-gpu0",
    "GPU-008": "facerec-gpu0",
    "GPU-009": "ocr-gpu0",
    "GPU-010": "asr-offline-gpu1",
    "GPU-011": "facerec-gpu0",
    "GPU-012": "facerec-gpu0",
    "GPU-013": "facerec-gpu0",
    "GPU-014": "facerec-gpu0",
    "GPU-015": "facerec-gpu2",
    "GPU-016": "facerec-gpu0",
    "GPU-017": "asr-offline-gpu0",
    "GPU-018": "facerec-gpu0",
    "GPU-019": "facerec-gpu0",
    "GPU-020": "facerec-gpu0",
}


def _gpu_resource_name(context: CaseContext, case: CaseDefinition) -> str:
    if case.case_id in {"GPU-001", "GPU-002"}:
        return f"m2b-{len(context.run_id)}-{context.run_id}-fake-gpu"
    return _TARGET_CONTAINERS[case.case_id]


def _gpu_scenario(context: CaseContext, case: CaseDefinition) -> dict[str, Any]:
    scenario: dict[str, Any] = {
        "mutation": {"case": case.case_id},
        "container": _gpu_resource_name(context, case),
        "gpu_verifier": "deploy/scripts/verify-gpu-instance",
    }
    controlled = {
        "GPU-001": {"visible_gpu_rows": ""},
        "GPU-002": {"container_gpu_count": 3, "profile_gpu": 0},
    }
    if case.case_id in controlled:
        scenario["fake_runtime"] = controlled[case.case_id]
    else:
        container = _TARGET_CONTAINERS[case.case_id]
        scenario.update(
            {
                "release_root": str(context.release_root),
                "git_sha": context.release_root.name,
                "operator_compose": "deploy/docker-compose.operators.yml",
                "running_evidence": f"gpu-instances/{container}.json",
                "stopped_evidence": f"recovery/{container}-stopped.json",
            }
        )
        if case.case_id == "GPU-018":
            scenario["registration_evidence"] = (
                "registration/"
                f"operator-registration-instance-{container}.json"
            )
    return scenario


def _gpu_resources(
    context: CaseContext, case: CaseDefinition
) -> tuple[ResourceSpec, ...]:
    return (ResourceSpec("container", _gpu_resource_name(context, case)),)


async def _run(
    context: CaseContext, case: CaseDefinition, case_id: str
) -> CaseOutcome:
    return await run_foundation_case(
        context=context,
        case=case,
        case_id=case_id,
        group="gpu",
        spec=CASE_SPECS[case_id],
        scenario_builder=_gpu_scenario,
        resource_builder=_gpu_resources,
    )


async def gpu_001(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "GPU-001")


async def gpu_002(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "GPU-002")


async def gpu_003(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "GPU-003")


async def gpu_004(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "GPU-004")


async def gpu_005(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "GPU-005")


async def gpu_006(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "GPU-006")


async def gpu_007(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "GPU-007")


async def gpu_008(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "GPU-008")


async def gpu_009(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "GPU-009")


async def gpu_010(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "GPU-010")


async def gpu_011(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "GPU-011")


async def gpu_012(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "GPU-012")


async def gpu_013(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "GPU-013")


async def gpu_014(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "GPU-014")


async def gpu_015(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "GPU-015")


async def gpu_016(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "GPU-016")


async def gpu_017(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "GPU-017")


async def gpu_018(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "GPU-018")


async def gpu_019(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "GPU-019")


async def gpu_020(context: CaseContext, case: CaseDefinition) -> CaseOutcome:
    return await _run(context, case, "GPU-020")


async def cleanup(context: CaseContext, case: CaseDefinition) -> None:
    spec = CASE_SPECS.get(case.case_id)
    if spec is None:
        raise ValueError("GPU cleanup case is not registered")
    await run_foundation_cleanup(
        context=context,
        case=case,
        group="gpu",
        spec=spec,
    )


for _case_id in CASE_SPECS:
    globals()[_case_id.lower().replace("-", "_")].cleanup = cleanup


def _fake_runtime(scenario: Mapping[str, Any]) -> dict[str, Any]:
    runtime = scenario.get("fake_runtime")
    if not isinstance(runtime, dict):
        raise ValueError("GPU 格式反例缺少 fake runtime")
    return runtime


_PLATFORM_ROOT = Path(__file__).resolve().parents[2]
_OPERATOR_COMPOSE = Path("deploy/docker-compose.operators.yml")

GpuBundle = tuple[
    OperatorInstance,
    dict[str, Any],
    dict[str, Any],
    dict[str, Any] | None,
    str,
]
GpuMutation = Callable[
    [dict[str, Any], dict[str, Any], dict[str, Any] | None, OperatorInstance],
    None,
]
GpuValidator = Callable[
    [OperatorInstance, dict[str, Any], dict[str, Any], dict[str, Any] | None, str],
    None,
]


def _fixed_release_json(
    scenario: Mapping[str, Any],
    *,
    release_root: Path,
    field: str,
    expected: Path,
) -> dict[str, Any]:
    raw_path = scenario.get(field)
    if raw_path != expected.as_posix():
        raise ValueError(
            f"GPU {field} path must remain fixed beneath release root: {expected}"
        )
    return _load_release_json(release_root, expected)


def _canonical_bundle(
    scenario: Mapping[str, Any], *, require_registration: bool = False
) -> GpuBundle:
    raw_root = scenario.get("release_root")
    git_sha = scenario.get("git_sha")
    container = scenario.get("container")
    if not isinstance(raw_root, str) or not Path(raw_root).is_absolute():
        raise ValueError("GPU canonical release root must be an absolute path")
    release_root = Path(raw_root)
    if not isinstance(git_sha, str) or git_sha != release_root.name:
        raise ValueError("GPU canonical git_sha must match release root")
    if scenario.get("operator_compose") != _OPERATOR_COMPOSE.as_posix():
        raise ValueError("GPU operator inventory path is not the fixed Compose path")
    if not isinstance(container, str):
        raise ValueError("GPU canonical target container is missing")

    inventory = load_operator_inventory(_PLATFORM_ROOT / _OPERATOR_COMPOSE)
    matches = [
        instance
        for instance in inventory.gpu_instances
        if instance.instance_id == container
    ]
    if len(matches) != 1:
        raise ValueError("GPU canonical target is not in operator inventory")
    instance = matches[0]
    running = _fixed_release_json(
        scenario,
        release_root=release_root,
        field="running_evidence",
        expected=Path(f"gpu-instances/{container}.json"),
    )
    stopped = _fixed_release_json(
        scenario,
        release_root=release_root,
        field="stopped_evidence",
        expected=Path(f"recovery/{container}-stopped.json"),
    )
    registration = None
    if require_registration:
        registration = _fixed_release_json(
            scenario,
            release_root=release_root,
            field="registration_evidence",
            expected=Path(
                "registration/"
                f"operator-registration-instance-{container}.json"
            ),
        )
    validate_gpu_pair(instance, running, stopped, git_sha)
    return instance, running, stopped, registration, git_sha


def _pair_validator(
    instance: OperatorInstance,
    running: dict[str, Any],
    stopped: dict[str, Any],
    registration: dict[str, Any] | None,
    git_sha: str,
) -> None:
    del registration
    validate_gpu_pair(instance, running, stopped, git_sha)


def _framework_validator(
    instance: OperatorInstance,
    running: dict[str, Any],
    stopped: dict[str, Any],
    registration: dict[str, Any] | None,
    git_sha: str,
) -> None:
    _pair_validator(instance, running, stopped, registration, git_sha)
    probe = running.get("cuda_probe")
    if not isinstance(probe, dict):
        raise ValueError("canonical GPU evidence is missing cuda_probe")
    if probe.get("framework_gpu_available") is not True:
        raise ValueError("算子框架 GPU 不可用")
    if "device_count" in probe and probe.get("device_count") != 1:
        raise ValueError("算子框架 device_count 必须等于 1")
    if "current_device" in probe and probe.get("current_device") != 0:
        raise ValueError("算子框架当前设备必须是 cuda:0")


def _no_oom_validator(
    instance: OperatorInstance,
    running: dict[str, Any],
    stopped: dict[str, Any],
    registration: dict[str, Any] | None,
    git_sha: str,
) -> None:
    _pair_validator(instance, running, stopped, registration, git_sha)
    failure = running.get("failure")
    if failure is None:
        return
    if not isinstance(failure, dict):
        raise ValueError("GPU failure evidence must be an object or null")
    error = failure.get("error")
    if not isinstance(error, str) or (
        "oom" not in error.lower() and "out of memory" not in error.lower()
    ):
        raise ValueError("GPU failure evidence is not an OOM")
    if failure.get("cpu_fallback") is not False:
        raise ValueError("GPU OOM evidence must prove no CPU fallback")
    for field in ("log_artifact", "memory_artifact"):
        value = failure.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"GPU OOM evidence is missing {field}")
    stage = failure.get("stage")
    if stage not in {"startup", "concurrent_inference"}:
        raise ValueError("GPU OOM evidence stage is invalid")
    raise ValueError(f"GPU {stage} OOM rejected without CPU fallback")


def _hardware_validator(
    instance: OperatorInstance,
    running: dict[str, Any],
    stopped: dict[str, Any],
    registration: dict[str, Any] | None,
    git_sha: str,
) -> None:
    _pair_validator(instance, running, stopped, registration, git_sha)
    hardware = running.get("hardware")
    if not isinstance(hardware, dict):
        raise ValueError("canonical GPU evidence is missing hardware state")
    samples = running.get("synchronous_samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("GPU hardware synchronous sample evidence is missing")
    numeric_fields = (
        "temperature_c",
        "temperature_limit_c",
        "power_watts",
        "power_limit_watts",
    )
    sample_hardware: list[dict[str, Any]] = []
    for sample in samples:
        measurement = sample.get("hardware") if isinstance(sample, dict) else None
        if not isinstance(measurement, dict):
            raise ValueError("GPU hardware synchronous sample evidence is missing")
        if any(
            not isinstance(measurement.get(field), (int, float))
            or isinstance(measurement.get(field), bool)
            for field in numeric_fields
        ) or not isinstance(measurement.get("hardware_slowdown"), bool):
            raise ValueError("GPU hardware synchronous sample is invalid")
        sample_hardware.append(measurement)
    expected = {
        "temperature_c": max(item["temperature_c"] for item in sample_hardware),
        "temperature_limit_c": min(
            item["temperature_limit_c"] for item in sample_hardware
        ),
        "power_watts": max(item["power_watts"] for item in sample_hardware),
        "power_limit_watts": min(
            item["power_limit_watts"] for item in sample_hardware
        ),
        "hardware_slowdown": any(
            item["hardware_slowdown"] for item in sample_hardware
        ),
    }
    if hardware != expected:
        raise ValueError("GPU hardware summary does not match synchronous samples")
    if any(
        not isinstance(hardware.get(field), (int, float))
        or isinstance(hardware.get(field), bool)
        for field in numeric_fields
    ) or not isinstance(hardware.get("hardware_slowdown"), bool):
        raise ValueError("GPU hardware state has invalid measurements")
    if (
        hardware["temperature_c"] >= hardware["temperature_limit_c"]
        or hardware["power_watts"] >= hardware["power_limit_watts"]
        or hardware["hardware_slowdown"]
    ):
        raise ValueError("GPU hardware temperature, power, or error state is unsafe")


def _registration_validator(
    instance: OperatorInstance,
    running: dict[str, Any],
    stopped: dict[str, Any],
    registration: dict[str, Any] | None,
    git_sha: str,
) -> None:
    _pair_validator(instance, running, stopped, registration, git_sha)
    if not isinstance(registration, dict):
        raise ValueError("canonical GPU registration evidence is missing")
    if registration.get("instance_id") != instance.instance_id:
        raise ValueError("GPU registration instance does not match inventory")
    labels = registration.get("labels")
    if not isinstance(labels, dict) or labels.get("gpu") != str(
        instance.physical_gpu
    ):
        raise ValueError("control GPU label does not match physical GPU")


def _single_model_validator(
    instance: OperatorInstance,
    running: dict[str, Any],
    stopped: dict[str, Any],
    registration: dict[str, Any] | None,
    git_sha: str,
) -> None:
    processes = [
        process
        for sample in running["synchronous_samples"]
        for process in sample["processes"]
    ]
    pids = {process["host_pid"] for process in processes}
    if len(pids) != 1:
        raise ValueError("GPU container has more than one model process")
    _pair_validator(instance, running, stopped, registration, git_sha)


def _utilization_validator(
    instance: OperatorInstance,
    running: dict[str, Any],
    stopped: dict[str, Any],
    registration: dict[str, Any] | None,
    git_sha: str,
) -> None:
    _pair_validator(instance, running, stopped, registration, git_sha)
    utilization = running.get("utilization")
    if not isinstance(utilization, dict):
        raise ValueError("canonical GPU evidence is missing utilization")
    samples = running.get("synchronous_samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("GPU utilization synchronous sample evidence is missing")
    sample_gpu: list[float] = []
    sample_cpu: list[float] = []
    sample_target_sm: list[float] = []
    for sample in samples:
        if not isinstance(sample, dict):
            raise ValueError("GPU utilization synchronous sample evidence is missing")
        gpu_percent = sample.get("gpu_utilization_percent")
        processes = sample.get("processes")
        if (
            not isinstance(gpu_percent, (int, float))
            or isinstance(gpu_percent, bool)
            or not isinstance(processes, list)
            or not processes
        ):
            raise ValueError("GPU utilization synchronous sample is invalid")
        sample_gpu.append(float(gpu_percent))
        for process in processes:
            cpu_percent = process.get("cpu_percent") if isinstance(process, dict) else None
            process_gpu = (
                process.get("gpu_utilization") if isinstance(process, dict) else None
            )
            if (
                not isinstance(cpu_percent, (int, float))
                or isinstance(cpu_percent, bool)
                or not isinstance(process_gpu, dict)
            ):
                raise ValueError("GPU utilization synchronous sample is invalid")
            expected_process_fields = {
                "sm_percent",
                "memory_percent",
                "encoder_percent",
                "decoder_percent",
            }
            if set(process_gpu) != expected_process_fields or any(
                process_gpu.get(field) is not None
                and (
                    not isinstance(process_gpu.get(field), (int, float))
                    or isinstance(process_gpu.get(field), bool)
                    or not 0 <= process_gpu[field] <= 100
                )
                for field in expected_process_fields
            ):
                raise ValueError("GPU process utilization synchronous sample is invalid")
            sample_cpu.append(float(cpu_percent))
            if process_gpu["sm_percent"] is not None:
                sample_target_sm.append(float(process_gpu["sm_percent"]))
    expected = {
        "cpu_percent": max(sample_cpu),
        "gpu_percent": max(sample_gpu),
        "target_sm_percent": max(sample_target_sm) if sample_target_sm else None,
    }
    if utilization != expected:
        raise ValueError("GPU utilization summary does not match synchronous samples")
    cpu = utilization.get("cpu_percent")
    target_sm = utilization.get("target_sm_percent")
    if (
        not isinstance(cpu, (int, float))
        or isinstance(cpu, bool)
        or (
            target_sm is not None
            and (
                not isinstance(target_sm, (int, float))
                or isinstance(target_sm, bool)
            )
        )
    ):
        raise ValueError("GPU utilization measurements are invalid")
    if cpu > 80 and target_sm == 0:
        raise ValueError(
            "CPU is busy while mapped target PID SM utilization remains zero"
        )


def _compatibility_validator(
    instance: OperatorInstance,
    running: dict[str, Any],
    stopped: dict[str, Any],
    registration: dict[str, Any] | None,
    git_sha: str,
) -> None:
    _pair_validator(instance, running, stopped, registration, git_sha)
    if instance.physical_gpu != 2 or instance.instance_id != "facerec-gpu2":
        raise ValueError("GPU-015 canonical target must be facerec-gpu2")
    compatibility = running.get("compatibility")
    if not isinstance(compatibility, dict) or set(compatibility) != {
        "gpu",
        "trigger",
        "result",
    }:
        raise ValueError("GPU2 compatibility evidence is missing")
    identity = compatibility.get("gpu")
    expected_identity_fields = {
        "physical_index",
        "physical_uuid",
        "product_name",
        "compute_capability",
        "driver_version",
        "driver_cuda_version",
        "container_cuda_runtime_version",
    }
    if not isinstance(identity, dict) or set(identity) != expected_identity_fields:
        raise ValueError("GPU2 compatibility identity is invalid")
    running_gpu = running.get("gpu")
    if (
        not isinstance(running_gpu, dict)
        or identity.get("physical_index") != running_gpu.get("physical_index")
        or identity.get("physical_uuid") != running_gpu.get("physical_uuid")
    ):
        raise ValueError("GPU2 compatibility identity does not match canonical GPU")
    if (
        identity.get("product_name") != "NVIDIA GeForce RTX 3090"
        or identity.get("compute_capability") != "8.6"
    ):
        raise ValueError("GPU2 compatibility requires RTX 3090 compute capability 8.6")
    version_pattern = re.compile(r"[0-9]+(?:\.[0-9]+)+")
    if any(
        not isinstance(identity.get(field), str)
        or version_pattern.fullmatch(identity[field]) is None
        for field in (
            "driver_version",
            "driver_cuda_version",
            "container_cuda_runtime_version",
        )
    ):
        raise ValueError("GPU2 compatibility driver or CUDA runtime version is invalid")
    probe = running.get("cuda_probe")
    if (
        not isinstance(probe, dict)
        or identity.get("container_cuda_runtime_version")
        != probe.get("container_cuda_runtime_version")
    ):
        raise ValueError("GPU2 compatibility container CUDA runtime does not match probe")

    activity = running.get("activity")
    trigger = compatibility.get("trigger")
    if (
        not isinstance(activity, dict)
        or not isinstance(trigger, dict)
        or set(trigger) != {"instance_id", "operator_code", "run_id"}
        or trigger.get("instance_id") != instance.instance_id
        or trigger.get("operator_code") != instance.operator_code
        or trigger.get("run_id") != activity.get("run_id")
        or activity.get("instance_id") != instance.instance_id
        or activity.get("operator_code") != instance.operator_code
    ):
        raise ValueError("GPU2 compatibility trigger is not its canonical real trigger")

    samples = running.get("synchronous_samples")
    result = compatibility.get("result")
    if not isinstance(samples, list) or not samples or not isinstance(result, dict):
        raise ValueError("GPU2 independent compatibility result is missing")
    try:
        target_sm_values = [
            process["gpu_utilization"]["sm_percent"]
            for sample in samples
            for process in sample["processes"]
            if process["gpu_utilization"]["sm_percent"] is not None
        ]
        target_sm_max = max(target_sm_values) if target_sm_values else None
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("GPU2 compatibility samples are invalid") from exc
    expected_result = {
        "status": "PASS",
        "real_trigger_completed": True,
        "sample_count": len(samples),
        "target_sm_max_percent": target_sm_max,
    }
    if result != expected_result:
        raise ValueError(
            "GPU2 independent compatibility result does not match its trigger samples"
        )


def _assert_isolated_rejection(
    *,
    scenario: Mapping[str, Any],
    case_id: str,
    fact_key: str,
    expected_fragment: str,
    mutation: GpuMutation,
    validator: GpuValidator = _pair_validator,
    require_registration: bool = False,
) -> dict[str, Any]:
    instance, running, stopped, registration, git_sha = _canonical_bundle(
        scenario, require_registration=require_registration
    )
    validator(instance, running, stopped, registration, git_sha)
    canonical_snapshot = copy.deepcopy((running, stopped, registration))
    mutated_running = copy.deepcopy(running)
    mutated_stopped = copy.deepcopy(stopped)
    mutated_registration = copy.deepcopy(registration)
    mutation(mutated_running, mutated_stopped, mutated_registration, instance)
    try:
        validator(
            instance,
            mutated_running,
            mutated_stopped,
            mutated_registration,
            git_sha,
        )
    except ValueError as exc:
        detail = str(exc)
        if expected_fragment not in detail:
            raise ValueError(
                f"{case_id} mutation rejection lacked precise reason: "
                f"{expected_fragment}"
            ) from exc
    else:
        raise ValueError(f"{case_id} isolated mutation was not rejected")
    if (running, stopped, registration) != canonical_snapshot:
        raise ValueError(f"{case_id} modified canonical GPU evidence")
    return {
        "canonical_pair_valid": True,
        "mutation_case": case_id,
        fact_key: True,
        "rejection_detail": detail,
    }


def _check_gpu_001(scenario: Mapping[str, Any]) -> dict[str, Any]:
    runtime = _fake_runtime(scenario)
    output = runtime.get("visible_gpu_rows")
    if not isinstance(output, str):
        raise ValueError("fake runtime GPU 行格式错误")
    try:
        preflight_checks.validate_gpu_output(output)
    except preflight_checks.PreflightError as exc:
        if "found 0 valid GPU records" not in str(exc):
            raise ValueError("未观察到零 GPU 的具体预检原因") from exc
        visible_rows = [line for line in output.splitlines() if line.strip()]
        return {"visible_gpu_records": len(visible_rows), "detail": str(exc)}
    raise ValueError("零 GPU fake runtime 未被预检拒绝")


def _check_gpu_002(scenario: Mapping[str, Any]) -> dict[str, Any]:
    runtime = _fake_runtime(scenario)
    count = runtime.get("container_gpu_count")
    profile = runtime.get("profile_gpu")
    if count == 1 or count != 3 or profile != 0:
        raise ValueError("GPU0 多卡可见反例未形成精确三卡事实")
    service = {
        "deploy": {
            "resources": {
                "reservations": {
                    "devices": [
                        {
                            "driver": "nvidia",
                            "device_ids": ["0", "1", "2"],
                            "capabilities": ["gpu"],
                        }
                    ]
                }
            }
        }
    }
    try:
        preflight_checks._validate_gpu_service(
            "m2b-controlled-gpu-runtime",
            service,
            {"PLATFORM_GPU_ID": "0", "NVIDIA_VISIBLE_DEVICES": "0"},
            "gpu0",
        )
    except preflight_checks.PreflightError as exc:
        detail = str(exc)
        if "reservation does not match profile" not in detail:
            raise ValueError("三卡反例没有得到精确绑定拒绝") from exc
        return {
            "profile_gpu": profile,
            "container_gpu_count": count,
            "binding_rejection": detail,
        }
    raise ValueError("三卡反例未被 GPU Compose validator 拒绝")


def _check_gpu_003(scenario: Mapping[str, Any]) -> dict[str, Any]:
    def mutate(
        running: dict[str, Any],
        stopped: dict[str, Any],
        registration: dict[str, Any] | None,
        instance: OperatorInstance,
    ) -> None:
        del stopped, registration, instance
        running["gpu"]["physical_index"] = 0

    return _assert_isolated_rejection(
        scenario=scenario,
        case_id="GPU-003",
        fact_key="physical_gpu_rejection",
        expected_fragment="physical_index does not match inventory",
        mutation=mutate,
    )


def _check_gpu_004(scenario: Mapping[str, Any]) -> dict[str, Any]:
    def mutate(
        running: dict[str, Any],
        stopped: dict[str, Any],
        registration: dict[str, Any] | None,
        instance: OperatorInstance,
    ) -> None:
        del stopped, registration, instance
        running["cuda_probe"]["device_count"] = 2

    return _assert_isolated_rejection(
        scenario=scenario,
        case_id="GPU-004",
        fact_key="device_count_rejection",
        expected_fragment="device_count 必须等于 1",
        mutation=mutate,
        validator=_framework_validator,
    )


def _check_gpu_005(scenario: Mapping[str, Any]) -> dict[str, Any]:
    def mutate(
        running: dict[str, Any],
        stopped: dict[str, Any],
        registration: dict[str, Any] | None,
        instance: OperatorInstance,
    ) -> None:
        del stopped, registration, instance
        running["cuda_probe"]["framework_gpu_available"] = False

    return _assert_isolated_rejection(
        scenario=scenario,
        case_id="GPU-005",
        fact_key="cuda_unavailable_rejection",
        expected_fragment="算子框架 GPU 不可用",
        mutation=mutate,
        validator=_framework_validator,
    )


def _check_gpu_006(scenario: Mapping[str, Any]) -> dict[str, Any]:
    def mutate(
        running: dict[str, Any],
        stopped: dict[str, Any],
        registration: dict[str, Any] | None,
        instance: OperatorInstance,
    ) -> None:
        del stopped, registration, instance
        running["cuda_probe"]["current_device"] = None
        running["cuda_probe"]["reported_device"] = "cpu"

    return _assert_isolated_rejection(
        scenario=scenario,
        case_id="GPU-006",
        fact_key="cpu_device_rejection",
        expected_fragment="当前设备必须是 cuda:0",
        mutation=mutate,
        validator=_framework_validator,
    )


def _check_gpu_007(scenario: Mapping[str, Any]) -> dict[str, Any]:
    def mutate(
        running: dict[str, Any],
        stopped: dict[str, Any],
        registration: dict[str, Any] | None,
        instance: OperatorInstance,
    ) -> None:
        del stopped, registration, instance
        for sample in running["synchronous_samples"]:
            sample["processes"] = []

    return _assert_isolated_rejection(
        scenario=scenario,
        case_id="GPU-007",
        fact_key="missing_pid_rejection",
        expected_fragment="at least one host_pid",
        mutation=mutate,
    )


def _check_gpu_008(scenario: Mapping[str, Any]) -> dict[str, Any]:
    def mutate(
        running: dict[str, Any],
        stopped: dict[str, Any],
        registration: dict[str, Any] | None,
        instance: OperatorInstance,
    ) -> None:
        del stopped, registration, instance
        running["synchronous_samples"][0]["processes"][0]["process_name"] = (
            "python"
        )

    return _assert_isolated_rejection(
        scenario=scenario,
        case_id="GPU-008",
        fact_key="python_process_name_rejection",
        expected_fragment="process_name does not match inventory",
        mutation=mutate,
    )


def _check_gpu_009(scenario: Mapping[str, Any]) -> dict[str, Any]:
    def mutate(
        running: dict[str, Any],
        stopped: dict[str, Any],
        registration: dict[str, Any] | None,
        instance: OperatorInstance,
    ) -> None:
        del stopped, registration, instance
        running["synchronous_samples"][0]["processes"][0]["process_name"] = (
            "unexpected_operator"
        )

    return _assert_isolated_rejection(
        scenario=scenario,
        case_id="GPU-009",
        fact_key="operator_process_name_rejection",
        expected_fragment="process_name does not match inventory",
        mutation=mutate,
    )


def _check_gpu_010(scenario: Mapping[str, Any]) -> dict[str, Any]:
    def mutate(
        running: dict[str, Any],
        stopped: dict[str, Any],
        registration: dict[str, Any] | None,
        instance: OperatorInstance,
    ) -> None:
        del stopped, registration, instance
        running["gpu"]["physical_index"] = 0

    return _assert_isolated_rejection(
        scenario=scenario,
        case_id="GPU-010",
        fact_key="asr_gpu_binding_rejection",
        expected_fragment="physical_index does not match inventory",
        mutation=mutate,
    )


def _check_gpu_011(scenario: Mapping[str, Any]) -> dict[str, Any]:
    def mutate(
        running: dict[str, Any],
        stopped: dict[str, Any],
        registration: dict[str, Any] | None,
        instance: OperatorInstance,
    ) -> None:
        del running, registration, instance
        stopped["remaining_cuda_pids"] = [stopped["prior_cuda_pids"][0]]

    return _assert_isolated_rejection(
        scenario=scenario,
        case_id="GPU-011",
        fact_key="remaining_pid_rejection",
        expected_fragment="remaining_cuda_pids=[]",
        mutation=mutate,
    )


def _check_gpu_012(scenario: Mapping[str, Any]) -> dict[str, Any]:
    def mutate(
        running: dict[str, Any],
        stopped: dict[str, Any],
        registration: dict[str, Any] | None,
        instance: OperatorInstance,
    ) -> None:
        del stopped, registration, instance
        running["failure"] = {
            "stage": "startup",
            "error": "CUDA out of memory",
            "cpu_fallback": False,
            "log_artifact": "gpu-failures/GPU-012.log",
            "memory_artifact": "gpu-failures/GPU-012-memory.json",
        }

    return _assert_isolated_rejection(
        scenario=scenario,
        case_id="GPU-012",
        fact_key="startup_oom_rejection",
        expected_fragment="GPU startup OOM rejected without CPU fallback",
        mutation=mutate,
        validator=_no_oom_validator,
    )


def _check_gpu_013(scenario: Mapping[str, Any]) -> dict[str, Any]:
    def mutate(
        running: dict[str, Any],
        stopped: dict[str, Any],
        registration: dict[str, Any] | None,
        instance: OperatorInstance,
    ) -> None:
        del stopped, registration, instance
        running["failure"] = {
            "stage": "concurrent_inference",
            "error": "CUDA OOM at concurrency six",
            "cpu_fallback": False,
            "log_artifact": "gpu-failures/GPU-013.log",
            "memory_artifact": "gpu-failures/GPU-013-peak-memory.json",
        }

    return _assert_isolated_rejection(
        scenario=scenario,
        case_id="GPU-013",
        fact_key="concurrent_oom_rejection",
        expected_fragment="GPU concurrent_inference OOM rejected",
        mutation=mutate,
        validator=_no_oom_validator,
    )


def _check_gpu_014(scenario: Mapping[str, Any]) -> dict[str, Any]:
    def mutate(
        running: dict[str, Any],
        stopped: dict[str, Any],
        registration: dict[str, Any] | None,
        instance: OperatorInstance,
    ) -> None:
        del stopped, registration, instance
        for sample in running["synchronous_samples"]:
            sample["hardware"]["temperature_c"] = sample["hardware"][
                "temperature_limit_c"
            ]
        running["hardware"]["temperature_c"] = max(
            sample["hardware"]["temperature_c"]
            for sample in running["synchronous_samples"]
        )

    return _assert_isolated_rejection(
        scenario=scenario,
        case_id="GPU-014",
        fact_key="hardware_state_rejection",
        expected_fragment="temperature, power, or error state is unsafe",
        mutation=mutate,
        validator=_hardware_validator,
    )


def _check_gpu_015(scenario: Mapping[str, Any]) -> dict[str, Any]:
    def mutate(
        running: dict[str, Any],
        stopped: dict[str, Any],
        registration: dict[str, Any] | None,
        instance: OperatorInstance,
    ) -> None:
        del stopped, registration
        del instance
        running["compatibility"]["gpu"]["product_name"] = (
            "NVIDIA GeForce RTX 4090 D"
        )
        running["compatibility"]["gpu"]["compute_capability"] = "8.9"

    return _assert_isolated_rejection(
        scenario=scenario,
        case_id="GPU-015",
        fact_key="gpu2_compatibility_rejection",
        expected_fragment="GPU2 compatibility requires RTX 3090",
        mutation=mutate,
        validator=_compatibility_validator,
    )


def _check_gpu_016(scenario: Mapping[str, Any]) -> dict[str, Any]:
    def mutate(
        running: dict[str, Any],
        stopped: dict[str, Any],
        registration: dict[str, Any] | None,
        instance: OperatorInstance,
    ) -> None:
        del stopped, registration, instance
        running["synchronous_samples"][0]["processes"][0]["mapping"][
            "docker_top"
        ] = False

    return _assert_isolated_rejection(
        scenario=scenario,
        case_id="GPU-016",
        fact_key="pid_mapping_rejection",
        expected_fragment="mapping.docker_top must be boolean true",
        mutation=mutate,
    )


def _check_gpu_017(scenario: Mapping[str, Any]) -> dict[str, Any]:
    def mutate(
        running: dict[str, Any],
        stopped: dict[str, Any],
        registration: dict[str, Any] | None,
        instance: OperatorInstance,
    ) -> None:
        del stopped, registration, instance
        running["cuda_probe"]["configured_device"] = "cuda:1"
        running["cuda_probe"]["current_device"] = 1

    return _assert_isolated_rejection(
        scenario=scenario,
        case_id="GPU-017",
        fact_key="container_device_rejection",
        expected_fragment="当前设备必须是 cuda:0",
        mutation=mutate,
        validator=_framework_validator,
    )


def _check_gpu_018(scenario: Mapping[str, Any]) -> dict[str, Any]:
    def mutate(
        running: dict[str, Any],
        stopped: dict[str, Any],
        registration: dict[str, Any] | None,
        instance: OperatorInstance,
    ) -> None:
        del running, stopped, instance
        if registration is None:
            raise ValueError("GPU registration mutation lacks evidence")
        registration["labels"]["gpu"] = "1"

    return _assert_isolated_rejection(
        scenario=scenario,
        case_id="GPU-018",
        fact_key="registration_label_rejection",
        expected_fragment="control GPU label does not match physical GPU",
        mutation=mutate,
        validator=_registration_validator,
        require_registration=True,
    )


def _check_gpu_019(scenario: Mapping[str, Any]) -> dict[str, Any]:
    def mutate(
        running: dict[str, Any],
        stopped: dict[str, Any],
        registration: dict[str, Any] | None,
        instance: OperatorInstance,
    ) -> None:
        del stopped, registration, instance
        process = copy.deepcopy(
            running["synchronous_samples"][0]["processes"][0]
        )
        process["host_pid"] += 1
        process["container_pid"] += 1
        process["mapping"]["nspid"] = [
            process["host_pid"],
            process["container_pid"],
        ]
        running["synchronous_samples"][0]["processes"].append(process)

    return _assert_isolated_rejection(
        scenario=scenario,
        case_id="GPU-019",
        fact_key="duplicate_model_rejection",
        expected_fragment="more than one model process",
        mutation=mutate,
        validator=_single_model_validator,
    )


def _check_gpu_020(scenario: Mapping[str, Any]) -> dict[str, Any]:
    def mutate(
        running: dict[str, Any],
        stopped: dict[str, Any],
        registration: dict[str, Any] | None,
        instance: OperatorInstance,
    ) -> None:
        del stopped, registration, instance
        for sample in running["synchronous_samples"]:
            sample["gpu_utilization_percent"] = 90
            for process in sample["processes"]:
                process["cpu_percent"] = 95
                process["gpu_utilization"]["sm_percent"] = 0
        running["utilization"] = {
            "cpu_percent": 95,
            "gpu_percent": 90,
            "target_sm_percent": 0,
        }

    return _assert_isolated_rejection(
        scenario=scenario,
        case_id="GPU-020",
        fact_key="zero_gpu_utilization_rejection",
        expected_fragment=(
            "CPU is busy while mapped target PID SM utilization remains zero"
        ),
        mutation=mutate,
        validator=_utilization_validator,
    )


_GPU_CHECKERS: Mapping[
    str, Callable[[Mapping[str, Any]], dict[str, Any]]
] = {
    "GPU-001": _check_gpu_001,
    "GPU-002": _check_gpu_002,
    "GPU-003": _check_gpu_003,
    "GPU-004": _check_gpu_004,
    "GPU-005": _check_gpu_005,
    "GPU-006": _check_gpu_006,
    "GPU-007": _check_gpu_007,
    "GPU-008": _check_gpu_008,
    "GPU-009": _check_gpu_009,
    "GPU-010": _check_gpu_010,
    "GPU-011": _check_gpu_011,
    "GPU-012": _check_gpu_012,
    "GPU-013": _check_gpu_013,
    "GPU-014": _check_gpu_014,
    "GPU-015": _check_gpu_015,
    "GPU-016": _check_gpu_016,
    "GPU-017": _check_gpu_017,
    "GPU-018": _check_gpu_018,
    "GPU-019": _check_gpu_019,
    "GPU-020": _check_gpu_020,
}


def evaluate_scenario(case_id: str, scenario: Mapping[str, Any]) -> dict[str, Any]:
    spec = CASE_SPECS.get(case_id)
    checker = _GPU_CHECKERS.get(case_id)
    mutation = scenario.get("mutation")
    if (
        spec is None
        or checker is None
        or scenario.get("schema_version") != 1
        or scenario.get("case_id") != case_id
        or scenario.get("mode") != spec.mode
        or not isinstance(mutation, dict)
        or mutation.get("case") != case_id
    ):
        return {
            "case_id": case_id,
            "status": "失败",
            "reason": "GPU 输入与固定 checker 不匹配",
            "observed": {"input_valid": False},
        }
    try:
        observed = checker(scenario)
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "case_id": case_id,
            "status": "失败",
            "reason": f"GPU checker 未观察到目标状态：{exc}",
            "observed": {"checker": checker.__name__, "detail": str(exc)},
        }
    return {
        "case_id": case_id,
        "status": spec.status,
        "reason": spec.reason,
        "observed": {"checker": checker.__name__, **observed},
    }


def checker_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--check", required=True, choices=sorted(CASE_SPECS))
    parser.add_argument("--input", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        document = json.loads(arguments.input.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("checker input must be a JSON object")
        result = evaluate_scenario(arguments.check, document)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        result = {
            "case_id": arguments.check,
            "status": "失败",
            "reason": f"GPU checker 输入失败：{exc}",
            "observed": {"input_valid": False},
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "通过" else 1


if __name__ == "__main__":
    raise SystemExit(checker_main())
