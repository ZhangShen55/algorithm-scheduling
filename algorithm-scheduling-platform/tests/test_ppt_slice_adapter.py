import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from orchestrator_service.app.api.routes import create_orchestrator_api
from orchestrator_service.app.domain.ppt_work import make_ppt_image_id
from orchestrator_service.app.infrastructure import ppt_slice
from pydantic import ValidationError

from packages.platform_common.config import PlatformSettings
from packages.platform_common.repository import NodeResultWrite
from packages.platform_common.state_machine import InvalidNodeTransition
from packages.platform_contracts.status import NodeStatus


@pytest.mark.asyncio
async def test_adapter_uses_new_platform_internal_submission_contract() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "task_id": "course-001",
                "operator_task_id": "ppt-run-001",
                "status": 50,
                "reason": "",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = ppt_slice.PptSliceAdapter(client)
        accepted = await adapter.submit(
            instance_url="http://ppt-slice:9001",
            local_video_path=Path("/data/course/course-001/media/slides.mp4"),
            task_id="course-001",
            operator_task_id="ppt-run-001",
            callback_url="http://orchestrator:18101/internal/ppt-slice/callback/11",
            threshold=0.98,
        )

    assert captured["path"] == "/LocalVideoPPTSliceTasks/v1.0.0"
    assert captured["body"] == {
        "video_path": "/data/course/course-001/media/slides.mp4",
        "task_id": "course-001",
        "operator_task_id": "ppt-run-001",
        "result_callback_uri": "http://orchestrator:18101/internal/ppt-slice/callback/11",
        "threshold": 0.98,
    }
    assert accepted.status == 50


@pytest.mark.asyncio
async def test_adapter_rejects_relative_local_video_path_before_http() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"不应发送请求: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = ppt_slice.PptSliceAdapter(client)
        with pytest.raises(ValueError, match="绝对本地路径"):
            await adapter.submit(
                instance_url="http://ppt-slice:9001",
                local_video_path=Path("media/slides.mp4"),
                task_id="course-001",
                operator_task_id="ppt-run-001",
                callback_url="http://orchestrator:18101/internal/ppt-slice/callback/11",
            )


def test_terminal_callback_rejects_legacy_and_unsafe_identifiers() -> None:
    with pytest.raises(ValidationError):
        ppt_slice.PptSliceTerminalCallback.model_validate(
            {
                "taskId": "../course-001",
                "operatorTaskId": "ppt-run-001",
                "statusCode": 60,
                "path": "/data/result/course-001/ppt/slices",
                "manifestPath": "/data/result/course-001/ppt/manifest.json",
                "count": 1,
            }
        )


def _write_manifest(result_root: Path, *, count: int = 2) -> dict[str, object]:
    ppt_root = result_root / "course-001" / "ppt"
    slices = ppt_root / "slices"
    slices.mkdir(parents=True)
    images = []
    for index in range(count):
        image_path = slices / f"ppt-{index + 1:04d}.jpg"
        image_path.write_bytes(b"jpeg")
        images.append({"frame_seq": index + 1, "snap_time": index, "path": str(image_path)})
    manifest_path = ppt_root / "manifest.json"
    manifest = {
        "schema_version": 1,
        "task_id": "course-001",
        "operator_task_id": "ppt-run-001",
        "status": 60,
        "path": str(slices),
        "manifest_path": str(manifest_path),
        "count": count,
        "reason": "",
        "images": images,
        "dynamic_segments": [
            {
                "type": "SUSPECTED_VIDEO_PLAYBACK",
                "start_ms": 6000,
                "end_ms": 15000,
                "confidence": 0.4722,
                "reason": "sustained_visual_change",
            }
        ],
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def test_manifest_validator_accepts_complete_shared_result(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path)
    callback = ppt_slice.PptSliceTerminalCallback.model_validate(
        {key: value for key, value in manifest.items() if key not in {"schema_version", "images"}}
    )

    validated = ppt_slice.PptSliceManifestValidator(
        result_root=tmp_path,
        max_manifest_bytes=4096,
    ).validate(callback)

    assert validated.path == tmp_path / "course-001/ppt/slices"
    assert validated.count == 2
    assert validated.dynamic_segments[0].start_ms == 6000
    assert [image.ppt_image_id for image in validated.images] == [
        make_ppt_image_id("course-001", frame_seq=1, snap_time=0),
        make_ppt_image_id("course-001", frame_seq=2, snap_time=1),
    ]


def test_manifest_validator_rejects_dynamic_segments_that_differ_from_callback(
    tmp_path: Path,
) -> None:
    manifest = _write_manifest(tmp_path)
    callback_payload = {
        key: value for key, value in manifest.items() if key not in {"schema_version", "images"}
    }
    callback_payload["dynamic_segments"] = []
    callback = ppt_slice.PptSliceTerminalCallback.model_validate(callback_payload)

    with pytest.raises(ppt_slice.PptSliceManifestError, match="dynamic_segments"):
        ppt_slice.PptSliceManifestValidator(
            result_root=tmp_path,
            max_manifest_bytes=4096,
        ).validate(callback)


def test_manifest_validator_rejects_symlinked_task_ancestor(tmp_path: Path) -> None:
    real_result_root = tmp_path / "real"
    manifest = _write_manifest(real_result_root)
    exposed_result_root = tmp_path / "exposed"
    exposed_result_root.mkdir()
    (exposed_result_root / "course-001").symlink_to(
        real_result_root / "course-001",
        target_is_directory=True,
    )
    callback = ppt_slice.PptSliceTerminalCallback.model_validate(
        {
            key: str(value).replace(str(real_result_root), str(exposed_result_root))
            if key in {"path", "manifest_path"}
            else value
            for key, value in manifest.items()
            if key not in {"schema_version", "images"}
        }
    )

    with pytest.raises(ppt_slice.PptSliceManifestError, match="符号链接"):
        ppt_slice.PptSliceManifestValidator(
            result_root=exposed_result_root,
            max_manifest_bytes=4096,
        ).validate(callback)


def test_manifest_validator_rejects_callback_outside_task_result_root(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path)
    manifest["path"] = str(tmp_path / "other-course/ppt/slices")
    callback = ppt_slice.PptSliceTerminalCallback.model_validate(
        {key: value for key, value in manifest.items() if key not in {"schema_version", "images"}}
    )

    with pytest.raises(ppt_slice.PptSliceManifestError, match="任务结果目录"):
        ppt_slice.PptSliceManifestValidator(
            result_root=tmp_path,
            max_manifest_bytes=4096,
        ).validate(callback)


class _Node:
    def __init__(self, status: NodeStatus) -> None:
        self.status = status
        self.reason = ""
        self.result: dict[str, object] | None = None
        self.artifact_path: str | None = None
        self.artifact_count: int | None = None
        self.progress = {
            "task_id": "course-001",
            "operator_task_id": "ppt-run-001",
            "lease_id": "lease-ppt-001",
            "instance_id": "ppt-slice-cpu0",
            "service_url": "http://ppt-slice-cpu0:9001",
            "lease_status": "ACTIVE",
        }


class _Repository:
    def __init__(self) -> None:
        self.node = _Node(NodeStatus.RUNNING)
        self.completions: list[tuple[int, NodeResultWrite, str]] = []
        self.transitions: list[tuple[int, NodeStatus, str]] = []

    def get_node(self, node_id: int) -> _Node:
        assert node_id == 11
        return self.node

    def complete_node(self, node_id: int, result: NodeResultWrite, *, reason: str) -> _Node:
        self.completions.append((node_id, result, reason))
        self.node.status = NodeStatus.COMPLETED
        self.node.reason = reason
        self.node.result = result.result if isinstance(result.result, dict) else None
        self.node.artifact_path = result.artifact_path
        self.node.artifact_count = result.artifact_count
        self.node.progress = result.progress
        return self.node

    def transition_node(self, node_id: int, status: NodeStatus, reason: str) -> _Node:
        self.transitions.append((node_id, status, reason))
        self.node.status = status
        self.node.reason = reason
        return self.node


class _RacingRepository(_Repository):
    def __init__(self, competing_status: NodeStatus) -> None:
        super().__init__()
        self.competing_status = competing_status

    def complete_node(self, node_id: int, result: NodeResultWrite, *, reason: str) -> _Node:
        self.node.status = self.competing_status
        if self.competing_status is NodeStatus.COMPLETED:
            self.node.reason = reason
            self.node.result = result.result if isinstance(result.result, dict) else None
            self.node.artifact_path = result.artifact_path
            self.node.artifact_count = result.artifact_count
            self.node.progress = result.progress
        raise InvalidNodeTransition(f"节点状态不允许从 {self.competing_status.value} 转换到 60")

    def transition_node(self, node_id: int, status: NodeStatus, reason: str) -> _Node:
        self.node.status = self.competing_status
        self.node.reason = reason if self.competing_status is status else "并发终态冲突"
        raise InvalidNodeTransition(
            f"节点状态不允许从 {self.competing_status.value} 转换到 {status.value}"
        )


def test_terminal_handler_is_idempotent_after_durable_completion(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path)
    callback = ppt_slice.PptSliceTerminalCallback.model_validate(
        {key: value for key, value in manifest.items() if key not in {"schema_version", "images"}}
    )
    repository = _Repository()
    handler = ppt_slice.PptSliceTerminalHandler(
        repository=repository,
        validator=ppt_slice.PptSliceManifestValidator(
            result_root=tmp_path,
            max_manifest_bytes=4096,
        ),
    )

    first = handler.handle(
        node_id=11,
        expected_task_id="course-001",
        expected_operator_task_id="ppt-run-001",
        callback=callback,
    )
    duplicate = handler.handle(
        node_id=11,
        expected_task_id="course-001",
        expected_operator_task_id="ppt-run-001",
        callback=callback,
    )

    assert first.completed is True and first.duplicate is False
    assert duplicate.completed is True and duplicate.duplicate is True
    assert len(repository.completions) == 1
    assert repository.completions[0][1].artifact_count == 2
    assert repository.completions[0][1].result is not None
    assert repository.completions[0][1].progress["lease_id"] == "lease-ppt-001"
    assert repository.completions[0][1].progress["lease_status"] == "TERMINAL_PERSISTED"
    assert repository.completions[0][1].result["dynamic_segments"] == [
        {
            "type": "SUSPECTED_VIDEO_PLAYBACK",
            "start_ms": 6000,
            "end_ms": 15000,
            "confidence": 0.4722,
            "reason": "sustained_visual_change",
        }
    ]


def test_terminal_handler_treats_concurrent_completion_as_duplicate(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path)
    callback = ppt_slice.PptSliceTerminalCallback.model_validate(
        {key: value for key, value in manifest.items() if key not in {"schema_version", "images"}}
    )
    handler = ppt_slice.PptSliceTerminalHandler(
        repository=_RacingRepository(NodeStatus.COMPLETED),
        validator=ppt_slice.PptSliceManifestValidator(
            result_root=tmp_path,
            max_manifest_bytes=4096,
        ),
    )

    result = handler.handle(
        node_id=11,
        expected_task_id="course-001",
        expected_operator_task_id="ppt-run-001",
        callback=callback,
    )

    assert result.completed is True
    assert result.duplicate is True


@pytest.mark.parametrize(
    "competing_status",
    (NodeStatus.FAILED, NodeStatus.CANCELLED),
)
def test_terminal_handler_does_not_hide_concurrent_completion_conflict(
    tmp_path: Path,
    competing_status: NodeStatus,
) -> None:
    manifest = _write_manifest(tmp_path)
    callback = ppt_slice.PptSliceTerminalCallback.model_validate(
        {key: value for key, value in manifest.items() if key not in {"schema_version", "images"}}
    )
    handler = ppt_slice.PptSliceTerminalHandler(
        repository=_RacingRepository(competing_status),
        validator=ppt_slice.PptSliceManifestValidator(
            result_root=tmp_path,
            max_manifest_bytes=4096,
        ),
    )

    with pytest.raises(ppt_slice.PptSliceCallbackError, match="终态与回调冲突"):
        handler.handle(
            node_id=11,
            expected_task_id="course-001",
            expected_operator_task_id="ppt-run-001",
            callback=callback,
        )


def test_terminal_handler_treats_concurrent_failure_as_duplicate(tmp_path: Path) -> None:
    callback = ppt_slice.PptSliceTerminalCallback.model_validate(
        {
            "task_id": "course-001",
            "operator_task_id": "ppt-run-001",
            "status": 70,
            "path": str(tmp_path / "course-001/ppt/slices"),
            "manifest_path": str(tmp_path / "course-001/ppt/manifest.json"),
            "count": 0,
            "reason": "视频解码失败",
            "dynamic_segments": [],
        }
    )
    handler = ppt_slice.PptSliceTerminalHandler(
        repository=_RacingRepository(NodeStatus.FAILED),
        validator=ppt_slice.PptSliceManifestValidator(
            result_root=tmp_path,
            max_manifest_bytes=4096,
        ),
    )

    result = handler.handle_callback(node_id=11, callback=callback)

    assert result.completed is False
    assert result.duplicate is True


@pytest.mark.parametrize(
    "competing_status",
    (NodeStatus.COMPLETED, NodeStatus.CANCELLED),
)
def test_terminal_handler_does_not_hide_concurrent_failure_conflict(
    tmp_path: Path,
    competing_status: NodeStatus,
) -> None:
    callback = ppt_slice.PptSliceTerminalCallback.model_validate(
        {
            "task_id": "course-001",
            "operator_task_id": "ppt-run-001",
            "status": 70,
            "path": str(tmp_path / "course-001/ppt/slices"),
            "manifest_path": str(tmp_path / "course-001/ppt/manifest.json"),
            "count": 0,
            "reason": "视频解码失败",
            "dynamic_segments": [],
        }
    )
    handler = ppt_slice.PptSliceTerminalHandler(
        repository=_RacingRepository(competing_status),
        validator=ppt_slice.PptSliceManifestValidator(
            result_root=tmp_path,
            max_manifest_bytes=4096,
        ),
    )

    with pytest.raises(ppt_slice.PptSliceCallbackError, match="终态与回调冲突"):
        handler.handle_callback(node_id=11, callback=callback)


def test_terminal_handler_rejects_duplicate_completion_with_changed_result(
    tmp_path: Path,
) -> None:
    manifest = _write_manifest(tmp_path)
    callback = ppt_slice.PptSliceTerminalCallback.model_validate(
        {key: value for key, value in manifest.items() if key not in {"schema_version", "images"}}
    )
    handler = ppt_slice.PptSliceTerminalHandler(
        repository=_Repository(),
        validator=ppt_slice.PptSliceManifestValidator(
            result_root=tmp_path,
            max_manifest_bytes=4096,
        ),
    )
    handler.handle_callback(node_id=11, callback=callback)

    changed = callback.model_copy(update={"count": callback.count + 1})
    with pytest.raises(ppt_slice.PptSliceCallbackError, match="已持久化结果不一致"):
        handler.handle_callback(node_id=11, callback=changed)


def test_terminal_handler_accepts_duplicate_completion_with_equivalent_paths(
    tmp_path: Path,
) -> None:
    manifest = _write_manifest(tmp_path)
    callback = ppt_slice.PptSliceTerminalCallback.model_validate(
        {key: value for key, value in manifest.items() if key not in {"schema_version", "images"}}
    )
    handler = ppt_slice.PptSliceTerminalHandler(
        repository=_Repository(),
        validator=ppt_slice.PptSliceManifestValidator(
            result_root=tmp_path,
            max_manifest_bytes=4096,
        ),
    )
    handler.handle_callback(node_id=11, callback=callback)

    equivalent = callback.model_copy(
        update={
            "path": str(Path(callback.path) / ".." / "slices"),
            "manifest_path": str(
                Path(callback.manifest_path).parent / "slices" / ".." / "manifest.json"
            ),
        }
    )
    result = handler.handle_callback(node_id=11, callback=equivalent)

    assert result.completed is True
    assert result.duplicate is True


def test_terminal_handler_rejects_duplicate_failure_with_changed_reason(
    tmp_path: Path,
) -> None:
    callback = ppt_slice.PptSliceTerminalCallback.model_validate(
        {
            "task_id": "course-001",
            "operator_task_id": "ppt-run-001",
            "status": 70,
            "path": str(tmp_path / "course-001/ppt/slices"),
            "manifest_path": str(tmp_path / "course-001/ppt/manifest.json"),
            "count": 0,
            "reason": "视频解码失败",
            "dynamic_segments": [],
        }
    )
    handler = ppt_slice.PptSliceTerminalHandler(
        repository=_Repository(),
        validator=ppt_slice.PptSliceManifestValidator(
            result_root=tmp_path,
            max_manifest_bytes=4096,
        ),
    )
    handler.handle_callback(node_id=11, callback=callback)

    changed = callback.model_copy(update={"reason": "manifest 损坏"})
    with pytest.raises(ppt_slice.PptSliceCallbackError, match="已持久化原因不一致"):
        handler.handle_callback(node_id=11, callback=changed)


def test_terminal_handler_persists_failed_terminal_state_idempotently(tmp_path: Path) -> None:
    repository = _Repository()
    handler = ppt_slice.PptSliceTerminalHandler(
        repository=repository,
        validator=ppt_slice.PptSliceManifestValidator(
            result_root=tmp_path,
            max_manifest_bytes=4096,
        ),
    )
    callback = ppt_slice.PptSliceTerminalCallback.model_validate(
        {
            "task_id": "course-001",
            "operator_task_id": "ppt-run-001",
            "status": 70,
            "path": str(tmp_path / "course-001/ppt/slices"),
            "manifest_path": str(tmp_path / "course-001/ppt/manifest.json"),
            "count": 0,
            "reason": "视频解码失败",
            "dynamic_segments": [],
        }
    )

    first = handler.handle_callback(node_id=11, callback=callback)
    duplicate = handler.handle_callback(node_id=11, callback=callback)

    assert first.completed is False and first.duplicate is False
    assert duplicate.completed is False and duplicate.duplicate is True
    assert repository.transitions == [(11, NodeStatus.FAILED, "视频解码失败")]


def test_manifest_reconciliation_uses_persisted_node_identity(tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    repository = _Repository()
    handler = ppt_slice.PptSliceTerminalHandler(
        repository=repository,
        validator=ppt_slice.PptSliceManifestValidator(
            result_root=tmp_path,
            max_manifest_bytes=4096,
        ),
    )

    result = handler.reconcile(node_id=11)

    assert result.completed is True
    assert result.duplicate is False
    assert len(repository.completions) == 1


def test_orchestrator_exposes_terminal_callback_route(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path)
    callback = {
        key: value for key, value in manifest.items() if key not in {"schema_version", "images"}
    }
    handler = ppt_slice.PptSliceTerminalHandler(
        repository=_Repository(),
        validator=ppt_slice.PptSliceManifestValidator(
            result_root=tmp_path,
            max_manifest_bytes=4096,
        ),
    )
    app = create_orchestrator_api(
        PlatformSettings(service_name="orchestrator-service"),
        ppt_terminal_handler=handler,
    )

    client = TestClient(app)
    response = client.post("/internal/ppt-slice/callback/11", json=callback)
    duplicate = client.post("/internal/ppt-slice/callback/11", json=callback)
    changed_callback = {**callback, "count": 1}
    conflict = client.post(
        "/internal/ppt-slice/callback/11",
        json=changed_callback,
    )

    assert response.status_code == 200
    assert response.json() == {
        "node_id": 11,
        "completed": True,
        "duplicate": False,
        "path": str(tmp_path / "course-001/ppt/slices"),
        "count": 2,
    }
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert conflict.status_code == 409
    assert "已持久化结果不一致" in conflict.json()["detail"]


class _CapacityClient:
    def __init__(self) -> None:
        self.renewals = 0
        self.releases = 0

    async def renew(self, lease_id: str, ttl_seconds: int) -> None:
        assert lease_id == "lease-001"
        assert ttl_seconds == 60
        self.renewals += 1

    async def release(self, lease_id: str) -> None:
        assert lease_id == "lease-001"
        self.releases += 1


@pytest.mark.asyncio
async def test_capacity_lease_is_renewed_until_terminal_persistence() -> None:
    client = _CapacityClient()
    keeper = ppt_slice.PptCapacityLeaseKeeper(
        client=client,
        lease_id="lease-001",
        ttl_seconds=60,
        renew_interval_seconds=0.01,
    )

    await keeper.start()
    await asyncio.sleep(0.035)
    await keeper.release_after_terminal_persistence()

    assert client.renewals >= 2
    assert client.releases == 1


@pytest.mark.asyncio
async def test_capacity_lease_is_released_when_background_renewal_failed() -> None:
    class _FailingCapacityClient(_CapacityClient):
        async def renew(self, lease_id: str, ttl_seconds: int) -> None:
            await super().renew(lease_id, ttl_seconds=ttl_seconds)
            raise ppt_slice.PptCapacityLeaseError("续租服务不可用")

    client = _FailingCapacityClient()
    keeper = ppt_slice.PptCapacityLeaseKeeper(
        client=client,
        lease_id="lease-001",
        ttl_seconds=60,
        renew_interval_seconds=0.01,
    )

    await keeper.start()
    await asyncio.sleep(0.02)
    with pytest.raises(ppt_slice.PptCapacityLeaseError, match="续租服务不可用"):
        await keeper.release_after_terminal_persistence()

    assert client.releases == 1


@pytest.mark.asyncio
async def test_capacity_http_client_calls_control_renew_and_release_endpoints() -> None:
    requests: list[tuple[str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.path, json.loads(request.content)))
        if request.url.path.endswith("/renew"):
            return httpx.Response(
                200,
                json={
                    "lease_id": "lease-001",
                    "instance_id": "ppt-gpu0",
                    "capability": "ppt_slice",
                    "service_url": "http://ppt-slice:9001",
                    "expires_at": "2026-08-06T12:00:00+00:00",
                },
            )
        return httpx.Response(200, json={"lease_id": "lease-001", "status": "RELEASED"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ppt_slice.PptCapacityHttpClient(
            http_client=http_client,
            control_service_url="http://control-service:18100",
        )
        await client.renew("lease-001", ttl_seconds=60)
        await client.release("lease-001")

    assert requests == [
        (
            "/internal/operator-instances/lease/renew",
            {"lease_id": "lease-001", "ttl_seconds": 60},
        ),
        ("/internal/operator-instances/release", {"lease_id": "lease-001"}),
    ]
