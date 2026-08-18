from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import time
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from typing import get_type_hints

import pytest


def test_base_contracts_are_frozen_and_keep_the_required_shape(tmp_path: Path) -> None:
    from scripts.milestone_2b_case_runners.base import (
        CaseContext,
        CaseOutcome,
        CaseRunner,
    )
    from scripts.milestone_2b_case_runners.process import (
        CommandSpec,
        ReadAction,
    )
    from scripts.milestone_2b_case_runners.safety import (
        ResourceSpec,
    )

    context = CaseContext(release_root=tmp_path, run_id="run-1", target="local")
    outcome = CaseOutcome(status="通过", reason="ok", evidence=(Path("result.json"),))
    resource = ResourceSpec(kind="filesystem", name="/dev/null")
    command = CommandSpec(action=ReadAction(kind="filesystem_file", resource=resource))
    resource = ResourceSpec(kind="container", name="m2b-run-1-worker")

    assert get_type_hints(CaseRunner.run)["return"] == CaseOutcome
    assert tuple(field.name for field in fields(CaseContext)) == (
        "release_root",
        "run_id",
        "target",
    )
    assert context.release_root == tmp_path
    assert outcome.evidence == (Path("result.json"),)
    assert command.argv == ("cat", "--", "/dev/null")
    assert resource.kind == "container"
    with pytest.raises(FrozenInstanceError):
        context.target = "changed"  # type: ignore[misc]


def test_case_outcome_rejects_unknown_status() -> None:
    from scripts.milestone_2b_case_runners.base import CaseOutcome

    with pytest.raises(ValueError, match="status"):
        CaseOutcome(status="unknown", reason="bad", evidence=())  # type: ignore[arg-type]


def test_case_outcome_requires_plain_strings_and_snapshots_evidence() -> None:
    from scripts.milestone_2b_case_runners.base import CaseOutcome

    class DerivedString(str):
        pass

    with pytest.raises(ValueError, match="status|plain string"):
        CaseOutcome(
            status=DerivedString("失败"),  # type: ignore[arg-type]
            reason="bad",
            evidence=(),
        )
    with pytest.raises(ValueError, match="reason|plain string"):
        CaseOutcome(
            status="失败",
            reason=DerivedString("bad"),
            evidence=(),
        )

    evidence = [Path("negative/evidence/DEP-001/result.json")]
    outcome = CaseOutcome(status="通过", reason="ok", evidence=evidence)
    evidence.append(Path("negative/evidence/DEP-001/late.json"))

    assert outcome.evidence == (
        Path("negative/evidence/DEP-001/result.json"),
    )


def _release_root(tmp_path: Path) -> Path:
    root = tmp_path / "releases" / "v1.0_260818" / ("a" * 40)
    root.mkdir(parents=True)
    return root


@pytest.mark.parametrize(
    ("mutation_kind", "resource_kind"),
    (
        ("docker_remove", "container"),
        ("database_drop", "database"),
        ("redis_delete_prefix", "redis_prefix"),
        ("kafka_delete_topic", "kafka_topic"),
    ),
)
@pytest.mark.asyncio
async def test_read_only_rejects_all_structured_mutations(
    tmp_path: Path,
    mutation_kind: str,
    resource_kind: str,
) -> None:
    from scripts.milestone_2b_case_runners.base import (
        CaseContext,
    )
    from scripts.milestone_2b_case_runners.process import (
        CommandSpec,
        MutationAction,
        validate_command_spec,
    )
    from scripts.milestone_2b_case_runners.safety import (
        ResourceSpec,
        _case_execution_scope,
    )

    context = CaseContext(_release_root(tmp_path), "run-1", "local")
    resource = ResourceSpec(
        kind=resource_kind,  # type: ignore[arg-type]
        name="production-resource",
    )
    async with _case_execution_scope(context, "read_only", None):
        with pytest.raises(ValueError, match="read_only"):
            validate_command_spec(
                context=context,
                command=CommandSpec(
                    action=MutationAction(
                        kind=mutation_kind,  # type: ignore[arg-type]
                        resource=resource,
                    )
                ),
            )


def test_typed_action_rejects_a_resource_that_changes_after_validation() -> None:
    from scripts.milestone_2b_case_runners.process import MutationAction, ReadAction

    class SwitchingResource:
        kind = "container"

        def __init__(self) -> None:
            self.reads = 0

        @property
        def name(self) -> str:
            self.reads += 1
            if self.reads == 1:
                return "m2b-run-1-safe"
            return "production-container"

    with pytest.raises(ValueError, match="ResourceSpec|resource type"):
        MutationAction(
            kind="docker_remove",
            resource=SwitchingResource(),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="ResourceSpec|resource type"):
        ReadAction(
            kind="docker_container",
            resource=SwitchingResource(),  # type: ignore[arg-type]
        )


def test_resource_rejects_str_subclass_namespace_bypass() -> None:
    from scripts.milestone_2b_case_runners.safety import ResourceSpec

    class NamespaceBypass(str):
        def startswith(
            self,
            prefix: str | tuple[str, ...],
            start: int = 0,
            end: int | None = None,
        ) -> bool:
            return True

    with pytest.raises(ValueError, match="plain string|resource name"):
        ResourceSpec(
            kind="container",
            name=NamespaceBypass("production-container"),
        )


@pytest.mark.asyncio
async def test_command_validation_rematerializes_mutated_resource(
    tmp_path: Path,
) -> None:
    from scripts.milestone_2b_case_runners.base import CaseContext
    from scripts.milestone_2b_case_runners.process import (
        CommandSpec,
        MutationAction,
        validate_command_spec,
    )
    from scripts.milestone_2b_case_runners.safety import (
        ResourceSpec,
        _case_execution_scope,
    )

    resource = ResourceSpec(kind="container", name="m2b-run-1-safe")
    command = CommandSpec(
        action=MutationAction(kind="docker_remove", resource=resource)
    )
    assert command.argv == ("docker", "rm", "--", "m2b-run-1-safe")

    object.__setattr__(resource, "name", "production-container")

    context = CaseContext(_release_root(tmp_path), "run-1", "local")
    async with _case_execution_scope(context, "isolated_mutation", None):
        with pytest.raises(ValueError, match="run namespace|isolated"):
            validate_command_spec(context=context, command=command)


@pytest.mark.asyncio
async def test_run_command_ignores_tampered_cached_argv(tmp_path: Path) -> None:
    import sys

    from scripts.milestone_2b_case_runners.base import CaseContext
    from scripts.milestone_2b_case_runners.process import (
        CommandSpec,
        ReadAction,
        run_command,
    )
    from scripts.milestone_2b_case_runners.safety import (
        ResourceSpec,
        _case_execution_scope,
    )

    command = CommandSpec(
        action=ReadAction(
            kind="filesystem_file",
            resource=ResourceSpec(kind="filesystem", name="/dev/null"),
        )
    )
    with pytest.raises(AttributeError):
        object.__setattr__(
            command,
            "_argv",
            (sys.executable, "-c", "print('ARBITRARY_ARGV_EXECUTED')"),
        )
    context = CaseContext(_release_root(tmp_path), "run-1", "local")
    async with _case_execution_scope(context, "read_only", None):
        result = await run_command(
            context=context,
            command=command,
            timeout_seconds=2,
        )

    assert result.argv == ("cat", "--", "/dev/null")
    assert result.stdout == b""


@pytest.mark.asyncio
async def test_command_validation_rejects_duck_typed_command(
    tmp_path: Path,
) -> None:
    from scripts.milestone_2b_case_runners.base import CaseContext
    from scripts.milestone_2b_case_runners.process import validate_command_spec
    from scripts.milestone_2b_case_runners.safety import (
        ResourceSpec,
        _case_execution_scope,
    )

    class DuckCommand:
        operation = "docker_mutation"
        resources = (ResourceSpec("container", "m2b-run-1-safe"),)
        argv = ("docker", "rm", "--", "production-container")

    context = CaseContext(_release_root(tmp_path), "run-1", "local")
    async with _case_execution_scope(context, "isolated_mutation", None):
        with pytest.raises(ValueError, match="CommandSpec|command type"):
            validate_command_spec(
                context=context,
                command=DuckCommand(),  # type: ignore[arg-type]
            )


@pytest.mark.parametrize(
    ("resource_kind", "valid_name", "invalid_name"),
    (
        ("container", "m2b-5-run-1-worker", "shared-worker"),
        ("kafka_topic", "m2b.run-1.events", "shared.events"),
        ("kafka_group", "m2b.run-1.consumer", "shared.consumer"),
        ("redis_prefix", "m2b:run-1:case:", "shared:case:"),
        ("database", "m2b_5_run_1_case_test", "production"),
    ),
)
@pytest.mark.asyncio
async def test_isolated_mutation_requires_current_run_namespace(
    tmp_path: Path,
    resource_kind: str,
    valid_name: str,
    invalid_name: str,
) -> None:
    from scripts.milestone_2b_case_runners.base import (
        CaseContext,
    )
    from scripts.milestone_2b_case_runners.process import (
        CommandSpec,
        MutationAction,
        mutation_kind_for_resource,
        validate_command_spec,
    )
    from scripts.milestone_2b_case_runners.safety import (
        ResourceSpec,
        _case_execution_scope,
    )

    context = CaseContext(_release_root(tmp_path), "run-1", "local")
    mutation_kind = mutation_kind_for_resource(resource_kind)
    async with _case_execution_scope(context, "isolated_mutation", None):
        validate_command_spec(
            context=context,
            command=CommandSpec(
                action=MutationAction(
                    kind=mutation_kind,
                    resource=ResourceSpec(
                        kind=resource_kind,  # type: ignore[arg-type]
                        name=valid_name,
                    ),
                )
            ),
        )
        with pytest.raises(ValueError, match="run namespace|isolated"):
            validate_command_spec(
                context=context,
                command=CommandSpec(
                    action=MutationAction(
                        kind=mutation_kind,
                        resource=ResourceSpec(
                            kind=resource_kind,  # type: ignore[arg-type]
                            name=invalid_name,
                        ),
                    )
                ),
                )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("resource_kind", "resource_name"),
    (
        ("container", "m2b-run-other-worker"),
        ("database", "m2b_run_other_case_test"),
    ),
)
async def test_isolated_namespace_rejects_another_run_with_the_same_prefix(
    tmp_path: Path,
    resource_kind: str,
    resource_name: str,
) -> None:
    from scripts.milestone_2b_case_runners.base import CaseContext
    from scripts.milestone_2b_case_runners.process import (
        CommandSpec,
        MutationAction,
        mutation_kind_for_resource,
        validate_command_spec,
    )
    from scripts.milestone_2b_case_runners.safety import (
        ResourceSpec,
        _case_execution_scope,
    )

    context = CaseContext(_release_root(tmp_path), "run", "local")
    command = CommandSpec(
        action=MutationAction(
            kind=mutation_kind_for_resource(resource_kind),
            resource=ResourceSpec(
                kind=resource_kind,  # type: ignore[arg-type]
                name=resource_name,
            ),
        )
    )
    async with _case_execution_scope(context, "isolated_mutation", None):
        with pytest.raises(ValueError, match="namespace|isolated"):
            validate_command_spec(context=context, command=command)


@pytest.mark.asyncio
async def test_execution_capability_rejects_mutated_context_namespace(
    tmp_path: Path,
) -> None:
    from scripts.milestone_2b_case_runners.base import CaseContext
    from scripts.milestone_2b_case_runners.process import (
        CommandSpec,
        MutationAction,
        validate_command_spec,
    )
    from scripts.milestone_2b_case_runners.safety import (
        ResourceSpec,
        _case_execution_scope,
    )

    context = CaseContext(_release_root(tmp_path), "run-1", "local")
    async with _case_execution_scope(context, "isolated_mutation", None):
        object.__setattr__(context, "run_id", "other-run")
        with pytest.raises(ValueError, match="context|capability|changed"):
            validate_command_spec(
                context=context,
                command=CommandSpec(
                    action=MutationAction(
                        kind="docker_remove",
                        resource=ResourceSpec(
                            kind="container",
                            name="m2b-other-run-production",
                        ),
                    )
                ),
            )


def test_task3_actions_reject_filesystem_mutation_even_through_run_symlink(
    tmp_path: Path,
) -> None:
    from scripts.milestone_2b_case_runners.base import (
        CaseContext,
    )
    from scripts.milestone_2b_case_runners.process import (
        CommandSpec,
        MutationAction,
        mutation_kind_for_resource,
    )
    from scripts.milestone_2b_case_runners.safety import (
        ResourceSpec,
    )

    context = CaseContext(_release_root(tmp_path), "run-1", "local")
    run_root = context.release_root / ".runs" / context.run_id
    run_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (run_root / "escape").symlink_to(outside, target_is_directory=True)
    escaped_path = run_root / "escape" / "victim.json"

    with pytest.raises(ValueError, match="unsupported"):
        mutation_kind_for_resource("filesystem")
    with pytest.raises(ValueError, match="action|resource"):
        CommandSpec(
            action=MutationAction(
                kind="filesystem_touch",  # type: ignore[arg-type]
                resource=ResourceSpec(kind="filesystem", name=str(escaped_path)),
            )
        )
    assert tuple(outside.iterdir()) == ()


@pytest.mark.asyncio
async def test_canonical_runtime_requires_held_release_maintenance_lock(
    tmp_path: Path,
) -> None:
    from scripts.milestone_2b_case_runners.base import (
        CaseContext,
    )
    from scripts.milestone_2b_case_runners.process import (
        CommandSpec,
        ReadAction,
        validate_command_spec,
    )
    from scripts.milestone_2b_case_runners.safety import (
        MaintenanceLockGuard,
        ResourceSpec,
        _case_execution_scope,
    )

    release_root = _release_root(tmp_path)
    unlocked = CaseContext(release_root, "run-1", "local")
    command = CommandSpec(
        action=ReadAction(
            kind="filesystem_file",
            resource=ResourceSpec(kind="filesystem", name="/dev/null"),
        )
    )
    async with _case_execution_scope(unlocked, "canonical_runtime", None):
        with pytest.raises(ValueError, match="maintenance lock"):
            validate_command_spec(context=unlocked, command=command)

    guard = MaintenanceLockGuard(release_root)
    with guard:
        locked = CaseContext(release_root, "run-1", "local")
        async with _case_execution_scope(locked, "canonical_runtime", guard):
            validate_command_spec(context=locked, command=command)
        lock_path = release_root.parent / ".operator-lifecycle.lock"
        metadata = os.lstat(lock_path)
        assert metadata.st_nlink == 1
        assert metadata.st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_maintenance_lock_rejects_unlink_and_inode_rebinding(
    tmp_path: Path,
) -> None:
    from scripts.milestone_2b_case_runners.base import CaseContext
    from scripts.milestone_2b_case_runners.process import (
        CommandSpec,
        ReadAction,
        validate_command_spec,
    )
    from scripts.milestone_2b_case_runners.safety import (
        MaintenanceLockGuard,
        ResourceSpec,
        _case_execution_scope,
    )

    release_root = _release_root(tmp_path)
    context = CaseContext(release_root, "run-1", "local")
    command = CommandSpec(
        action=ReadAction(
            kind="filesystem_file",
            resource=ResourceSpec(kind="filesystem", name="/dev/null"),
        )
    )
    guard = MaintenanceLockGuard(release_root)
    with guard:
        async with _case_execution_scope(
            context,
            "canonical_runtime",
            guard,
        ):
            lock_path = release_root.parent / ".operator-lifecycle.lock"
            lock_path.unlink()
            lock_path.write_text("replacement\n", encoding="utf-8")
            lock_path.chmod(0o600)

            with pytest.raises(ValueError, match="maintenance lock"):
                validate_command_spec(context=context, command=command)
            with pytest.raises(ValueError, match="another|maintenance lock"):
                with MaintenanceLockGuard(release_root):
                    pass


def test_maintenance_lock_is_nonblocking_and_exclusive(tmp_path: Path) -> None:
    from scripts.milestone_2b_case_runners.safety import MaintenanceLockGuard

    release_root = _release_root(tmp_path)
    with MaintenanceLockGuard(release_root):
        with pytest.raises(ValueError, match="another|maintenance lock"):
            with MaintenanceLockGuard(release_root):
                pass


@pytest.mark.asyncio
async def test_command_output_is_bounded(tmp_path: Path) -> None:
    from scripts.milestone_2b_case_runners.base import CaseContext
    from scripts.milestone_2b_case_runners.process import (
        CommandSpec,
        OutputProbeAction,
        run_command,
    )
    from scripts.milestone_2b_case_runners.safety import (
        _case_execution_scope,
    )

    context = CaseContext(_release_root(tmp_path), "run-1", "local")
    async with _case_execution_scope(context, "read_only", None):
        result = await run_command(
            context=context,
            command=CommandSpec(
                action=OutputProbeAction(stdout_bytes=10_000, stderr_bytes=10_000),
                max_output_bytes=64,
            ),
            timeout_seconds=5,
        )

    assert result.returncode == 0
    assert result.stdout == b"x" * 64
    assert result.stderr == b"y" * 64
    assert result.stdout_truncated is True
    assert result.stderr_truncated is True


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.asyncio
async def test_timeout_terminates_then_kills_the_process_group(
    tmp_path: Path,
) -> None:
    from scripts.milestone_2b_case_runners.base import CaseContext
    from scripts.milestone_2b_case_runners.process import (
        CaseCommandTimeout,
        CommandSpec,
        ProcessGroupProbeAction,
        run_command,
    )
    from scripts.milestone_2b_case_runners.safety import _case_execution_scope

    release_root = _release_root(tmp_path)
    context = CaseContext(release_root, "run-1", "local")
    async with _case_execution_scope(context, "isolated_mutation", None):
        with pytest.raises(CaseCommandTimeout, match="timed out") as caught:
            await run_command(
                context=context,
                command=CommandSpec(
                    action=ProcessGroupProbeAction(
                        spawn_child=True,
                        parent_exits=False,
                        ignore_sigterm=True,
                    )
                ),
                timeout_seconds=0.3,
                terminate_grace_seconds=0.1,
            )

    pids = json.loads(caught.value.result.stdout)
    assert not _process_exists(pids["parent"])
    assert not _process_exists(pids["child"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("timeout_seconds", "terminate_grace_seconds"),
    (
        (float("nan"), 0.1),
        (float("inf"), 0.1),
        (True, 0.1),
        (type("DerivedFloat", (float,), {})(0.1), 0.1),
        (1.0, float("nan")),
        (1.0, float("inf")),
        (1.0, True),
        (1.0, type("DerivedFloat", (float,), {})(0.1)),
        (1.0, 30.1),
    ),
)
async def test_run_command_rejects_nonfinite_or_nonbase_timing_values_before_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    timeout_seconds: float,
    terminate_grace_seconds: float,
) -> None:
    import asyncio

    from scripts.milestone_2b_case_runners.base import CaseContext
    from scripts.milestone_2b_case_runners.process import (
        CommandSpec,
        ReadAction,
        run_command,
    )
    from scripts.milestone_2b_case_runners.safety import (
        ResourceSpec,
        _case_execution_scope,
    )

    async def reject_spawn(*args: object, **kwargs: object) -> object:
        raise AssertionError("invalid timing value reached subprocess creation")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", reject_spawn)
    context = CaseContext(_release_root(tmp_path), "run-1", "local")
    command = CommandSpec(
        action=ReadAction(
            kind="filesystem_file",
            resource=ResourceSpec(kind="filesystem", name="/dev/null"),
        )
    )
    async with _case_execution_scope(context, "read_only", None):
        with pytest.raises(ValueError, match="timeout|grace|finite|plain"):
            await run_command(
                context=context,
                command=command,
                timeout_seconds=timeout_seconds,
                terminate_grace_seconds=terminate_grace_seconds,
            )


@pytest.mark.asyncio
async def test_process_supervisor_registration_failure_reaps_spawned_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from scripts.milestone_2b_case_runners import process as process_module
    from scripts.milestone_2b_case_runners.base import CaseContext
    from scripts.milestone_2b_case_runners.process import (
        CommandSpec,
        ProcessCleanupError,
        ProcessGroupProbeAction,
        run_command,
    )
    from scripts.milestone_2b_case_runners.safety import _case_execution_scope

    original_create_subprocess_exec = asyncio.create_subprocess_exec
    spawned: list[asyncio.subprocess.Process] = []

    async def capture_process(*args: object, **kwargs: object) -> object:
        child = await original_create_subprocess_exec(*args, **kwargs)  # type: ignore[arg-type]
        spawned.append(child)
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture_process)
    read_descriptor, write_descriptor = os.pipe()
    os.close(read_descriptor)
    process_module._configure_process_supervisor(write_descriptor)
    os.close(write_descriptor)
    context = CaseContext(_release_root(tmp_path), "run-1", "local")
    try:
        async with _case_execution_scope(context, "read_only", None):
            with pytest.raises(ProcessCleanupError, match="supervisor"):
                await asyncio.wait_for(
                    run_command(
                        context=context,
                        command=CommandSpec(
                            action=ProcessGroupProbeAction(
                                spawn_child=True,
                                parent_exits=False,
                                ignore_sigterm=True,
                            )
                        ),
                        timeout_seconds=60,
                        terminate_grace_seconds=0.1,
                    ),
                    timeout=2,
                )
        assert len(spawned) == 1
        with pytest.raises(ProcessLookupError):
            os.killpg(spawned[0].pid, 0)
    finally:
        process_module._configure_process_supervisor(None)
        for child in spawned:
            if child.returncode is None:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await child.wait()


@pytest.mark.asyncio
async def test_post_kill_completion_drain_has_a_hard_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from scripts.milestone_2b_case_runners import process

    class FakeProcess:
        pid = 12345

    release = asyncio.Event()

    async def retained_pipe_completion(
    ) -> tuple[int, tuple[bytes, bool], tuple[bytes, bool]]:
        await release.wait()
        return -9, (b"", False), (b"", False)

    completion_task = asyncio.create_task(retained_pipe_completion())
    monkeypatch.setattr(os, "killpg", lambda pid, sig: None)
    monkeypatch.setattr(process, "POST_KILL_DRAIN_TIMEOUT_SECONDS", 0.05)
    delayed_release = asyncio.create_task(asyncio.sleep(0.5))
    delayed_release.add_done_callback(lambda _task: release.set())
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="reap|drain|SIGKILL"):
            await process._terminate_process_group(
                FakeProcess(),  # type: ignore[arg-type]
                completion_task,
                0.01,
            )
    finally:
        release.set()
        await asyncio.gather(completion_task, return_exceptions=True)
        delayed_release.cancel()
        await asyncio.gather(delayed_release, return_exceptions=True)

    assert time.monotonic() - started < 0.3


@pytest.mark.asyncio
async def test_timeout_reaps_inherited_pipe_child_after_parent_exits(
    tmp_path: Path,
) -> None:
    import asyncio

    from scripts.milestone_2b_case_runners.base import CaseContext
    from scripts.milestone_2b_case_runners.process import (
        CaseCommandTimeout,
        CommandSpec,
        ProcessGroupProbeAction,
        run_command,
    )
    from scripts.milestone_2b_case_runners.safety import _case_execution_scope

    release_root = _release_root(tmp_path)
    context = CaseContext(release_root, "run-1", "local")
    child_pid = -1
    try:
        async with _case_execution_scope(context, "isolated_mutation", None):
            with pytest.raises(CaseCommandTimeout, match="timed out") as caught:
                await asyncio.wait_for(
                    run_command(
                        context=context,
                        command=CommandSpec(
                            action=ProcessGroupProbeAction(
                                spawn_child=True,
                                parent_exits=True,
                                ignore_sigterm=False,
                            )
                        ),
                        timeout_seconds=0.3,
                        terminate_grace_seconds=0.1,
                    ),
                    timeout=2,
                )

        child_pid = json.loads(caught.value.result.stdout)["child"]
        assert not _process_exists(child_pid)
    finally:
        if child_pid > 0 and _process_exists(child_pid):
            os.kill(child_pid, signal.SIGKILL)


@pytest.mark.asyncio
async def test_success_reaps_same_group_child_that_closed_standard_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.milestone_2b_case_runners import process
    from scripts.milestone_2b_case_runners.base import CaseContext
    from scripts.milestone_2b_case_runners.process import (
        CommandSpec,
        ProcessGroupProbeAction,
        run_command,
    )
    from scripts.milestone_2b_case_runners.safety import _case_execution_scope

    marker = tmp_path / "detached-child.ready"
    monkeypatch.setattr(
        process,
        "_PROCESS_GROUP_PROBE_SOURCE",
        f"""
import json
import os
import pathlib
import subprocess
import sys
import time

child_source = r'''
import os
import pathlib
import time

os.closerange(0, 3)
pathlib.Path({str(marker)!r}).write_text("ready", encoding="utf-8")
time.sleep(60)
'''
child = subprocess.Popen([sys.executable, "-c", child_source], close_fds=True)
marker = pathlib.Path({str(marker)!r})
while not marker.exists():
    time.sleep(0.01)
print(json.dumps({{"parent": os.getpid(), "child": child.pid}}), flush=True)
""",
    )
    context = CaseContext(_release_root(tmp_path), "run-1", "local")
    process_group_id = -1
    child_pid = -1
    try:
        async with _case_execution_scope(context, "isolated_mutation", None):
            result = await run_command(
                context=context,
                command=CommandSpec(
                    action=ProcessGroupProbeAction(
                        spawn_child=True,
                        parent_exits=True,
                        ignore_sigterm=False,
                    )
                ),
                timeout_seconds=1,
                terminate_grace_seconds=0.1,
            )

        pids = json.loads(result.stdout)
        process_group_id = pids["parent"]
        child_pid = pids["child"]
        assert result.returncode == 0
        with pytest.raises(ProcessLookupError):
            os.killpg(process_group_id, 0)
        assert not _process_exists(child_pid)
    finally:
        if process_group_id > 0:
            try:
                os.killpg(process_group_id, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif child_pid > 0 and _process_exists(child_pid):
            os.kill(child_pid, signal.SIGKILL)


@pytest.mark.asyncio
async def test_cancelling_command_reaps_the_process_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from scripts.milestone_2b_case_runners.base import CaseContext
    from scripts.milestone_2b_case_runners.process import (
        CommandSpec,
        ProcessGroupProbeAction,
        run_command,
    )
    from scripts.milestone_2b_case_runners.safety import _case_execution_scope

    release_root = _release_root(tmp_path)
    context = CaseContext(release_root, "run-1", "local")
    original_create_subprocess_exec = asyncio.create_subprocess_exec
    process_group_ids: list[int] = []

    async def capture_process_group(*args: object, **kwargs: object) -> object:
        process = await original_create_subprocess_exec(*args, **kwargs)  # type: ignore[arg-type]
        process_group_ids.append(process.pid)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture_process_group)
    async with _case_execution_scope(context, "isolated_mutation", None):
        task = asyncio.create_task(
            run_command(
                context=context,
                command=CommandSpec(
                    action=ProcessGroupProbeAction(
                        spawn_child=True,
                        parent_exits=False,
                        ignore_sigterm=True,
                    )
                ),
                timeout_seconds=60,
                terminate_grace_seconds=0.1,
            )
        )
        for _ in range(100):
            if process_group_ids:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert len(process_group_ids) == 1
    with pytest.raises(ProcessLookupError):
        os.killpg(process_group_ids[0], 0)


def _case(
    case_id: str,
    *,
    runner: str = "testing.run",
    phase: str = "deployment",
    safety: str = "read_only",
    timeout_seconds: int = 2,
) -> object:
    from scripts.milestone_2b_case_catalog import CaseDefinition

    return CaseDefinition(
        case_id=case_id,
        category="load" if case_id.startswith("LOAD-") else "negative",
        phase=phase,  # type: ignore[arg-type]
        title=f"case {case_id}",
        expected="expected result",
        runner=runner,
        timeout_seconds=timeout_seconds,
        safety=safety,  # type: ignore[arg-type]
    )


class _PublishingRunner:
    def __init__(self, *, delay: float = 0.0, status: str = "通过") -> None:
        self.delay = delay
        self.status = status
        self.calls = 0
        self.active = 0
        self.max_active = 0

    async def run(self, context: object, case: object) -> object:
        from scripts.milestone_2b_case_runners.base import CaseOutcome
        from scripts.milestone_2b_case_runners.evidence import (
            publish_case_evidence,
        )

        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await __import__("asyncio").sleep(self.delay)
            path = publish_case_evidence(
                context=context,  # type: ignore[arg-type]
                case=case,  # type: ignore[arg-type]
                name="result.json",
                payload={"observed": True},
            )
            return CaseOutcome(
                status=self.status,  # type: ignore[arg-type]
                reason="controlled observation",
                evidence=(path,),
            )
        finally:
            self.active -= 1


@pytest.mark.asyncio
async def test_batch_publishes_schema2_evidence_and_execution_once(
    tmp_path: Path,
) -> None:
    from scripts.run_milestone_2b_case_batch import run_case_batch

    release_root = _release_root(tmp_path)
    runner = _PublishingRunner()
    case = _case("DEP-001")
    result = await run_case_batch(
        cases=(case,),  # type: ignore[arg-type]
        release_root=release_root,
        runners={"testing.run": runner},  # type: ignore[dict-item]
        concurrency=1,
        run_id="run-1",
        target="local",
    )

    raw_path = release_root / "negative/evidence/DEP-001/result.json"
    execution_path = release_root / "negative/executions/DEP-001.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    execution = json.loads(execution_path.read_text(encoding="utf-8"))
    assert set(raw) == {
        "schema_version",
        "evidence_type",
        "case_id",
        "release_tag",
        "git_sha",
        "recorded_at",
        "payload",
    }
    assert raw == {
        "schema_version": 2,
        "evidence_type": "case_evidence",
        "case_id": "DEP-001",
        "release_tag": "v1.0_260818",
        "git_sha": "a" * 40,
        "recorded_at": raw["recorded_at"],
        "payload": {"observed": True},
    }
    assert raw["recorded_at"].endswith("+00:00")
    assert set(execution) == {
        "schema_version",
        "evidence_type",
        "case_id",
        "status",
        "started_at",
        "finished_at",
        "target",
        "command",
        "reason",
        "mock",
        "release_tag",
        "git_sha",
        "evidence",
    }
    assert execution["schema_version"] == 2
    assert execution["evidence_type"] == "negative_case"
    assert execution["case_id"] == "DEP-001"
    assert execution["status"] == "通过"
    assert execution["target"] == "local"
    assert execution["command"] == (
        "run_milestone_2b_case_batch.py --case DEP-001"
    )
    assert execution["mock"] is False
    assert execution["evidence"] == ["negative/evidence/DEP-001/result.json"]
    assert result.exit_code == 0
    assert result.completed == ("DEP-001",)
    claim = release_root / "negative/evidence/DEP-001/.case-runner.claim"
    claim_metadata = os.lstat(claim)
    assert claim_metadata.st_nlink == 1
    assert claim_metadata.st_uid == os.getuid()
    assert stat.S_IMODE(claim_metadata.st_mode) == 0o600


class _ExplodingRunner:
    async def run(self, context: object, case: object) -> object:
        raise RuntimeError("runner exploded")


class _CaseIdentityMutatingRunner:
    def __init__(self) -> None:
        self.cleanup_case_id: str | None = None

    async def run(self, context: object, case: object) -> object:
        from scripts.milestone_2b_case_runners.evidence import publish_case_evidence

        object.__setattr__(case, "case_id", "DEP-002")
        publish_case_evidence(
            context=context,  # type: ignore[arg-type]
            case=case,  # type: ignore[arg-type]
            name="result.json",
            payload={"cross_case": True},
        )
        raise AssertionError("cross-case publication unexpectedly succeeded")

    async def cleanup(self, context: object, case: object) -> None:
        self.cleanup_case_id = case.case_id  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_runner_cannot_rebind_a_claim_to_another_case(
    tmp_path: Path,
) -> None:
    from scripts.run_milestone_2b_case_batch import run_case_batch

    release_root = _release_root(tmp_path)
    runner = _CaseIdentityMutatingRunner()
    result = await run_case_batch(
        cases=(_case("DEP-001"),),  # type: ignore[arg-type]
        release_root=release_root,
        runners={"testing.run": runner},  # type: ignore[dict-item]
        concurrency=1,
        run_id="run-1",
        target="local",
        require_cleanup=True,
    )

    execution = json.loads(
        (release_root / "negative/executions/DEP-001.json").read_text(
            encoding="utf-8"
        )
    )
    assert execution["case_id"] == "DEP-001"
    assert execution["status"] == "失败"
    assert "case" in execution["reason"].lower()
    assert result.completed == ("DEP-001",)
    assert result.exit_code != 0
    assert runner.cleanup_case_id == "DEP-001"
    assert not (release_root / "negative/executions/DEP-002.json").exists()
    assert not (release_root / "negative/evidence/DEP-002").exists()


@pytest.mark.asyncio
async def test_runner_exception_becomes_a_terminal_failure_record(
    tmp_path: Path,
) -> None:
    from scripts.run_milestone_2b_case_batch import run_case_batch

    release_root = _release_root(tmp_path)
    result = await run_case_batch(
        cases=(_case("DEP-002"),),  # type: ignore[arg-type]
        release_root=release_root,
        runners={"testing.run": _ExplodingRunner()},  # type: ignore[dict-item]
        concurrency=1,
        run_id="run-1",
        target="local",
    )

    execution = json.loads(
        (release_root / "negative/executions/DEP-002.json").read_text(
            encoding="utf-8"
        )
    )
    assert execution["status"] == "失败"
    assert "runner exploded" in execution["reason"]
    assert len(execution["evidence"]) == 1
    assert result.exit_code != 0


class _FailureNameCollisionRunner:
    async def run(self, context: object, case: object) -> object:
        from scripts.milestone_2b_case_runners.evidence import publish_case_evidence

        publish_case_evidence(
            context=context,  # type: ignore[arg-type]
            case=case,  # type: ignore[arg-type]
            name="runner-failure.json",
            payload={"runner_owned": True},
        )
        raise RuntimeError("failure after collision")


@pytest.mark.asyncio
async def test_framework_failure_evidence_cannot_be_preoccupied(
    tmp_path: Path,
) -> None:
    from scripts.run_milestone_2b_case_batch import run_case_batch

    release_root = _release_root(tmp_path)
    result = await run_case_batch(
        cases=(_case("DEP-017"),),  # type: ignore[arg-type]
        release_root=release_root,
        runners={"testing.run": _FailureNameCollisionRunner()},  # type: ignore[dict-item]
        concurrency=1,
        run_id="run-1",
        target="local",
    )

    execution = json.loads(
        (release_root / "negative/executions/DEP-017.json").read_text(
            encoding="utf-8"
        )
    )
    assert execution["status"] == "失败"
    assert "failure after collision" in execution["reason"]
    assert execution["evidence"][0] != (
        "negative/evidence/DEP-017/runner-failure.json"
    )
    assert result.exit_code != 0


class _SleepingRunner:
    async def run(self, context: object, case: object) -> object:
        await __import__("asyncio").sleep(60)
        raise AssertionError("unreachable")


@pytest.mark.asyncio
async def test_runner_timeout_becomes_a_terminal_failure_record(
    tmp_path: Path,
) -> None:
    from scripts.run_milestone_2b_case_batch import run_case_batch

    release_root = _release_root(tmp_path)
    case = _case("DEP-003", timeout_seconds=1)
    result = await run_case_batch(
        cases=(case,),  # type: ignore[arg-type]
        release_root=release_root,
        runners={"testing.run": _SleepingRunner()},  # type: ignore[dict-item]
        concurrency=1,
        run_id="run-1",
        target="local",
    )

    execution = json.loads(
        (release_root / "negative/executions/DEP-003.json").read_text(
            encoding="utf-8"
        )
    )
    assert execution["status"] == "失败"
    assert "timeout" in execution["reason"].lower()
    assert result.exit_code != 0


class _OutsideEvidenceRunner:
    def __init__(self, outside: Path) -> None:
        self.outside = outside

    async def run(self, context: object, case: object) -> object:
        from scripts.milestone_2b_case_runners.base import CaseOutcome

        self.outside.write_text("{}", encoding="utf-8")
        return CaseOutcome(
            status="通过",
            reason="unsafe evidence",
            evidence=(self.outside,),
        )


@pytest.mark.asyncio
async def test_evidence_must_stay_in_the_current_case_directory(
    tmp_path: Path,
) -> None:
    from scripts.run_milestone_2b_case_batch import run_case_batch

    release_root = _release_root(tmp_path)
    outside = tmp_path / "outside.json"
    result = await run_case_batch(
        cases=(_case("DEP-004"),),  # type: ignore[arg-type]
        release_root=release_root,
        runners={"testing.run": _OutsideEvidenceRunner(outside)},  # type: ignore[dict-item]
        concurrency=1,
        run_id="run-1",
        target="local",
    )

    execution = json.loads(
        (release_root / "negative/executions/DEP-004.json").read_text(
            encoding="utf-8"
        )
    )
    assert execution["status"] == "失败"
    assert "case evidence" in execution["reason"].lower()
    assert all("outside.json" not in path for path in execution["evidence"])
    assert result.exit_code != 0


@pytest.mark.asyncio
async def test_same_case_resume_skips_without_replacing_terminal_files(
    tmp_path: Path,
) -> None:
    from scripts.run_milestone_2b_case_batch import run_case_batch

    release_root = _release_root(tmp_path)
    runner = _PublishingRunner()
    arguments = {
        "cases": (_case("DEP-005"),),
        "release_root": release_root,
        "runners": {"testing.run": runner},
        "concurrency": 1,
        "run_id": "run-1",
        "target": "local",
    }
    await run_case_batch(**arguments)  # type: ignore[arg-type]
    execution_path = release_root / "negative/executions/DEP-005.json"
    evidence_path = release_root / "negative/evidence/DEP-005/result.json"
    before = (
        execution_path.read_bytes(),
        evidence_path.read_bytes(),
        os.lstat(execution_path).st_ino,
        os.lstat(evidence_path).st_ino,
    )

    resumed = await run_case_batch(**arguments)  # type: ignore[arg-type]

    after = (
        execution_path.read_bytes(),
        evidence_path.read_bytes(),
        os.lstat(execution_path).st_ino,
        os.lstat(evidence_path).st_ino,
    )
    assert runner.calls == 1
    assert resumed.skipped == ("DEP-005",)
    assert after == before


@pytest.mark.asyncio
async def test_concurrent_batches_use_one_atomic_case_claim(tmp_path: Path) -> None:
    import asyncio

    from scripts.run_milestone_2b_case_batch import run_case_batch

    release_root = _release_root(tmp_path)
    runner = _PublishingRunner(delay=0.15)
    arguments = {
        "cases": (_case("DEP-018"),),
        "release_root": release_root,
        "runners": {"testing.run": runner},
        "concurrency": 1,
        "run_id": "run-1",
        "target": "local",
        "require_all_selected": True,
    }

    first, second = await asyncio.gather(
        run_case_batch(**arguments),  # type: ignore[arg-type]
        run_case_batch(**arguments),  # type: ignore[arg-type]
    )

    assert runner.calls == 1
    assert sorted((first.exit_code, second.exit_code)) == [0, 1]
    assert (release_root / "negative/executions/DEP-018.json").is_file()


@pytest.mark.asyncio
async def test_partial_case_evidence_refuses_resume(tmp_path: Path) -> None:
    from scripts.milestone_2b_case_runners.base import CaseContext
    from scripts.milestone_2b_case_runners.evidence import publish_case_evidence
    from scripts.milestone_2b_case_runners.safety import _case_execution_scope
    from scripts.run_milestone_2b_case_batch import run_case_batch

    release_root = _release_root(tmp_path)
    case = _case("DEP-006")
    context = CaseContext(release_root, "run-1", "local")
    async with _case_execution_scope(
        context,
        "read_only",
        None,
        case,  # type: ignore[arg-type]
    ):
        publish_case_evidence(
            context=context,
            case=case,  # type: ignore[arg-type]
            name="partial.json",
            payload={"partial": True},
        )
    runner = _PublishingRunner()

    result = await run_case_batch(
        cases=(case,),  # type: ignore[arg-type]
        release_root=release_root,
        runners={"testing.run": runner},  # type: ignore[dict-item]
        concurrency=1,
        run_id="run-2",
        target="local",
        require_all_selected=True,
    )

    assert runner.calls == 0
    assert result.missing == ("DEP-006",)
    assert result.exit_code != 0


@pytest.mark.asyncio
async def test_missing_terminal_only_changes_exit_for_require_all_selected(
    tmp_path: Path,
) -> None:
    from scripts.milestone_2b_case_runners.base import CaseContext
    from scripts.milestone_2b_case_runners.evidence import publish_case_evidence
    from scripts.milestone_2b_case_runners.safety import _case_execution_scope
    from scripts.run_milestone_2b_case_batch import run_case_batch

    release_root = _release_root(tmp_path)
    case = _case("DEP-015")
    context = CaseContext(release_root, "run-1", "local")
    async with _case_execution_scope(
        context,
        "read_only",
        None,
        case,  # type: ignore[arg-type]
    ):
        publish_case_evidence(
            context=context,
            case=case,  # type: ignore[arg-type]
            name="partial.json",
            payload={"partial": True},
        )

    result = await run_case_batch(
        cases=(case,),  # type: ignore[arg-type]
        release_root=release_root,
        runners={"testing.run": _PublishingRunner()},  # type: ignore[dict-item]
        concurrency=1,
        run_id="run-2",
        target="local",
        require_all_selected=False,
    )

    assert result.missing == ("DEP-015",)
    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_different_case_ids_run_concurrently_with_a_global_bound(
    tmp_path: Path,
) -> None:
    from scripts.run_milestone_2b_case_batch import run_case_batch

    release_root = _release_root(tmp_path)
    runner = _PublishingRunner(delay=0.15)
    cases = tuple(_case(f"DEP-{number:03d}") for number in range(7, 11))
    started = time.monotonic()
    result = await run_case_batch(
        cases=cases,  # type: ignore[arg-type]
        release_root=release_root,
        runners={"testing.run": runner},  # type: ignore[dict-item]
        concurrency=2,
        run_id="run-1",
        target="local",
    )
    elapsed = time.monotonic() - started

    assert runner.max_active == 2
    assert elapsed < 0.55
    assert result.completed == tuple(f"DEP-{number:03d}" for number in range(7, 11))


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrency", (True, 1.5))
async def test_batch_rejects_nonbase_integer_concurrency(
    tmp_path: Path,
    concurrency: object,
) -> None:
    from scripts.run_milestone_2b_case_batch import run_case_batch

    runner = _PublishingRunner()
    with pytest.raises(ValueError, match="concurrency|integer"):
        await run_case_batch(
            cases=(_case("DEP-010"),),  # type: ignore[arg-type]
            release_root=_release_root(tmp_path),
            runners={"testing.run": runner},  # type: ignore[dict-item]
            concurrency=concurrency,  # type: ignore[arg-type]
            run_id="run-1",
            target="local",
        )
    assert runner.calls == 0


class _StatefulFailureStatus(str):
    def __new__(cls) -> _StatefulFailureStatus:
        value = super().__new__(cls, "失败")
        value._comparisons = 0
        return value

    def __eq__(self, other: object) -> bool:
        self._comparisons += 1
        return self._comparisons == 1

    __hash__ = str.__hash__


class _MutatedOutcomeRunner(_PublishingRunner):
    async def run(self, context: object, case: object) -> object:
        outcome = await super().run(context, case)
        object.__setattr__(outcome, "status", _StatefulFailureStatus())
        return outcome


@pytest.mark.asyncio
async def test_runner_outcome_is_exactly_snapshotted_before_exit_status(
    tmp_path: Path,
) -> None:
    from scripts.run_milestone_2b_case_batch import run_case_batch

    release_root = _release_root(tmp_path)
    result = await run_case_batch(
        cases=(_case("DEP-009"),),  # type: ignore[arg-type]
        release_root=release_root,
        runners={"testing.run": _MutatedOutcomeRunner()},  # type: ignore[dict-item]
        concurrency=1,
        run_id="run-1",
        target="local",
    )

    execution = json.loads(
        (release_root / "negative/executions/DEP-009.json").read_text(
            encoding="utf-8"
        )
    )
    assert execution["status"] == "失败"
    assert result.exit_code != 0


class _LockCheckingRunner(_PublishingRunner):
    async def run(self, context: object, case: object) -> object:
        from scripts.milestone_2b_case_runners.safety import (
            MaintenanceLockGuard,
            maintenance_lock_held,
        )

        assert maintenance_lock_held(context.release_root)  # type: ignore[attr-defined]
        with pytest.raises(ValueError, match="another|maintenance lock"):
            with MaintenanceLockGuard(context.release_root):  # type: ignore[attr-defined]
                pass
        return await super().run(context, case)


def _start_operator_lifecycle_lock_holder(
    tmp_path: Path,
    release_root: Path,
) -> subprocess.Popen[str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    flock = fake_bin / "flock"
    flock.write_text(
        "#!/usr/bin/env python3\n"
        "import fcntl, sys\n"
        "fcntl.flock(int(sys.argv[2]), fcntl.LOCK_EX | fcntl.LOCK_NB)\n",
        encoding="utf-8",
    )
    flock.chmod(0o755)
    environment = dict(os.environ)
    environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
    lock_path = release_root.parent / ".operator-lifecycle.lock"
    process = subprocess.Popen(
        (
            sys.executable,
            "deploy/scripts/operator_lifecycle.py",
            "hold-lock",
            "--release-tag-root",
            str(release_root.parent),
            "--lock-path",
            str(lock_path),
        ),
        cwd=Path(__file__).parents[1],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "LOCKED"
    return process


def _stop_lock_holder(process: subprocess.Popen[str]) -> None:
    if process.stdin is not None:
        process.stdin.close()
    process.wait(timeout=5)


def test_delegated_lock_rejects_noncanonical_same_basename_holder(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from scripts.milestone_2b_case_runners import safety

    release_root = _release_root(tmp_path)
    lock_path = release_root.parent / ".operator-lifecycle.lock"
    monkeypatch.setattr(
        safety,
        "_holder_command_tokens",
        lambda holder_pid: (
            sys.executable,
            "/tmp/operator_lifecycle.py",
            "hold-lock",
            "--release-tag-root",
            str(release_root.parent),
            "--lock-path",
            str(lock_path),
        ),
    )

    assert not safety._holder_command_matches(123, release_root.parent, lock_path)


def test_relative_holder_script_is_resolved_from_holder_process_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from scripts.milestone_2b_case_runners import safety

    platform_root = Path(__file__).parents[1]
    release_root = _release_root(tmp_path)
    lock_path = release_root.parent / ".operator-lifecycle.lock"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        safety,
        "_holder_command_tokens",
        lambda holder_pid: (
            sys.executable,
            "deploy/scripts/operator_lifecycle.py",
            "hold-lock",
            "--release-tag-root",
            str(release_root.parent),
            "--lock-path",
            str(lock_path),
        ),
    )
    monkeypatch.setattr(
        safety,
        "_holder_working_directory",
        lambda holder_pid: platform_root,
        raising=False,
    )

    assert safety._holder_command_matches(123, release_root.parent, lock_path)


@pytest.mark.asyncio
async def test_batch_delegates_to_exact_live_operator_lifecycle_lock_holder(
    tmp_path: Path,
) -> None:
    from scripts.milestone_2b_case_runners.safety import MaintenanceLockGuard
    from scripts.run_milestone_2b_case_batch import run_case_batch

    release_root = _release_root(tmp_path)
    lock_path = release_root.parent / ".operator-lifecycle.lock"
    holder = _start_operator_lifecycle_lock_holder(tmp_path, release_root)
    try:
        result = await run_case_batch(
            cases=(_case("GPU-001", safety="canonical_runtime"),),  # type: ignore[arg-type]
            release_root=release_root,
            runners={"testing.run": _LockCheckingRunner()},  # type: ignore[dict-item]
            concurrency=1,
            run_id="run-1",
            target="local",
            delegated_lock_holder_pid=holder.pid,
            delegated_lock_path=lock_path,
        )

        assert result.exit_code == 0
        assert holder.poll() is None
        with pytest.raises(ValueError, match="another|maintenance lock"):
            with MaintenanceLockGuard(release_root):
                pass
    finally:
        _stop_lock_holder(holder)


class _KillingLockHolderRunner(_PublishingRunner):
    def __init__(self, holder_pid: int) -> None:
        super().__init__()
        self._holder_pid = holder_pid

    async def run(self, context: object, case: object) -> object:
        outcome = await super().run(context, case)
        os.kill(self._holder_pid, signal.SIGTERM)
        os.waitpid(self._holder_pid, 0)
        return outcome


@pytest.mark.asyncio
async def test_delegated_lock_fails_closed_if_holder_dies_before_batch_exit(
    tmp_path: Path,
) -> None:
    from scripts.run_milestone_2b_case_batch import run_case_batch

    release_root = _release_root(tmp_path)
    lock_path = release_root.parent / ".operator-lifecycle.lock"
    holder = _start_operator_lifecycle_lock_holder(tmp_path, release_root)

    with pytest.raises(ValueError, match="delegated maintenance lock"):
        await run_case_batch(
            cases=(_case("GPU-001", safety="canonical_runtime"),),  # type: ignore[arg-type]
            release_root=release_root,
            runners={"testing.run": _KillingLockHolderRunner(holder.pid)},  # type: ignore[dict-item]
            concurrency=1,
            run_id="run-1",
            target="local",
            delegated_lock_holder_pid=holder.pid,
            delegated_lock_path=lock_path,
        )


@pytest.mark.asyncio
async def test_canonical_runner_holds_the_release_lock_for_its_execution(
    tmp_path: Path,
) -> None:
    from scripts.run_milestone_2b_case_batch import run_case_batch

    result = await run_case_batch(
        cases=(_case("GPU-001", safety="canonical_runtime"),),  # type: ignore[arg-type]
        release_root=_release_root(tmp_path),
        runners={"testing.run": _LockCheckingRunner()},  # type: ignore[dict-item]
        concurrency=1,
        run_id="run-1",
        target="local",
    )

    assert result.exit_code == 0


class _TimeoutAndCleanupFailureRunner:
    async def run(self, context: object, case: object) -> object:
        from scripts.milestone_2b_case_runners.process import (
            CommandSpec,
            ProcessGroupProbeAction,
            run_command,
        )

        return await run_command(
            context=context,  # type: ignore[arg-type]
            command=CommandSpec(
                action=ProcessGroupProbeAction(
                    spawn_child=False,
                    parent_exits=False,
                    ignore_sigterm=True,
                )
            ),
            timeout_seconds=60,
            terminate_grace_seconds=0.1,
        )

    async def cleanup(self, context: object, case: object) -> None:
        raise RuntimeError("cleanup exploded")


class _DetachedCommandRunner(_PublishingRunner):
    def __init__(self) -> None:
        super().__init__()
        self.command_task: object | None = None

    async def run(self, context: object, case: object) -> object:
        import asyncio

        from scripts.milestone_2b_case_runners.process import (
            CommandSpec,
            ProcessGroupProbeAction,
            run_command,
        )

        self.command_task = asyncio.create_task(
            run_command(
                context=context,  # type: ignore[arg-type]
                command=CommandSpec(
                    action=ProcessGroupProbeAction(
                        spawn_child=True,
                        parent_exits=False,
                        ignore_sigterm=True,
                    )
                ),
                timeout_seconds=10.0,
                terminate_grace_seconds=0.1,
            )
        )
        await asyncio.sleep(0.1)
        return await super().run(context, case)


@pytest.mark.asyncio
async def test_batch_drains_detached_commands_before_terminal_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from scripts.run_milestone_2b_case_batch import run_case_batch

    release_root = _release_root(tmp_path)
    runner = _DetachedCommandRunner()
    original_create_subprocess_exec = asyncio.create_subprocess_exec
    process_group_ids: list[int] = []

    async def capture_process_group(*args: object, **kwargs: object) -> object:
        process = await original_create_subprocess_exec(*args, **kwargs)  # type: ignore[arg-type]
        process_group_ids.append(process.pid)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture_process_group)
    result = await run_case_batch(
        cases=(_case("DEP-011", safety="isolated_mutation"),),  # type: ignore[arg-type]
        release_root=release_root,
        runners={"testing.run": runner},  # type: ignore[dict-item]
        concurrency=1,
        run_id="run-1",
        target="local",
    )

    assert result.completed == ("DEP-011",)
    assert runner.command_task is not None
    assert runner.command_task.done()  # type: ignore[union-attr]
    assert len(process_group_ids) == 1
    with pytest.raises(ProcessLookupError):
        os.killpg(process_group_ids[0], 0)


class _LateCapabilityRunner(_PublishingRunner):
    def __init__(self) -> None:
        super().__init__()
        self.release_child = __import__("asyncio").Event()
        self.late_task: object | None = None

    async def run(self, context: object, case: object) -> object:
        import asyncio

        from scripts.milestone_2b_case_runners.process import (
            CommandSpec,
            ProcessGroupProbeAction,
            run_command,
        )

        async def launch_after_release() -> object:
            await self.release_child.wait()
            return await run_command(
                context=context,  # type: ignore[arg-type]
                command=CommandSpec(
                    action=ProcessGroupProbeAction(
                        spawn_child=False,
                        parent_exits=False,
                        ignore_sigterm=False,
                    )
                ),
                timeout_seconds=1.0,
                terminate_grace_seconds=0.1,
            )

        self.late_task = asyncio.create_task(launch_after_release())
        return await super().run(context, case)


@pytest.mark.asyncio
async def test_revoked_capability_rejects_late_child_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from scripts.run_milestone_2b_case_batch import run_case_batch

    spawned = False

    async def reject_spawn(*args: object, **kwargs: object) -> object:
        nonlocal spawned
        spawned = True
        raise AssertionError("revoked capability reached subprocess creation")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", reject_spawn)
    runner = _LateCapabilityRunner()
    result = await run_case_batch(
        cases=(_case("DEP-012", safety="isolated_mutation"),),  # type: ignore[arg-type]
        release_root=_release_root(tmp_path),
        runners={"testing.run": runner},  # type: ignore[dict-item]
        concurrency=1,
        run_id="run-1",
        target="local",
    )
    runner.release_child.set()

    assert runner.late_task is not None
    with pytest.raises(ValueError, match="capability|active|revoked"):
        await runner.late_task  # type: ignore[misc]
    assert result.completed == ("DEP-012",)
    assert spawned is False


@pytest.mark.asyncio
async def test_cleanup_failure_is_recorded_and_timed_out_process_is_reaped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from scripts.run_milestone_2b_case_batch import run_case_batch

    release_root = _release_root(tmp_path)
    original_create_subprocess_exec = asyncio.create_subprocess_exec
    process_group_ids: list[int] = []

    async def capture_process_group(*args: object, **kwargs: object) -> object:
        process = await original_create_subprocess_exec(*args, **kwargs)  # type: ignore[arg-type]
        process_group_ids.append(process.pid)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture_process_group)
    case = _case(
        "DEP-011",
        safety="isolated_mutation",
        timeout_seconds=1,
    )
    result = await run_case_batch(
        cases=(case,),  # type: ignore[arg-type]
        release_root=release_root,
        runners={"testing.run": _TimeoutAndCleanupFailureRunner()},  # type: ignore[dict-item]
        concurrency=1,
        run_id="run-1",
        target="local",
        require_cleanup=True,
    )

    execution = json.loads(
        (release_root / "negative/executions/DEP-011.json").read_text(
            encoding="utf-8"
        )
    )
    assert execution["status"] == "失败"
    assert "cleanup exploded" in execution["reason"]
    assert len(process_group_ids) == 1
    with pytest.raises(ProcessLookupError):
        os.killpg(process_group_ids[0], 0)
    assert result.exit_code != 0


class _DelayedProcessGroupCleanupRunner:
    def __init__(self, marker: Path, process_group_ids: list[int]) -> None:
        self.marker = marker
        self.process_group_ids = process_group_ids
        self.command_task: object | None = None
        self.cleanup_called = False
        self.cleanup_group_alive: bool | None = None

    async def run(self, context: object, case: object) -> object:
        import asyncio

        from scripts.milestone_2b_case_runners.process import (
            CommandSpec,
            ProcessGroupProbeAction,
            run_command,
        )

        self.command_task = asyncio.create_task(
            run_command(
                context=context,  # type: ignore[arg-type]
                command=CommandSpec(
                    action=ProcessGroupProbeAction(
                        spawn_child=True,
                        parent_exits=False,
                        ignore_sigterm=False,
                    )
                ),
                timeout_seconds=60,
                terminate_grace_seconds=2,
            )
        )
        deadline = asyncio.get_running_loop().time() + 2
        while not self.marker.exists():
            if asyncio.get_running_loop().time() >= deadline:
                raise AssertionError("delayed child did not create marker")
            await asyncio.sleep(0.01)
        await asyncio.sleep(60)
        raise AssertionError("runner timeout did not cancel delayed child")

    async def cleanup(self, context: object, case: object) -> None:
        del context, case
        self.cleanup_called = True
        process_group_id = self.process_group_ids[0]
        try:
            os.killpg(process_group_id, 0)
        except ProcessLookupError:
            self.cleanup_group_alive = False
        else:
            self.cleanup_group_alive = True
        self.marker.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_cleanup_waits_for_delayed_command_process_group_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from scripts.milestone_2b_case_runners import process as process_module
    from scripts.run_milestone_2b_case_batch import run_case_batch

    marker = tmp_path / "delayed-command-marker"
    monkeypatch.setenv("M2B_DELAYED_COMMAND_MARKER", str(marker))
    monkeypatch.setattr(
        process_module,
        "_PROCESS_GROUP_PROBE_SOURCE",
        """
import os
import pathlib
import signal
import subprocess
import sys
import time

marker = pathlib.Path(os.environ["M2B_DELAYED_COMMAND_MARKER"])
child_source = r'''\
import os
import pathlib
import signal
import time

marker = pathlib.Path(os.environ["M2B_DELAYED_COMMAND_MARKER"])
def stop(_signum, _frame):
    time.sleep(0.75)
    marker.write_text("recreated", encoding="utf-8")
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
marker.write_text("running", encoding="utf-8")
while True:
    time.sleep(1)
'''
child = subprocess.Popen(
    [sys.executable, "-c", child_source],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
print(child.pid, flush=True)
time.sleep(60)
""",
    )
    original_create_subprocess_exec = asyncio.create_subprocess_exec
    process_group_ids: list[int] = []

    async def capture_process_group(*args: object, **kwargs: object) -> object:
        process = await original_create_subprocess_exec(*args, **kwargs)  # type: ignore[arg-type]
        process_group_ids.append(process.pid)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture_process_group)
    runner = _DelayedProcessGroupCleanupRunner(marker, process_group_ids)

    result = await run_case_batch(
        cases=(_case("DEP-011", safety="isolated_mutation", timeout_seconds=1),),  # type: ignore[arg-type]
        release_root=_release_root(tmp_path),
        runners={"testing.run": runner},  # type: ignore[dict-item]
        concurrency=1,
        run_id="run-1",
        target="local",
        require_cleanup=True,
    )
    await asyncio.sleep(0.5)

    assert result.exit_code != 0
    assert runner.cleanup_called is True
    assert runner.cleanup_group_alive is False
    assert marker.exists() is False
    assert runner.command_task is not None
    assert runner.command_task.done()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_cleanup_is_skipped_when_command_termination_is_unconfirmed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from scripts.milestone_2b_case_runners import process as process_module
    from scripts.milestone_2b_case_runners import safety
    from scripts.milestone_2b_case_runners.process import (
        CommandResult,
        CommandSpec,
        OutputProbeAction,
        run_command,
    )
    from scripts.run_milestone_2b_case_batch import run_case_batch

    started = asyncio.Event()
    release = asyncio.Event()

    async def cancellation_resistant_argv(**kwargs: object) -> CommandResult:
        argv = kwargs["argv"]
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            await release.wait()
        return CommandResult(
            argv=argv,  # type: ignore[arg-type]
            returncode=0,
            stdout=b"",
            stderr=b"",
            stdout_truncated=False,
            stderr_truncated=False,
        )

    monkeypatch.setattr(process_module, "_run_argv", cancellation_resistant_argv)
    monkeypatch.setattr(process_module, "POST_KILL_DRAIN_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(process_module, "COMPLETION_CANCEL_GRACE_SECONDS", 0.01)
    monkeypatch.setattr(safety, "COMMAND_TASK_TERMINATION_MARGIN_SECONDS", 0.01)

    class Runner(_PublishingRunner):
        def __init__(self) -> None:
            super().__init__()
            self.command_task: asyncio.Task[object] | None = None
            self.cleanup_called = False

        async def run(self, context: object, case: object) -> object:
            self.command_task = asyncio.create_task(
                run_command(
                    context=context,  # type: ignore[arg-type]
                    command=CommandSpec(action=OutputProbeAction(0, 0)),
                    timeout_seconds=60,
                    terminate_grace_seconds=0.01,
                )
            )
            await started.wait()
            return await super().run(context, case)

        async def cleanup(self, context: object, case: object) -> None:
            del context, case
            self.cleanup_called = True

    runner = Runner()
    result = await run_case_batch(
        cases=(_case("DEP-011", safety="isolated_mutation"),),  # type: ignore[arg-type]
        release_root=_release_root(tmp_path),
        runners={"testing.run": runner},  # type: ignore[dict-item]
        concurrency=1,
        run_id="run-1",
        target="local",
        require_cleanup=True,
    )
    release.set()
    assert runner.command_task is not None
    await runner.command_task

    assert result.exit_code != 0
    assert runner.cleanup_called is False


class _CancellationResistantRunner(_PublishingRunner):
    def __init__(self) -> None:
        super().__init__()
        self.release = __import__("asyncio").Event()
        self.task: object | None = None

    async def run(self, context: object, case: object) -> object:
        import asyncio

        self.task = asyncio.current_task()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            await self.release.wait()
        return await super().run(context, case)


class _CancellationResistantCleanupRunner(_PublishingRunner):
    def __init__(self) -> None:
        super().__init__()
        self.release = __import__("asyncio").Event()
        self.cleanup_task: object | None = None

    async def cleanup(self, context: object, case: object) -> None:
        import asyncio

        self.cleanup_task = asyncio.current_task()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            await self.release.wait()


async def _release_after(delay: float, event: object) -> None:
    await __import__("asyncio").sleep(delay)
    event.set()  # type: ignore[attr-defined]


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup", (False, True))
async def test_runner_and_cleanup_timeouts_are_hard_bounds(
    tmp_path: Path,
    cleanup: bool,
) -> None:
    import asyncio

    from scripts.run_milestone_2b_case_batch import run_case_batch

    runner = (
        _CancellationResistantCleanupRunner()
        if cleanup
        else _CancellationResistantRunner()
    )
    delayed_release = asyncio.create_task(_release_after(2.0, runner.release))
    release_root = _release_root(tmp_path)
    started = time.monotonic()
    result = await run_case_batch(
        cases=(_case("DEP-013", timeout_seconds=1),),  # type: ignore[arg-type]
        release_root=release_root,
        runners={"testing.run": runner},  # type: ignore[dict-item]
        concurrency=1,
        run_id="run-1",
        target="local",
        require_cleanup=cleanup,
    )
    elapsed = time.monotonic() - started
    runner.release.set()
    await asyncio.sleep(0)
    delayed_release.cancel()
    await asyncio.gather(delayed_release, return_exceptions=True)

    assert elapsed < 1.6
    assert result.completed == ("DEP-013",)
    assert result.exit_code != 0
    execution = json.loads(
        (
            release_root
            / "negative/executions/DEP-013.json"
        ).read_text(encoding="utf-8")
    )
    assert "timeout" in execution["reason"]


class _SafetyOverrideRunner:
    async def run(self, context: object, case: object) -> object:
        from scripts.milestone_2b_case_runners.process import (
            CommandSpec,
            MutationAction,
            run_command,
        )
        from scripts.milestone_2b_case_runners.safety import ResourceSpec

        return await run_command(
            context=context,  # type: ignore[arg-type]
            command=CommandSpec(
                action=MutationAction(
                    kind="docker_remove",
                    resource=ResourceSpec(
                        kind="container",
                        name="production-container",
                    ),
                )
            ),
            timeout_seconds=1,
        )


@pytest.mark.asyncio
async def test_runner_cannot_override_catalog_safety(tmp_path: Path) -> None:
    from scripts.run_milestone_2b_case_batch import run_case_batch

    release_root = _release_root(tmp_path)
    result = await run_case_batch(
        cases=(_case("DEP-019", safety="read_only"),),  # type: ignore[arg-type]
        release_root=release_root,
        runners={"testing.run": _SafetyOverrideRunner()},  # type: ignore[dict-item]
        concurrency=1,
        run_id="run-1",
        target="local",
    )

    execution = json.loads(
        (release_root / "negative/executions/DEP-019.json").read_text(
            encoding="utf-8"
        )
    )
    assert execution["status"] == "失败"
    assert "read_only" in execution["reason"]
    assert result.exit_code != 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("attribute", "value"),
    (
        ("phase", "unknown"),
        ("safety", "unknown"),
    ),
)
async def test_batch_rejects_unknown_phase_or_safety(
    tmp_path: Path,
    attribute: str,
    value: str,
) -> None:
    from dataclasses import replace

    from scripts.run_milestone_2b_case_batch import run_case_batch

    case = replace(_case("DEP-012"), **{attribute: value})
    with pytest.raises(ValueError, match=attribute):
        await run_case_batch(
            cases=(case,),  # type: ignore[arg-type]
            release_root=_release_root(tmp_path),
            runners={"testing.run": _PublishingRunner()},  # type: ignore[dict-item]
            concurrency=1,
            run_id="run-1",
            target="local",
        )


@pytest.mark.asyncio
async def test_batch_rejects_stateful_safety_string_before_runner(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    from scripts.run_milestone_2b_case_batch import run_case_batch

    class StatefulSafety(str):
        pass

    runner = _PublishingRunner()
    case = replace(_case("DEP-020"), safety=StatefulSafety("read_only"))
    with pytest.raises(ValueError, match="safety|plain string"):
        await run_case_batch(
            cases=(case,),  # type: ignore[arg-type]
            release_root=_release_root(tmp_path),
            runners={"testing.run": runner},  # type: ignore[dict-item]
            concurrency=1,
            run_id="run-1",
            target="local",
        )
    assert runner.calls == 0


@pytest.mark.asyncio
async def test_batch_rejects_unknown_runner_before_execution(tmp_path: Path) -> None:
    from scripts.run_milestone_2b_case_batch import run_case_batch

    runner = _PublishingRunner()
    with pytest.raises(ValueError, match="unknown runner"):
        await run_case_batch(
            cases=(_case("DEP-013", runner="missing.run"),),  # type: ignore[arg-type]
            release_root=_release_root(tmp_path),
            runners={"testing.run": runner},  # type: ignore[dict-item]
            concurrency=1,
            run_id="run-1",
            target="local",
        )
    assert runner.calls == 0


@pytest.mark.parametrize(
    "runner_name",
    (
        "os.system",
        "deployment._private",
        "deployment.__dict__",
        "deployment/path.run",
        "deployment.run.extra",
    ),
)
def test_dynamic_runner_resolution_is_package_scoped(runner_name: str) -> None:
    from scripts.run_milestone_2b_case_batch import resolve_runner

    with pytest.raises(ValueError, match="runner"):
        resolve_runner(runner_name)


def test_cli_disables_abbreviations_and_requires_positive_concurrency() -> None:
    from scripts.run_milestone_2b_case_batch import parse_args

    common = [
        "--catalog",
        "catalog.yaml",
        "--release-root",
        "/tmp/release",
        "--phase",
        "deployment",
    ]
    with pytest.raises(SystemExit):
        parse_args(["--cata", "catalog.yaml", *common[2:], "--concurrency", "1"])
    with pytest.raises(SystemExit):
        parse_args([*common, "--concurrency", "0"])


def test_cli_selects_one_case_or_the_requested_phase() -> None:
    from scripts.run_milestone_2b_case_batch import select_cases

    cases = (
        _case("DEP-014", phase="deployment"),
        _case("JOB-001", phase="offline"),
        _case("JOB-002", phase="offline"),
    )

    assert [case.case_id for case in select_cases(cases, "offline", None)] == [  # type: ignore[arg-type]
        "JOB-001",
        "JOB-002",
    ]
    assert [
        case.case_id
        for case in select_cases(cases, "offline", "JOB-002")  # type: ignore[arg-type]
    ] == ["JOB-002"]
    with pytest.raises(ValueError, match="phase"):
        select_cases(cases, "unknown", None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="case"):
        select_cases(cases, "offline", "DEP-014")  # type: ignore[arg-type]


def test_final_phase_reselects_every_case_and_allows_any_single_case() -> None:
    from scripts.run_milestone_2b_case_batch import select_cases

    cases = (
        _case("DEP-016", phase="deployment"),
        _case("JOB-003", phase="offline"),
        _case("ONL-001", phase="online"),
    )

    assert select_cases(cases, "final", None) == cases  # type: ignore[arg-type]
    assert select_cases(cases, "final", "DEP-016") == (cases[0],)  # type: ignore[arg-type]


def _catalog_with_case_timeout(tmp_path: Path, case_id: str, timeout: int) -> Path:
    import yaml

    source = Path(__file__).parents[1] / "deploy/milestone-2b-case-catalog.yaml"
    document = yaml.safe_load(source.read_text(encoding="utf-8"))
    matching = [case for case in document["cases"] if case["case_id"] == case_id]
    assert len(matching) == 1
    matching[0]["timeout_seconds"] = timeout
    destination = tmp_path / "catalog.yaml"
    destination.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return destination


def _noncooperative_cli_source() -> str:
    return """
import asyncio
import sys

from scripts import run_milestone_2b_case_batch as batch
from scripts.milestone_2b_case_runners.base import CaseOutcome
from scripts.milestone_2b_case_runners.evidence import publish_case_evidence

mode = sys.argv[1]

async def ignore_every_cancellation():
    while True:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            continue

class Runner:
    async def run(self, context, case):
        if mode == "runner":
            await ignore_every_cancellation()
        path = publish_case_evidence(
            context=context,
            case=case,
            name="result.json",
            payload={"mode": mode},
        )
        return CaseOutcome(status="通过", reason="run complete", evidence=(path,))

    async def cleanup(self, context, case):
        if mode == "cleanup":
            await ignore_every_cancellation()

runner = Runner()
batch.resolve_runner = lambda _name: runner
raise SystemExit(batch.main(sys.argv[2:]))
"""


@pytest.mark.parametrize(
    ("mode", "extra_args", "expected_reason"),
    (
        ("runner", (), "runner timeout"),
        ("cleanup", ("--require-cleanup",), "runner cleanup timeout"),
    ),
)
def test_cli_hard_bounds_noncooperative_runner_and_cleanup(
    tmp_path: Path,
    mode: str,
    extra_args: tuple[str, ...],
    expected_reason: str,
) -> None:
    import subprocess
    import sys

    catalog = _catalog_with_case_timeout(tmp_path, "DEP-013", 1)
    release_root = _release_root(tmp_path)
    command = [
        sys.executable,
        "-c",
        _noncooperative_cli_source(),
        mode,
        "--catalog",
        str(catalog),
        "--release-root",
        str(release_root),
        "--phase",
        "deployment",
        "--case",
        "DEP-013",
        "--concurrency",
        "1",
        *extra_args,
    ]

    completed = subprocess.run(
        command,
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )

    assert completed.returncode == 1, completed.stderr
    execution = json.loads(
        (release_root / "negative/executions/DEP-013.json").read_text(
            encoding="utf-8"
        )
    )
    assert execution["status"] == "失败"
    assert expected_reason in execution["reason"]
    assert any(
        path.name.startswith("framework-failure-")
        for path in (release_root / "negative/evidence/DEP-013").iterdir()
    )


def _controlled_command_cli_source(
    parent_pid_file: Path,
    child_pid_file: Path,
    batch_pid_file: Path | None = None,
) -> str:
    batch_pid_statement = (
        f'open({str(batch_pid_file)!r}, "w").write(str(os.getppid()))'
        if batch_pid_file is not None
        else "pass"
    )
    probe_source = f"""
import os
import signal
import subprocess
import sys
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
{batch_pid_statement}
open({str(parent_pid_file)!r}, "w").write(str(os.getpid()))
child_source = {f'''import os,signal,time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
open({str(child_pid_file)!r}, "w").write(str(os.getpid()))
time.sleep(60)
'''!r}
subprocess.Popen([sys.executable, "-c", child_source])
print("started", flush=True)
time.sleep(60)
"""
    return f"""
import sys

from scripts import run_milestone_2b_case_batch as batch
from scripts.milestone_2b_case_runners import process
from scripts.milestone_2b_case_runners.process import (
    CommandSpec,
    ProcessGroupProbeAction,
    run_command,
)

process._PROCESS_GROUP_PROBE_SOURCE = {probe_source!r}

class Runner:
    async def run(self, context, case):
        return await run_command(
            context=context,
            command=CommandSpec(
                action=ProcessGroupProbeAction(
                    spawn_child=True,
                    parent_exits=False,
                    ignore_sigterm=True,
                )
            ),
            timeout_seconds=60,
            terminate_grace_seconds=2.5,
        )

runner = Runner()
batch.resolve_runner = lambda _name: runner
raise SystemExit(batch.main(sys.argv[1:]))
"""


def test_supervisor_deadline_includes_maximum_command_termination_budget() -> None:
    from scripts import run_milestone_2b_case_batch as batch
    from scripts.milestone_2b_case_runners import process

    termination_budget = getattr(
        process,
        "maximum_command_termination_budget_seconds",
        None,
    )

    assert callable(termination_budget)
    case = _case("DEP-013", timeout_seconds=1)
    assert batch._supervisor_deadline_seconds(
        (case,),  # type: ignore[arg-type]
        require_cleanup=False,
    ) == pytest.approx(
        case.timeout_seconds
        + batch.CASE_TASK_CANCEL_GRACE_SECONDS
        + termination_budget()
        + batch.SUPERVISOR_FIXED_GRACE_SECONDS
    )
    assert batch._supervisor_deadline_seconds(
        (case,),  # type: ignore[arg-type]
        require_cleanup=True,
    ) == pytest.approx(
        case.timeout_seconds
        + batch.CASE_TASK_CANCEL_GRACE_SECONDS
        + termination_budget()
        + min(case.timeout_seconds, 30)
        + batch.CASE_TASK_CANCEL_GRACE_SECONDS
        + termination_budget()
        + batch.SUPERVISOR_FIXED_GRACE_SECONDS
    )


def test_cli_supervisor_kills_detached_controlled_command_group(
    tmp_path: Path,
) -> None:
    import subprocess
    import sys

    from scripts import run_milestone_2b_case_batch as batch

    catalog = _catalog_with_case_timeout(tmp_path, "DEP-013", 1)
    release_root = _release_root(tmp_path)
    parent_pid_file = tmp_path / "command-parent.pid"
    child_pid_file = tmp_path / "command-child.pid"
    supervisor_timeout = (
        batch._supervisor_deadline_seconds(
            (_case("DEP-013", timeout_seconds=1),),  # type: ignore[arg-type]
            require_cleanup=False,
        )
        + batch.SUPERVISOR_SIGNAL_GRACE_SECONDS
        + batch.SUPERVISOR_FIXED_GRACE_SECONDS
        + 1
    )
    completed = subprocess.run(
        (
            sys.executable,
            "-c",
            _controlled_command_cli_source(parent_pid_file, child_pid_file),
            "--catalog",
            str(catalog),
            "--release-root",
            str(release_root),
            "--phase",
            "deployment",
            "--case",
            "DEP-013",
            "--concurrency",
            "1",
        ),
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
        timeout=supervisor_timeout,
        check=False,
    )

    assert completed.returncode == 1, completed.stderr
    parent_pid = int(parent_pid_file.read_text(encoding="utf-8"))
    child_pid = int(child_pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.killpg(parent_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.01)
    else:
        os.killpg(parent_pid, signal.SIGKILL)
        pytest.fail("controlled command process group survived CLI shutdown")
    for pid in (parent_pid, child_pid):
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_supervisor_finally_drains_result_written_just_before_child_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import run_milestone_2b_case_batch as batch

    release_root = _release_root(tmp_path)
    runner = _PublishingRunner()
    monkeypatch.setattr(batch, "resolve_runner", lambda _name: runner)
    original_drain = batch._drain_supervisor_pipes
    deferred = False

    def defer_first_drain(*args: object, **kwargs: object) -> None:
        nonlocal deferred
        if not deferred:
            deferred = True
            time.sleep(0.3)
            return
        original_drain(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(batch, "_drain_supervisor_pipes", defer_first_drain)
    result = batch._supervise_case_batch(
        cases=(_case("DEP-013"),),  # type: ignore[arg-type]
        release_root=release_root,
        concurrency=1,
        require_cleanup=False,
        require_all_selected=False,
        run_id="run-1",
        target="local",
    )

    assert deferred is True
    assert result.exit_code == 0
    assert result.error is None


def test_supervisor_keeps_result_first_read_during_termination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from scripts import run_milestone_2b_case_batch as batch

    class ForeverRunner:
        async def run(self, context: object, case: object) -> object:
            while True:
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    continue

    release_root = _release_root(tmp_path)
    monkeypatch.setattr(batch, "resolve_runner", lambda _name: ForeverRunner())
    original_drain = batch._drain_supervisor_pipes
    original_terminate = batch._terminate_supervised_batch
    termination_started = False

    def delay_result_until_termination(
        descriptors: set[int],
        **kwargs: object,
    ) -> None:
        result_descriptor = kwargs["result_descriptor"]
        result_was_open = result_descriptor in descriptors
        if not termination_started:
            descriptors.discard(result_descriptor)  # type: ignore[arg-type]
        try:
            original_drain(
                descriptors,
                **kwargs,  # type: ignore[arg-type]
            )
        finally:
            if not termination_started and result_was_open:
                descriptors.add(result_descriptor)  # type: ignore[arg-type]

    def observed_termination(**kwargs: object) -> int | None:
        nonlocal termination_started
        termination_started = True
        return original_terminate(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(batch, "_drain_supervisor_pipes", delay_result_until_termination)
    monkeypatch.setattr(batch, "_terminate_supervised_batch", observed_termination)
    result = batch._supervise_case_batch(
        cases=(_case("DEP-013", timeout_seconds=1),),  # type: ignore[arg-type]
        release_root=release_root,
        concurrency=1,
        require_cleanup=False,
        require_all_selected=False,
        run_id="run-1",
        target="local",
    )

    assert termination_started is True
    assert result.exit_code == 1
    assert result.error is None
    execution = json.loads(
        (release_root / "negative/executions/DEP-013.json").read_text(
            encoding="utf-8"
        )
    )
    assert execution["status"] == "失败"
    assert "runner timeout" in execution["reason"]


def test_supervisor_hard_deadline_runs_parent_canonical_recovery_after_reap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from scripts import run_milestone_2b_case_batch as batch

    class ForeverRunner:
        async def run(self, context: object, case: object) -> object:
            await asyncio.sleep(60)

    recovered: list[dict[str, object]] = []
    monkeypatch.setattr(batch, "resolve_runner", lambda _name: ForeverRunner())
    monkeypatch.setattr(
        batch,
        "_supervisor_deadline_seconds",
        lambda cases, *, require_cleanup: 0.0,
    )
    monkeypatch.setattr(
        batch,
        "_recover_parent_runtime_state",
        lambda **kwargs: recovered.append(kwargs),
        raising=False,
    )

    result = batch._supervise_case_batch(
        cases=(
            _case("LOAD-010", safety="canonical_runtime", timeout_seconds=1),
        ),  # type: ignore[arg-type]
        release_root=_release_root(tmp_path),
        concurrency=1,
        require_cleanup=False,
        require_all_selected=False,
        run_id="run-1",
        target="local",
    )

    assert result.exit_code == 2
    assert len(recovered) == 1
    assert recovered[0]["run_id"] == "run-1"


def test_supervisor_hard_deadline_reports_unexpected_parent_recovery_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from scripts import run_milestone_2b_case_batch as batch

    class ForeverRunner:
        async def run(self, context: object, case: object) -> object:
            await asyncio.sleep(60)

    def fail_recovery(**kwargs: object) -> None:
        raise LookupError("unexpected recovery failure")

    monkeypatch.setattr(batch, "resolve_runner", lambda _name: ForeverRunner())
    monkeypatch.setattr(
        batch,
        "_supervisor_deadline_seconds",
        lambda cases, *, require_cleanup: 0.0,
    )
    monkeypatch.setattr(batch, "_recover_parent_runtime_state", fail_recovery)

    result = batch._supervise_case_batch(
        cases=(
            _case("LOAD-010", safety="canonical_runtime", timeout_seconds=1),
        ),  # type: ignore[arg-type]
        release_root=_release_root(tmp_path),
        concurrency=1,
        require_cleanup=False,
        require_all_selected=False,
        run_id="run-1",
        target="local",
    )

    assert result.exit_code == 2
    assert result.error == (
        "parent runtime recovery failed: unexpected recovery failure"
    )


def test_parent_runtime_recovery_reuses_delegated_lock_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import run_milestone_2b_case_batch as batch

    release_root = _release_root(tmp_path)
    lock_path = release_root.parent / ".operator-lifecycle.lock"
    events: list[object] = []

    class DelegatedGuard:
        def __init__(
            self,
            observed_release_root: Path,
            holder_pid: int,
            observed_lock_path: Path,
        ) -> None:
            events.append(
                ("delegated", observed_release_root, holder_pid, observed_lock_path)
            )

        def __enter__(self) -> DelegatedGuard:
            events.append("enter")
            return self

        def __exit__(self, *args: object) -> None:
            events.append("exit")

        def held_for(self, observed_release_root: Path) -> bool:
            events.append(("held_for", observed_release_root))
            return True

    async def observed_recovery(**kwargs: object) -> None:
        events.append(
            (
                "recover",
                kwargs["cases"],
                isinstance(kwargs["maintenance_lock"], DelegatedGuard),
            )
        )

    monkeypatch.setattr(batch, "DelegatedMaintenanceLockGuard", DelegatedGuard)
    monkeypatch.setattr(
        batch,
        "MaintenanceLockGuard",
        lambda release_root: pytest.fail("parent must reuse delegated lock authority"),
    )
    monkeypatch.setattr(batch, "_run_parent_runtime_recovery", observed_recovery)
    canonical_case = _case("LOAD-010", safety="canonical_runtime")
    read_only_case = _case("LOAD-017")

    batch._recover_parent_runtime_state(
        cases=(canonical_case, read_only_case),  # type: ignore[arg-type]
        release_root=release_root,
        run_id="run-1",
        target="local",
        delegated_lock_holder_pid=123,
        delegated_lock_path=lock_path,
    )

    assert events == [
        ("delegated", release_root, 123, lock_path),
        "enter",
        ("held_for", release_root),
        ("recover", (canonical_case,), True),
        ("held_for", release_root),
        "exit",
    ]


def test_supervisor_signal_during_first_parent_close_reaps_child_and_restores_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import run_milestone_2b_case_batch as batch

    release_root = _release_root(tmp_path)
    monkeypatch.setattr(
        batch,
        "resolve_runner",
        lambda _name: _PublishingRunner(),
    )
    parent_pid = os.getpid()
    original_pipe = batch.os.pipe
    original_fork = batch.os.fork
    original_close = batch.os.close
    pipe_descriptors: list[int] = []
    child_pids: list[int] = []
    recovered: list[dict[str, object]] = []
    injected = False
    handlers_before = {
        signal_number: signal.getsignal(signal_number)
        for signal_number in (signal.SIGTERM, signal.SIGHUP)
    }
    mask_before = signal.pthread_sigmask(signal.SIG_BLOCK, ())

    def observed_pipe() -> tuple[int, int]:
        descriptors = original_pipe()
        pipe_descriptors.extend(descriptors)
        return descriptors

    def observed_fork() -> int:
        child_pid = original_fork()
        if os.getpid() == parent_pid:
            child_pids.append(child_pid)
        return child_pid

    def signal_on_first_parent_close(descriptor: int) -> None:
        nonlocal injected
        if os.getpid() == parent_pid and not injected:
            injected = True
            os.kill(parent_pid, signal.SIGTERM)
        original_close(descriptor)

    monkeypatch.setattr(batch.os, "pipe", observed_pipe)
    monkeypatch.setattr(batch.os, "fork", observed_fork)
    monkeypatch.setattr(batch.os, "close", signal_on_first_parent_close)
    monkeypatch.setattr(
        batch,
        "_recover_parent_runtime_state",
        lambda **kwargs: recovered.append(kwargs),
        raising=False,
    )
    try:
        with pytest.raises(batch._SupervisorSignal) as caught:
            batch._supervise_case_batch(
                cases=(
                    _case("LOAD-010", safety="canonical_runtime"),
                ),  # type: ignore[arg-type]
                release_root=release_root,
                concurrency=1,
                require_cleanup=False,
                require_all_selected=False,
                run_id="run-1",
                target="local",
            )

        assert caught.value.signal_number == signal.SIGTERM
        assert len(recovered) == 1
        assert recovered[0]["run_id"] == "run-1"
        assert injected is True
        assert len(child_pids) == 1
        with pytest.raises(ProcessLookupError):
            os.kill(child_pids[0], 0)
        assert len(pipe_descriptors) == 4
        for descriptor in pipe_descriptors:
            with pytest.raises(OSError):
                os.fstat(descriptor)
        assert {
            signal_number: signal.getsignal(signal_number)
            for signal_number in (signal.SIGTERM, signal.SIGHUP)
        } == handlers_before
        assert signal.pthread_sigmask(signal.SIG_BLOCK, ()) == mask_before
    finally:
        for child_pid in child_pids:
            try:
                os.killpg(child_pid, signal.SIGKILL)
            except (PermissionError, ProcessLookupError):
                try:
                    os.kill(child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                os.waitpid(child_pid, 0)
            except ChildProcessError:
                pass
        for descriptor in pipe_descriptors:
            try:
                original_close(descriptor)
            except OSError:
                pass


@pytest.mark.parametrize(
    "signal_number",
    (signal.SIGINT, signal.SIGTERM, signal.SIGHUP),
)
@pytest.mark.parametrize("signal_window", ("termination", "recovery", "both"))
@pytest.mark.parametrize("recovery_fails", (False, True))
def test_signal_is_deferred_until_termination_and_recovery_finish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    signal_number: int,
    signal_window: str,
    recovery_fails: bool,
) -> None:
    import asyncio

    from scripts import run_milestone_2b_case_batch as batch

    class ForeverRunner:
        async def run(self, context: object, case: object) -> object:
            await asyncio.sleep(60)

    parent_pid = os.getpid()
    original_fork = batch.os.fork
    original_terminate = batch._terminate_supervised_batch
    child_pids: list[int] = []
    events: list[object] = []
    signal_injected = False

    def observed_fork() -> int:
        child_pid = original_fork()
        if os.getpid() == parent_pid:
            child_pids.append(child_pid)
        return child_pid

    def signal_at_termination_entry(**kwargs: object) -> int | None:
        nonlocal signal_injected
        events.append("termination_entered")
        if signal_window in ("termination", "both") and not signal_injected:
            signal_injected = True
            os.kill(parent_pid, signal_number)
        child_status = original_terminate(**kwargs)  # type: ignore[arg-type]
        events.append("termination_reaped")
        return child_status

    def observed_recovery(**kwargs: object) -> None:
        child_alive = True
        try:
            os.kill(child_pids[0], 0)
        except ProcessLookupError:
            child_alive = False
        events.append(("recovery_entered", child_alive))
        if signal_window in ("recovery", "both"):
            os.kill(parent_pid, signal_number)
        events.append("recovery_finished")
        if recovery_fails:
            raise LookupError("recovery failed after deferred signal")

    monkeypatch.setattr(batch, "resolve_runner", lambda _name: ForeverRunner())
    monkeypatch.setattr(batch.os, "fork", observed_fork)
    monkeypatch.setattr(
        batch,
        "_supervisor_deadline_seconds",
        lambda cases, *, require_cleanup: 0.0,
    )
    monkeypatch.setattr(batch, "_terminate_supervised_batch", signal_at_termination_entry)
    monkeypatch.setattr(batch, "_recover_parent_runtime_state", observed_recovery)
    try:
        expected_exception = (
            KeyboardInterrupt
            if signal_number == signal.SIGINT
            else batch._SupervisorSignal
        )
        with pytest.raises(expected_exception) as caught:
            batch._supervise_case_batch(
                cases=(
                    _case("LOAD-010", safety="canonical_runtime"),
                ),  # type: ignore[arg-type]
                release_root=_release_root(tmp_path),
                concurrency=1,
                require_cleanup=False,
                require_all_selected=False,
                run_id="run-1",
                target="local",
            )

        if signal_number == signal.SIGINT:
            assert isinstance(caught.value, KeyboardInterrupt)
        else:
            assert isinstance(caught.value, batch._SupervisorSignal)
            assert caught.value.signal_number == signal_number
        if recovery_fails:
            assert isinstance(caught.value.__cause__, LookupError)
            assert str(caught.value.__cause__) == (
                "recovery failed after deferred signal"
            )
        else:
            assert caught.value.__cause__ is None
        assert events == [
            "termination_entered",
            "termination_reaped",
            ("recovery_entered", False),
            "recovery_finished",
        ]
    finally:
        for child_pid in child_pids:
            try:
                os.killpg(child_pid, signal.SIGKILL)
            except (PermissionError, ProcessLookupError):
                try:
                    os.kill(child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                os.waitpid(child_pid, 0)
            except ChildProcessError:
                pass


@pytest.mark.parametrize("child_end", ("exit_2", "sigkill"))
def test_supervisor_rejects_success_result_from_abnormal_child_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    child_end: str,
) -> None:
    from scripts import run_milestone_2b_case_batch as batch

    def false_success_child(**kwargs: object) -> None:
        batch._write_supervisor_result(
            kwargs["result_write_descriptor"],  # type: ignore[arg-type]
            batch._SupervisorResult(exit_code=0, error=None),
        )
        if child_end == "sigkill":
            os.kill(os.getpid(), signal.SIGKILL)

    monkeypatch.setattr(batch, "_batch_child_entry", false_success_child)
    result = batch._supervise_case_batch(
        cases=(_case("DEP-013"),),  # type: ignore[arg-type]
        release_root=_release_root(tmp_path),
        concurrency=1,
        require_cleanup=False,
        require_all_selected=False,
        run_id="run-1",
        target="local",
    )

    assert result.exit_code == 2
    assert result.error is not None
    assert any(
        word in result.error
        for word in ("child", "status", "exit", "signal", "success")
    )


def test_supervisor_rejects_success_that_required_active_process_group_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess
    import sys

    from scripts import run_milestone_2b_case_batch as batch

    process_group_file = tmp_path / "false-success-process-group.pid"

    def false_success_with_process_group(**kwargs: object) -> None:
        child = subprocess.Popen(
            (sys.executable, "-c", "import time; time.sleep(60)"),
            start_new_session=True,
        )
        process_group_file.write_text(str(child.pid), encoding="utf-8")
        os.write(
            kwargs["process_write_descriptor"],  # type: ignore[arg-type]
            f"+{child.pid}\n".encode("ascii"),
        )
        batch._write_supervisor_result(
            kwargs["result_write_descriptor"],  # type: ignore[arg-type]
            batch._SupervisorResult(exit_code=0, error=None),
        )
        os._exit(0)

    monkeypatch.setattr(
        batch,
        "_batch_child_entry",
        false_success_with_process_group,
    )
    process_group_id = -1
    try:
        result = batch._supervise_case_batch(
            cases=(_case("DEP-013"),),  # type: ignore[arg-type]
            release_root=_release_root(tmp_path),
            concurrency=1,
            require_cleanup=False,
            require_all_selected=False,
            run_id="run-1",
            target="local",
        )
        process_group_id = int(process_group_file.read_text(encoding="utf-8"))

        assert result.exit_code == 2
        assert result.error is not None
        assert any(
            word in result.error
            for word in ("process", "cleanup", "success")
        )
        with pytest.raises(ProcessLookupError):
            os.killpg(process_group_id, 0)
    finally:
        if process_group_id > 0:
            try:
                os.killpg(process_group_id, signal.SIGKILL)
            except (PermissionError, ProcessLookupError):
                pass


def test_supervisor_rejects_success_with_surviving_batch_group_descendant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess
    import sys

    from scripts import run_milestone_2b_case_batch as batch

    process_file = tmp_path / "false-success-batch-group.json"

    def false_success_with_batch_group_descendant(**kwargs: object) -> None:
        os.setsid()
        descendant = subprocess.Popen(
            (sys.executable, "-c", "import time; time.sleep(60)"),
            close_fds=True,
        )
        process_group_id = os.getpgrp()
        process_file.write_text(
            json.dumps(
                {
                    "process_group_id": process_group_id,
                    "descendant_pid": descendant.pid,
                }
            ),
            encoding="utf-8",
        )
        batch._write_supervisor_result(
            kwargs["result_write_descriptor"],  # type: ignore[arg-type]
            batch._SupervisorResult(exit_code=0, error=None),
        )
        for descriptor_name in (
            "result_read_descriptor",
            "result_write_descriptor",
            "process_read_descriptor",
            "process_write_descriptor",
        ):
            os.close(kwargs[descriptor_name])  # type: ignore[arg-type]
        os._exit(0)

    monkeypatch.setattr(
        batch,
        "_batch_child_entry",
        false_success_with_batch_group_descendant,
    )
    process_group_id = -1
    try:
        result = batch._supervise_case_batch(
            cases=(_case("DEP-013"),),  # type: ignore[arg-type]
            release_root=_release_root(tmp_path),
            concurrency=1,
            require_cleanup=False,
            require_all_selected=False,
            run_id="run-1",
            target="local",
        )
        process_document = json.loads(process_file.read_text(encoding="utf-8"))
        process_group_id = process_document["process_group_id"]
        descendant_pid = process_document["descendant_pid"]

        assert result.exit_code == 2
        assert result.error is not None
        assert any(
            word in result.error
            for word in ("batch", "process", "cleanup", "success")
        )
        with pytest.raises(ProcessLookupError):
            os.killpg(process_group_id, 0)
        with pytest.raises(ProcessLookupError):
            os.kill(descendant_pid, 0)
    finally:
        if process_group_id > 0:
            try:
                os.killpg(process_group_id, signal.SIGKILL)
            except (PermissionError, ProcessLookupError):
                pass


@pytest.mark.parametrize(
    ("signal_number", "expected_returncode"),
    (
        (signal.SIGINT, 130),
        (signal.SIGTERM, 128 + signal.SIGTERM),
        (signal.SIGHUP, 128 + signal.SIGHUP),
    ),
)
def test_cli_signal_cleans_batch_and_controlled_command_groups(
    tmp_path: Path,
    signal_number: int,
    expected_returncode: int,
) -> None:
    import subprocess
    import sys

    catalog = _catalog_with_case_timeout(tmp_path, "DEP-013", 30)
    release_root = _release_root(tmp_path)
    parent_pid_file = tmp_path / "signal-command-parent.pid"
    child_pid_file = tmp_path / "signal-command-child.pid"
    batch_pid_file = tmp_path / "signal-batch.pid"
    process = subprocess.Popen(
        (
            sys.executable,
            "-c",
            _controlled_command_cli_source(
                parent_pid_file,
                child_pid_file,
                batch_pid_file,
            ),
            "--catalog",
            str(catalog),
            "--release-root",
            str(release_root),
            "--phase",
            "deployment",
            "--case",
            "DEP-013",
            "--concurrency",
            "1",
        ),
        cwd=Path(__file__).parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    known_process_groups: set[int] = set()
    try:
        deadline = time.monotonic() + 5
        while not all(
            path.exists()
            for path in (parent_pid_file, child_pid_file, batch_pid_file)
        ):
            assert process.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.01)
        known_process_groups = {
            int(parent_pid_file.read_text(encoding="utf-8")),
            int(batch_pid_file.read_text(encoding="utf-8")),
        }
        process.send_signal(signal_number)
        _stdout, stderr = process.communicate(timeout=5)

        assert process.returncode == expected_returncode, stderr
        deadline = time.monotonic() + 2
        while known_process_groups and time.monotonic() < deadline:
            for process_group_id in tuple(known_process_groups):
                try:
                    os.killpg(process_group_id, 0)
                except (PermissionError, ProcessLookupError):
                    known_process_groups.remove(process_group_id)
            time.sleep(0.01)
        assert not known_process_groups
        for pid_file in (parent_pid_file, child_pid_file, batch_pid_file):
            pid = int(pid_file.read_text(encoding="utf-8"))
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for process_group_id in known_process_groups:
            try:
                os.killpg(process_group_id, signal.SIGKILL)
            except (PermissionError, ProcessLookupError):
                pass


def _tree_bytes(root: Path) -> dict[str, bytes | None]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes() if path.is_file() else None
        for path in sorted(root.rglob("*"))
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "seal_path",
    (
        Path("negative/cases.json"),
        Path("load/cases.json"),
        Path("summary/cases.json"),
    ),
)
async def test_sealed_release_rejects_batch_without_any_tree_mutation(
    tmp_path: Path,
    seal_path: Path,
) -> None:
    from scripts.run_milestone_2b_case_batch import run_case_batch

    release_root = _release_root(tmp_path)
    seal = release_root / seal_path
    seal.parent.mkdir()
    seal.write_text(
        '{"schema_version":1,"cases":[]}\n',
        encoding="utf-8",
    )
    sentinel = release_root / "preflight/sentinel.txt"
    sentinel.parent.mkdir()
    sentinel.write_bytes(b"immutable evidence\n")
    before = _tree_bytes(release_root)

    with pytest.raises(ValueError, match="sealed|summary|immutable|read-only"):
        await run_case_batch(
            cases=(_case("DEP-013"),),  # type: ignore[arg-type]
            release_root=release_root,
            runners={"testing.run": _PublishingRunner()},  # type: ignore[dict-item]
            concurrency=1,
            run_id="run-1",
            target="local",
        )

    assert _tree_bytes(release_root) == before


@pytest.mark.asyncio
async def test_runner_root_lock_rejects_rebound_replacement_without_writing_it(
    tmp_path: Path,
) -> None:
    from scripts.milestone_2b_case_runners.base import CaseOutcome
    from scripts.milestone_2b_case_runners.evidence import publish_case_evidence
    from scripts.run_milestone_2b_case_batch import run_case_batch

    release_root = _release_root(tmp_path)
    displaced_root = release_root.with_name(f"{release_root.name}.displaced")
    replacement_root = release_root.with_name(f"{release_root.name}.replacement")
    seal = replacement_root / "negative/cases.json"
    seal.parent.mkdir(parents=True)
    seal.write_text(
        '{"schema_version":1,"cases":[]}\n',
        encoding="utf-8",
    )
    sentinel = replacement_root / "preflight/sentinel.txt"
    sentinel.parent.mkdir()
    sentinel.write_bytes(b"replacement must remain immutable\n")
    before = _tree_bytes(replacement_root)

    class RebindingRunner:
        async def run(self, context: object, case: object) -> CaseOutcome:
            release_root.rename(displaced_root)
            replacement_root.rename(release_root)
            evidence = publish_case_evidence(
                context=context,  # type: ignore[arg-type]
                case=case,  # type: ignore[arg-type]
                name="result.json",
                payload={"rebound": True},
            )
            return CaseOutcome(
                status="通过",
                reason="unexpected publication",
                evidence=(evidence,),
            )

    with pytest.raises(ValueError, match="release root.*changed|binding|inode"):
        await run_case_batch(
            cases=(_case("DEP-013"),),  # type: ignore[arg-type]
            release_root=release_root,
            runners={"testing.run": RebindingRunner()},  # type: ignore[dict-item]
            concurrency=1,
            run_id="run-1",
            target="local",
        )

    assert _tree_bytes(release_root) == before


@pytest.mark.parametrize(
    "seal_path",
    (
        Path("negative/cases.json"),
        Path("load/cases.json"),
        Path("summary/cases.json"),
    ),
)
def test_cli_rejects_sealed_release_without_any_tree_mutation(
    tmp_path: Path,
    seal_path: Path,
) -> None:
    import subprocess
    import sys

    catalog = _catalog_with_case_timeout(tmp_path, "DEP-013", 1)
    release_root = _release_root(tmp_path)
    seal = release_root / seal_path
    seal.parent.mkdir()
    seal.write_text(
        '{"schema_version":1,"cases":[]}\n',
        encoding="utf-8",
    )
    sentinel = release_root / "preflight/sentinel.txt"
    sentinel.parent.mkdir()
    sentinel.write_bytes(b"immutable evidence\n")
    before = _tree_bytes(release_root)

    completed = subprocess.run(
        (
            sys.executable,
            "scripts/run_milestone_2b_case_batch.py",
            "--catalog",
            str(catalog),
            "--release-root",
            str(release_root),
            "--phase",
            "deployment",
            "--case",
            "DEP-013",
            "--concurrency",
            "1",
        ),
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )

    assert completed.returncode == 2, completed.stderr
    assert "sealed" in completed.stderr
    assert _tree_bytes(release_root) == before


def test_aggregator_ex_lock_blocks_runner_from_collect_through_all_publications(
    tmp_path: Path,
) -> None:
    import asyncio
    import subprocess
    import sys

    from scripts.aggregate_milestone_2b_cases import _release_root_lock
    from scripts.run_milestone_2b_case_batch import run_case_batch

    release_root = _release_root(tmp_path)
    report_plan = tmp_path / "report-plan.json"
    report_plan.write_text("{}\n", encoding="utf-8")
    sync_root = tmp_path / "aggregation-sync"
    sync_root.mkdir()
    source = """
import sys
import time
from argparse import Namespace
from pathlib import Path

from scripts import aggregate_milestone_2b_cases as aggregate

release_root = Path(sys.argv[1])
report_plan = Path(sys.argv[2])
sync_root = Path(sys.argv[3])

def checkpoint(name):
    (sync_root / name).write_text("entered\\n", encoding="utf-8")
    deadline = time.monotonic() + 5
    while not (sync_root / (name + ".continue")).exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("checkpoint timed out: " + name)
        time.sleep(0.01)

aggregate.parse_args = lambda: Namespace(
    release_root=release_root,
    operator_compose=Path("unused-compose"),
    smoke_manifest=Path("unused-smoke"),
    report_plan=report_plan,
    output=release_root / "summary/cases.json",
    require_all_executed=False,
)
aggregate.load_operator_inventory = lambda _path: object()
aggregate.load_report_plan_bytes = lambda _payload: {}
aggregate.load_smoke_manifest = lambda _path: []

def collect_registration_gpu_cases(**_kwargs):
    checkpoint("collect")
    return [], {}

aggregate.collect_registration_gpu_cases = collect_registration_gpu_cases
aggregate.collect_smoke_cases = lambda **_kwargs: ([], {})
aggregate.collect_case_executions = lambda **_kwargs: []
original_publish = aggregate.publish_json_once

def observed_publish(*, release_root, relative_path, document):
    original_publish(
        release_root=release_root,
        relative_path=relative_path,
        document=document,
    )
    if relative_path == Path("summary/cases.json"):
        checkpoint("summary")

aggregate.publish_json_once = observed_publish

def materialize_declaration_cases(**_kwargs):
    for category in ("negative", "load"):
        aggregate.publish_json_once(
            release_root=release_root,
            relative_path=Path(category) / "cases.json",
            document={"category": category},
        )
        checkpoint(category)
    return [], {}

aggregate.materialize_declaration_cases = materialize_declaration_cases
aggregate.validate_cases_envelope = lambda _envelope: None
raise SystemExit(aggregate.main())
"""
    process = subprocess.Popen(
        (
            sys.executable,
            "-c",
            source,
            str(release_root),
            str(report_plan),
            str(sync_root),
        ),
        cwd=Path(__file__).parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        for checkpoint in ("collect", "negative", "load", "summary"):
            marker = sync_root / checkpoint
            deadline = time.monotonic() + 5
            while not marker.exists():
                assert process.poll() is None, process.stderr.read()
                assert time.monotonic() < deadline, f"missing checkpoint: {checkpoint}"
                time.sleep(0.01)

            with pytest.raises(ValueError, match="locked"):
                with _release_root_lock(release_root, exclusive=False):
                    pass

            if checkpoint == "collect":
                before = _tree_bytes(release_root)
                with pytest.raises(ValueError, match="locked"):
                    asyncio.run(
                        run_case_batch(
                            cases=(_case("DEP-013"),),  # type: ignore[arg-type]
                            release_root=release_root,
                            runners={  # type: ignore[dict-item]
                                "testing.run": _PublishingRunner()
                            },
                            concurrency=1,
                            run_id="run-1",
                            target="local",
                        )
                    )
                assert _tree_bytes(release_root) == before
            (sync_root / f"{checkpoint}.continue").write_text(
                "continue\n",
                encoding="utf-8",
            )

        stdout, stderr = process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    assert process.returncode == 0, f"stdout={stdout}\nstderr={stderr}"
    assert (release_root / "negative/cases.json").is_file()
    assert (release_root / "load/cases.json").is_file()
    assert (release_root / "summary/cases.json").is_file()
