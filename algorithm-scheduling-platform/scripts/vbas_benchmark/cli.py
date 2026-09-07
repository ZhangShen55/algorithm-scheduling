from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import cast

import httpx
from vision_orchestrator_service.app.infrastructure.cache import VisionStream
from vision_orchestrator_service.app.infrastructure.capacity import (
    CapacityLeaseHttpClient,
)
from vision_orchestrator_service.app.infrastructure.vbas import (
    ControlVbasOfflineCapacitySource,
    VbasBatchClient,
    VbasBatchConfig,
    VbasOfflineCapacityGate,
)

from .dispatch import (
    DispatchEvent,
    batches_from_paths,
    run_dispatch_replay,
    stage_observer,
)
from .fixtures import (
    build_manifest,
    load_manifest,
    sha256_file,
    validate_manifest_files,
)
from .load import LoadRunConfig, run_sustained_load
from .media import run_media_feed_isolation
from .models import (
    BenchmarkGuardrails,
    BenchmarkIdentity,
    BenchmarkMode,
    VbasTarget,
    config_sha256,
)
from .report import (
    atomic_write_once_json,
    attempt_root,
    build_attempt_summary,
    identity_document,
)
from .telemetry import NvidiaDockerTelemetry

DEFAULT_TIERS = (8, 6, 4, 3, 2, 1)
DEFAULT_MEDIA_TIERS = (16, 12, 8, 6, 4, 2, 1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="VBas 充足供料吞吐基准")
    subparsers = parser.add_subparsers(dest="command", required=True)

    manifest = subparsers.add_parser("manifest", help="生成 fixture manifest")
    manifest.add_argument("--fixture-root", type=Path, required=True)
    manifest.add_argument("--teacher", action="append", type=Path, default=[])
    manifest.add_argument("--student", action="append", type=Path, default=[])
    manifest.add_argument("--empty", action="append", type=Path, default=[])
    manifest.add_argument("--multi-person", action="append", type=Path, default=[])
    manifest.add_argument("--source-video", action="append", default=[])
    manifest.add_argument("--output", type=Path, required=True)

    validate = subparsers.add_parser("validate-fixtures", help="校验 fixture 完整性")
    validate.add_argument("--fixture-root", type=Path, required=True)
    validate.add_argument("--manifest", type=Path, required=True)

    load = subparsers.add_parser("vbas", help="执行一档 VBas 持续供料")
    _add_common_identity_arguments(load)
    _add_fixture_arguments(load)
    load.add_argument("--target", action="append", required=True)
    load.add_argument("--mode", choices=[item.value for item in BenchmarkMode], required=True)
    load.add_argument("--concurrency", type=int, required=True)
    load.add_argument("--batch-size", type=int, default=8)
    load.add_argument("--warmup-seconds", type=float, default=60)
    load.add_argument("--steady-seconds", type=float, default=600)
    load.add_argument("--min-steady-batches", type=int, default=10_000)
    load.add_argument("--max-total-seconds", type=float, default=3_600)
    load.add_argument("--request-timeout-seconds", type=float, default=120)
    load.add_argument("--telemetry-interval-seconds", type=float, default=1)
    load.add_argument("--max-gpu-memory-mib", type=int, default=22_000)
    load.add_argument("--max-error-rate", type=float, default=0.01)
    load.add_argument("--effective-config-path", type=Path, required=True)

    media = subparsers.add_parser("media", help="执行纯媒体零延迟 sink 基准")
    _add_common_identity_arguments(media)
    media.add_argument("--video", type=Path, required=True)
    media.add_argument("--course-root", type=Path, default=Path("/data/course"))
    media.add_argument("--stream", choices=("teacher", "student"), required=True)
    media.add_argument("--active-streams", type=int, required=True)
    media.add_argument("--batch-size", type=int, default=8)
    media.add_argument("--frame-interval-seconds", type=float, default=10)
    media.add_argument("--max-concurrent-processes", type=int)

    dispatch = subparsers.add_parser("dispatch", help="执行预抽帧租约与分发回放")
    _add_common_identity_arguments(dispatch)
    _add_fixture_arguments(dispatch)
    dispatch.add_argument("--control-url", default="http://127.0.0.1:18100")
    dispatch.add_argument("--stream", choices=("teacher", "student"), required=True)
    dispatch.add_argument("--batch-size", type=int, default=8)
    dispatch.add_argument("--batch-count", type=int, default=100)
    dispatch.add_argument("--target-slots", type=int, required=True)
    dispatch.add_argument("--lease-ttl-seconds", type=int, default=30)

    preflight = subparsers.add_parser("preflight", help="采集三卡和 VBas 预检证据")
    _add_common_identity_arguments(preflight)
    _add_fixture_arguments(preflight)
    preflight.add_argument("--target", action="append", required=True)
    preflight.add_argument("--config-path", type=Path, required=True)
    preflight.add_argument("--control-url", default="http://127.0.0.1:18100")

    return parser


def _add_common_identity_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20260907)
    parser.add_argument("--git-sha")
    parser.add_argument("--report-root", type=Path, required=True)
    parser.add_argument("--case-id", required=True)


def _add_fixture_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--fixture-root-in-container", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "manifest":
        return _manifest(args)
    if args.command == "validate-fixtures":
        validate_manifest_files(load_manifest(args.manifest), args.fixture_root)
        return 0
    if args.command == "vbas":
        return asyncio.run(_run_vbas(args))
    if args.command == "media":
        return asyncio.run(_run_media(args))
    if args.command == "dispatch":
        return asyncio.run(_run_dispatch(args))
    if args.command == "preflight":
        return _preflight(args)
    raise AssertionError(f"未处理的命令: {args.command}")


def _manifest(args: argparse.Namespace) -> int:
    categorized = [
        *((path, "teacher") for path in args.teacher),
        *((path, "student") for path in args.student),
        *((path, "empty") for path in args.empty),
        *((path, "multi_person") for path in args.multi_person),
    ]
    source_videos = [
        {"url_sha256": hashlib.sha256(value.encode()).hexdigest()}
        for value in args.source_video
    ]
    document = build_manifest(
        args.fixture_root,
        categorized,
        source_videos=source_videos,
    ).to_document()
    atomic_write_once_json(args.output, document)
    return 0


async def _run_vbas(args: argparse.Namespace) -> int:
    targets = tuple(_parse_target(value) for value in args.target)
    manifest = load_manifest(args.manifest)
    validate_manifest_files(manifest, args.fixture_root)
    guardrails = BenchmarkGuardrails(
        max_error_rate=args.max_error_rate,
        max_gpu_memory_mib=args.max_gpu_memory_mib,
    )
    image_ids = _container_image_ids(targets)
    config_document = {
        "mode": args.mode,
        "concurrency": args.concurrency,
        "batch_size": args.batch_size,
        "warmup_seconds": args.warmup_seconds,
        "steady_seconds": args.steady_seconds,
        "min_steady_batches": args.min_steady_batches,
        "max_total_seconds": args.max_total_seconds,
        "targets": [target.instance_id for target in targets],
        "effective_config_sha256": sha256_file(args.effective_config_path),
    }
    identity = _identity(args, image_ids, config_document)
    root = attempt_root(args.report_root, identity, args.case_id)
    telemetry = NvidiaDockerTelemetry(targets, guardrails=guardrails)
    last_telemetry = 0.0
    last_phase: str | None = None

    async def sample_hook(phase: str, _records: int, now: float) -> None:
        nonlocal last_phase, last_telemetry
        if now - last_telemetry >= args.telemetry_interval_seconds:
            sample_phase = "M_warm" if phase == "steady" and last_phase == "warmup" else phase
            telemetry.collect(sample_phase)
            last_telemetry = now
            last_phase = phase

    telemetry.collect("M_ready")
    result = await run_sustained_load(
        targets=targets,
        manifest=manifest,
        fixture_root_in_container=str(args.fixture_root_in_container),
        config=LoadRunConfig(
            mode=BenchmarkMode(args.mode),
            concurrency_per_instance=args.concurrency,
            batch_size=args.batch_size,
            warmup_seconds=args.warmup_seconds,
            steady_seconds=args.steady_seconds,
            min_steady_batches=args.min_steady_batches,
            request_timeout_seconds=args.request_timeout_seconds,
            max_total_seconds=args.max_total_seconds,
            seed=args.seed,
            guardrails=guardrails,
        ),
        guardrail_probe=lambda: telemetry.latest_guardrail_reason,
        sample_hook=sample_hook,
    )
    telemetry.collect("test_end")
    identity_doc = identity_document(identity)
    load_doc = result.to_document()
    telemetry_doc = telemetry.to_document()
    summary = build_attempt_summary(
        identity=identity,
        case_id=args.case_id,
        load=load_doc,
        telemetry=telemetry_doc,
        fixture_manifest_sha256=sha256_file(args.manifest),
    )
    summary["effective_config_sha256"] = sha256_file(args.effective_config_path)
    atomic_write_once_json(root / "identity.json", identity_doc)
    atomic_write_once_json(root / "load.json", load_doc)
    atomic_write_once_json(root / "gpu.json", telemetry_doc)
    atomic_write_once_json(root / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["status"] == "passed" else 2


async def _run_media(args: argparse.Namespace) -> int:
    identity = _identity(args, ("not-applicable",), vars(args))
    root = attempt_root(args.report_root, identity, args.case_id)
    result = await run_media_feed_isolation(
        video_path=args.video,
        course_root=args.course_root,
        campaign_id=args.campaign_id,
        stream=(VisionStream.TEACHER if args.stream == "teacher" else VisionStream.STUDENT),
        active_streams=args.active_streams,
        batch_size=args.batch_size,
        sample_interval_seconds=args.frame_interval_seconds,
        max_concurrent_processes=args.max_concurrent_processes,
    )
    atomic_write_once_json(root / "identity.json", identity_document(identity))
    atomic_write_once_json(root / "media.json", result.to_document())
    return 0 if result.status == "passed" else 2


async def _run_dispatch(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.manifest)
    validate_manifest_files(manifest, args.fixture_root)
    stream = VisionStream.TEACHER if args.stream == "teacher" else VisionStream.STUDENT
    entries = [
        entry
        for entry in manifest.entries
        if entry.category in {args.stream, "empty", "multi_person"}
    ]
    if not entries:
        raise ValueError(f"manifest 不包含 {args.stream} fixture")
    paths = [
        args.fixture_root_in_container / entries[index % len(entries)].relative_path
        for index in range(args.batch_count * args.batch_size)
    ]
    batches = batches_from_paths(paths, batch_size=args.batch_size, stream=stream)
    events: list[DispatchEvent] = []
    async with httpx.AsyncClient(timeout=120) as http:
        source = ControlVbasOfflineCapacitySource(
            http,
            control_service_url=args.control_url,
            refresh_seconds=0.2,
        )
        gate = VbasOfflineCapacityGate(
            source,
            wait_timeout_seconds=300,
            retry_interval_seconds=0.2,
        )
        lease_client = CapacityLeaseHttpClient(
            http,
            control_service_url=args.control_url,
            acquire_wait_timeout_seconds=300,
            acquire_retry_interval_seconds=0.2,
            stage_observer=stage_observer(events),
        )
        client = VbasBatchClient(
            http,
            lease_client,
            config=VbasBatchConfig(
                batch_size=args.batch_size,
                lease_ttl_seconds=args.lease_ttl_seconds,
                request_timeout_seconds=120,
                capacity_retry_delay_seconds=0.2,
            ),
            capacity_gate=gate,
            stage_observer=stage_observer(events),
        )
        result = await run_dispatch_replay(
            client=client,
            stream=stream,
            batches=batches,
            target_slots=args.target_slots,
            campaign_id=args.campaign_id,
            stage_events=events,
        )
    identity = _identity(args, ("runtime-routed",), vars(args))
    root = attempt_root(args.report_root, identity, args.case_id)
    atomic_write_once_json(root / "identity.json", identity_document(identity))
    atomic_write_once_json(root / "dispatch.json", result.to_document())
    return 0 if result.status == "passed" else 2


def _preflight(args: argparse.Namespace) -> int:
    targets = tuple(_parse_target(value) for value in args.target)
    manifest = load_manifest(args.manifest)
    validate_manifest_files(manifest, args.fixture_root)
    image_ids = _container_image_ids(targets)
    identity = _identity(args, image_ids, vars(args))
    root = attempt_root(args.report_root, identity, args.case_id)
    gpu_inventory = _gpu_inventory()
    runtime = [
        _target_preflight(
            target,
            image_id=image_id,
            config_path=args.config_path,
            fixture_paths=tuple(
                args.fixture_root_in_container / entry.relative_path
                for entry in manifest.entries
            ),
            control_url=args.control_url,
        )
        for target, image_id in zip(targets, image_ids, strict=True)
    ]
    config_bytes = args.config_path.read_bytes()
    document = {
        "schema_version": 1,
        "evidence_type": "vbas_benchmark_preflight",
        "identity": identity_document(identity),
        "targets": runtime,
        "gpu_inventory": gpu_inventory,
        "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "fixture_manifest_sha256": sha256_file(args.manifest),
        "fixture_count": len(manifest.entries),
    }
    atomic_write_once_json(root / "identity.json", identity_document(identity))
    atomic_write_once_json(root / "preflight.json", document)
    passed = len(targets) == 3 and len({item.gpu_index for item in targets}) == 3
    passed = passed and all(bool(item["passed"]) for item in runtime)
    return 0 if passed else 2


def _target_preflight(
    target: VbasTarget,
    *,
    image_id: str,
    config_path: Path,
    fixture_paths: tuple[Path, ...],
    control_url: str,
) -> dict[str, object]:
    inspect_output = subprocess.run(
        ["docker", "inspect", target.container_name],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    ).stdout
    inspect_documents = cast(list[dict[str, object]], json.loads(inspect_output))
    if len(inspect_documents) != 1:
        raise ValueError(f"Docker inspect 结果数量异常: {target.container_name}")
    inspect_document = inspect_documents[0]
    host_config = cast(dict[str, object], inspect_document.get("HostConfig", {}))
    device_requests = cast(list[dict[str, object]], host_config.get("DeviceRequests", []))
    assigned_ids = [
        str(device_id)
        for request in device_requests
        for device_id in cast(list[object], request.get("DeviceIDs", []))
    ]
    mounts = cast(list[dict[str, object]], inspect_document.get("Mounts", []))
    mounted_config = next(
        (
            str(item.get("Source", ""))
            for item in mounts
            if item.get("Destination") == "/workspace/config.toml"
        ),
        "",
    )
    container_gpu_uuid = _command_output(
        [
            "docker",
            "exec",
            target.container_name,
            "nvidia-smi",
            "--query-gpu=uuid",
            "--format=csv,noheader",
        ]
    )
    host_gpu_uuid = _command_output(
        [
            "nvidia-smi",
            f"--id={target.gpu_index}",
            "--query-gpu=uuid",
            "--format=csv,noheader",
        ]
    )
    logs_writable = _command_succeeds(
        ["docker", "exec", target.container_name, "test", "-w", "/workspace/logs"]
    )
    fixtures_readable = all(
        _command_succeeds(
            ["docker", "exec", target.container_name, "test", "-r", str(path)]
        )
        for path in fixture_paths
    )
    health = _health(target)
    leases_response = httpx.get(
        f"{control_url.rstrip('/')}/ops/operator-instances/"
        f"{target.instance_id}/active-leases",
        timeout=10,
    )
    leases_response.raise_for_status()
    leases = cast(dict[str, object], leases_response.json())
    active_lease_count = leases.get("active_lease_count")
    checks = {
        "healthy": bool(health["healthy"]),
        "config_mount_matches": Path(mounted_config).resolve() == config_path.resolve(),
        "gpu_device_request_matches": assigned_ids == [str(target.gpu_index)],
        "gpu_uuid_matches": container_gpu_uuid == host_gpu_uuid,
        "logs_writable": logs_writable,
        "fixtures_readable": fixtures_readable,
        "active_leases_zero": active_lease_count == 0,
    }
    return {
        "instance_id": target.instance_id,
        "base_url": target.base_url,
        "gpu_index": target.gpu_index,
        "container_name": target.container_name,
        "image_id": image_id,
        "mounted_config": mounted_config,
        "assigned_gpu_ids": assigned_ids,
        "container_gpu_uuid": container_gpu_uuid,
        "host_gpu_uuid": host_gpu_uuid,
        "health": health,
        "active_lease_count": active_lease_count,
        "checks": checks,
        "passed": all(checks.values()),
    }


def _gpu_inventory() -> list[dict[str, object]]:
    output = _command_output(
        [
            "nvidia-smi",
            "--query-gpu=index,uuid,name,memory.total,memory.used,utilization.gpu,"
            "power.draw,driver_version",
            "--format=csv,noheader,nounits",
        ]
    )
    inventory: list[dict[str, object]] = []
    for line in output.splitlines():
        fields = [item.strip() for item in line.split(",")]
        if len(fields) != 8:
            raise ValueError("nvidia-smi GPU 清单字段数量异常")
        inventory.append(
            {
                "index": int(fields[0]),
                "uuid": fields[1],
                "name": fields[2],
                "memory_total_mib": int(fields[3]),
                "memory_used_mib": int(fields[4]),
                "utilization_gpu_percent": int(fields[5]),
                "power_draw_watts": float(fields[6]),
                "driver_version": fields[7],
            }
        )
    return inventory


def _command_output(command: list[str]) -> str:
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()


def _command_succeeds(command: list[str]) -> bool:
    return (
        subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        ).returncode
        == 0
    )


def _health(target: VbasTarget) -> dict[str, object]:
    response = httpx.get(f"{target.base_url.rstrip('/')}/AE/Health", timeout=10)
    return {
        "status_code": response.status_code,
        "healthy": response.status_code == 200,
    }


def _identity(
    args: argparse.Namespace,
    image_ids: tuple[str, ...],
    config_document: object,
) -> BenchmarkIdentity:
    git_sha = args.git_sha or subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    ).stdout.strip()
    serialized = json.dumps(config_document, default=str, sort_keys=True).encode()
    return BenchmarkIdentity(
        campaign_id=args.campaign_id,
        attempt=args.attempt,
        seed=args.seed,
        git_sha=git_sha,
        image_ids=image_ids,
        config_digest=config_sha256(serialized),
    )


def _parse_target(value: str) -> VbasTarget:
    fields = value.split(",")
    if len(fields) != 4:
        raise ValueError(
            "--target 格式必须为 instance_id,base_url,gpu_index,container_name"
        )
    return VbasTarget(fields[0], fields[1], int(fields[2]), fields[3])


def _container_image_ids(targets: tuple[VbasTarget, ...]) -> tuple[str, ...]:
    output = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{.Image}}",
            *[target.container_name for target in targets],
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    ).stdout
    values = tuple(line.strip() for line in output.splitlines() if line.strip())
    if len(values) != len(targets):
        raise ValueError("Docker 镜像 ID 数量与 VBas 目标数不一致")
    return values


if __name__ == "__main__":
    sys.exit(main())
