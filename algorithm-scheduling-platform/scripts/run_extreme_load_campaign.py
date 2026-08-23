#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

_PLATFORM_ROOT = Path(__file__).resolve().parents[1]
if str(_PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLATFORM_ROOT))

from scripts.extreme_load.coordinator import (  # noqa: E402
    CampaignCoordinator,
    CoordinatorBlockedError,
)
from scripts.extreme_load.plan import (  # noqa: E402
    build_campaign_plan,
    load_campaign_plan,
    load_fixture_manifest,
    publish_campaign_plan,
)
from scripts.extreme_load.stage_runtime import (  # noqa: E402
    STAGE_ADAPTER_NAMES,
    StageAdapterFactory,
)


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _print_json(document: object) -> None:
    print(json.dumps(document, ensure_ascii=False, sort_keys=True))


def _add_adapter_factory_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--adapter-factory",
        action="append",
        default=[],
        metavar="NAME=MODULE:CALLABLE",
        help=(
            "显式注册 media_download/metrics/fault/mixed/soak adapter factory；"
            "未注册时对应阶段保持 blocked"
        ),
    )


def _load_adapter_factories(specifications: Sequence[str]) -> dict[str, StageAdapterFactory]:
    factories: dict[str, StageAdapterFactory] = {}
    for specification in specifications:
        name, separator, reference = specification.partition("=")
        module_name, reference_separator, attribute_name = reference.partition(":")
        if (
            not separator
            or not reference_separator
            or name not in STAGE_ADAPTER_NAMES
            or not module_name
            or not attribute_name
        ):
            raise ValueError(
                "adapter factory 必须使用 NAME=MODULE:CALLABLE，且 NAME 属于 "
                + ",".join(sorted(STAGE_ADAPTER_NAMES))
            )
        if name in factories:
            raise ValueError(f"adapter factory 重复注册: {name}")
        try:
            factory = getattr(importlib.import_module(module_name), attribute_name)
        except (AttributeError, ImportError) as error:
            raise ValueError(f"adapter factory 无法加载: {name}") from error
        if not callable(factory):
            raise ValueError(f"adapter factory 不是可调用对象: {name}")
        factories[name] = cast(StageAdapterFactory, factory)
    return factories


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="创建、检查并逐案执行极限负载 Campaign",
        allow_abbrev=False,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create-plan", allow_abbrev=False)
    create.add_argument("--release-tag", required=True)
    create.add_argument("--git-sha", required=True)
    create.add_argument("--seed", type=int, required=True)
    create.add_argument("--control-origin", default="http://127.0.0.1:18100")
    create.add_argument("--gateway-origin", default="http://127.0.0.1:18103")
    create.add_argument("--fixture-manifest", type=_path, required=True)
    create.add_argument("--output", type=_path, required=True)

    for name in ("validate", "status"):
        command = subparsers.add_parser(name, allow_abbrev=False)
        command.add_argument("--plan", type=_path, required=True)
        command.add_argument("--release-root", type=_path, required=True)
        _add_adapter_factory_arguments(command)

    execute = subparsers.add_parser("execute-case", allow_abbrev=False)
    execute.add_argument("--plan", type=_path, required=True)
    execute.add_argument("--release-root", type=_path, required=True)
    execute.add_argument("--case-id", required=True)
    _add_adapter_factory_arguments(execute)
    execute.add_argument(
        "--allow-live-execution",
        action="store_true",
        help="显式允许执行器访问北向端点或调用已注册的阶段适配器",
    )
    return parser


def _coordinator(args: argparse.Namespace) -> CampaignCoordinator:
    return CampaignCoordinator(
        load_campaign_plan(args.plan),
        args.release_root,
        adapter_factories=_load_adapter_factories(args.adapter_factory),
    )


def _create_plan(args: argparse.Namespace) -> int:
    fixture_manifest = load_fixture_manifest(args.fixture_manifest)
    plan = build_campaign_plan(
        release_tag=args.release_tag,
        git_sha=args.git_sha,
        seed=args.seed,
        control_origin=args.control_origin,
        gateway_origin=args.gateway_origin,
        fixture_manifest=fixture_manifest,
    )
    publish_campaign_plan(args.output, plan)
    _print_json(
        {
            "status": "created",
            "campaign_id": plan.campaign_id,
            "git_sha": plan.git_sha,
            "plan": str(args.output),
        }
    )
    return 0


def _validate(args: argparse.Namespace) -> int:
    validation = _coordinator(args).validate()
    _print_json(
        {
            "status": "passed" if validation.passed else "failed",
            **validation.to_dict(),
        }
    )
    return 0 if validation.passed else 1


def _status(args: argparse.Namespace) -> int:
    status = _coordinator(args).status()
    _print_json({"status": "ok", **status.to_dict()})
    return 0


def _execute_case(args: argparse.Namespace) -> int:
    result = asyncio.run(
        _coordinator(args).execute_case(
            args.case_id,
            allow_live_execution=args.allow_live_execution,
        )
    )
    _print_json({"status": result.status, **result.to_dict()})
    return 0 if result.status == "passed" else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "create-plan":
            return _create_plan(args)
        if args.command == "validate":
            return _validate(args)
        if args.command == "status":
            return _status(args)
        if args.command == "execute-case":
            return _execute_case(args)
        raise ValueError(f"未知命令: {args.command}")
    except CoordinatorBlockedError as error:
        _print_json({"status": "blocked", "reason": str(error)})
        return 3
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        _print_json(
            {
                "status": "invalid",
                "error_type": type(error).__name__,
                "reason": str(error),
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
