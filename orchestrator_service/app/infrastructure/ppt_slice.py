from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from packages.platform_common.repository import NodeResultWrite
from packages.platform_common.state_machine import InvalidNodeTransition
from packages.platform_contracts.status import NodeStatus

from ..domain.ppt_work import make_ppt_image_id

_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class PptSliceCallbackError(RuntimeError):
    pass


class PptSliceManifestError(PptSliceCallbackError):
    pass


class PptSliceAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    operator_task_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    status: int
    reason: str = ""

    @field_validator("task_id", "operator_task_id")
    @classmethod
    def reject_dot_identifiers(cls, value: str) -> str:
        if value in {".", ".."}:
            raise ValueError("任务 ID 不能为 . 或 ..")
        return value


class PptDynamicSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    confidence: float = Field(ge=0, le=1)
    reason: str

    @model_validator(mode="after")
    def validate_interval(self) -> PptDynamicSegment:
        if self.start_ms >= self.end_ms:
            raise ValueError("动态区间必须满足 start_ms < end_ms")
        return self


class PptSliceTerminalCallback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    operator_task_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    status: int
    path: str
    manifest_path: str
    count: int = Field(ge=0)
    reason: str = ""
    dynamic_segments: list[PptDynamicSegment] = Field(default_factory=list)

    @field_validator("task_id", "operator_task_id")
    @classmethod
    def reject_dot_identifiers(cls, value: str) -> str:
        if value in {".", ".."}:
            raise ValueError("任务 ID 不能为 . 或 ..")
        return value


@dataclass(frozen=True, slots=True)
class PptSliceImage:
    ppt_image_id: str
    frame_seq: int
    snap_time: int
    path: Path


@dataclass(frozen=True, slots=True)
class ValidatedPptSliceResult:
    task_id: str
    operator_task_id: str
    path: Path
    manifest_path: Path
    count: int
    images: tuple[PptSliceImage, ...]
    dynamic_segments: tuple[PptDynamicSegment, ...]


@dataclass(frozen=True, slots=True)
class PptTerminalHandleResult:
    completed: bool
    duplicate: bool
    path: Path | None = None
    count: int = 0


class PptSliceAdapter:
    """Submit the platform-only PPT shared-result contract."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    async def submit(
        self,
        *,
        instance_url: str,
        local_video_path: Path,
        task_id: str,
        operator_task_id: str,
        callback_url: str,
        threshold: float = 0.98,
    ) -> PptSliceAccepted:
        if not local_video_path.is_absolute():
            raise ValueError("PPT 视频必须使用绝对本地路径")
        response = await self._http.post(
            f"{instance_url.rstrip('/')}/LocalVideoPPTSliceTasks/v1.0.0",
            json={
                "video_path": str(local_video_path),
                "task_id": task_id,
                "operator_task_id": operator_task_id,
                "result_callback_uri": callback_url,
                "threshold": threshold,
            },
        )
        response.raise_for_status()
        accepted = PptSliceAccepted.model_validate(response.json())
        if accepted.task_id != task_id or accepted.operator_task_id != operator_task_id:
            raise PptSliceCallbackError("PPT 算子受理响应的任务身份不一致")
        if accepted.status != NodeStatus.RUNNING:
            raise PptSliceCallbackError(accepted.reason or "PPT 切片任务未受理")
        return accepted


class PptSliceManifestValidator:
    def __init__(self, *, result_root: Path, max_manifest_bytes: int) -> None:
        if max_manifest_bytes <= 0:
            raise ValueError("manifest 大小上限必须大于 0")
        self._result_root = result_root.expanduser().resolve()
        self._max_manifest_bytes = max_manifest_bytes

    def _reject_symlink_ancestors(self, path: Path) -> None:
        try:
            relative = path.relative_to(self._result_root)
        except ValueError as exc:
            raise PptSliceManifestError("PPT 结果路径不在结果根目录") from exc
        current = self._result_root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise PptSliceManifestError("PPT 结果路径包含符号链接")

    def manifest_path(self, task_id: str) -> Path:
        if task_id in {".", ".."} or _TASK_ID_PATTERN.fullmatch(task_id) is None:
            raise PptSliceManifestError("PPT task_id 不合法")
        return self._result_root / task_id / "ppt" / "manifest.json"

    def load_terminal_callback(
        self,
        *,
        task_id: str,
        operator_task_id: str,
    ) -> PptSliceTerminalCallback | None:
        manifest_path = self.manifest_path(task_id)
        self._reject_symlink_ancestors(manifest_path)
        if not manifest_path.exists():
            return None
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise PptSliceManifestError("PPT manifest 不存在或不安全")
        if manifest_path.stat().st_size > self._max_manifest_bytes:
            raise PptSliceManifestError("PPT manifest 超过大小上限")
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            callback = PptSliceTerminalCallback.model_validate(
                {
                    field_name: raw.get(field_name)
                    for field_name in PptSliceTerminalCallback.model_fields
                }
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise PptSliceManifestError("PPT manifest 终态元数据不合法") from exc
        if callback.task_id != task_id or callback.operator_task_id != operator_task_id:
            raise PptSliceManifestError("PPT manifest 任务身份不一致")
        return callback

    def validate(self, callback: PptSliceTerminalCallback) -> ValidatedPptSliceResult:
        if callback.status != NodeStatus.COMPLETED:
            raise PptSliceManifestError(callback.reason or "PPT 切片未成功完成")

        task_ppt_root = self._result_root / callback.task_id / "ppt"
        expected_slices = task_ppt_root / "slices"
        expected_manifest = task_ppt_root / "manifest.json"
        callback_path = Path(callback.path).expanduser()
        callback_manifest = Path(callback.manifest_path).expanduser()
        if (
            not callback_path.is_absolute()
            or callback_path.resolve() != expected_slices.resolve()
        ):
            raise PptSliceManifestError("PPT path 不在当前任务结果目录")
        if (
            not callback_manifest.is_absolute()
            or callback_manifest.resolve() != expected_manifest.resolve()
        ):
            raise PptSliceManifestError("PPT manifest_path 不在当前任务结果目录")
        self._reject_symlink_ancestors(callback_path)
        self._reject_symlink_ancestors(callback_manifest)
        if callback_path.is_symlink() or not callback_path.is_dir():
            raise PptSliceManifestError("PPT 切片结果目录不存在或不安全")
        if callback_manifest.is_symlink() or not callback_manifest.is_file():
            raise PptSliceManifestError("PPT manifest 不存在或不安全")
        if callback_manifest.stat().st_size > self._max_manifest_bytes:
            raise PptSliceManifestError("PPT manifest 超过大小上限")

        try:
            raw = json.loads(callback_manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PptSliceManifestError("PPT manifest 不是有效 JSON") from exc
        if not isinstance(raw, dict):
            raise PptSliceManifestError("PPT manifest 顶层必须是对象")

        expected_metadata = {
            "schema_version": 1,
            "task_id": callback.task_id,
            "operator_task_id": callback.operator_task_id,
            "status": callback.status,
            "path": callback.path,
            "manifest_path": callback.manifest_path,
            "count": callback.count,
            "reason": callback.reason,
            "dynamic_segments": [
                segment.model_dump() for segment in callback.dynamic_segments
            ],
        }
        for key, expected in expected_metadata.items():
            if raw.get(key) != expected:
                raise PptSliceManifestError(f"PPT manifest 字段不一致: {key}")

        raw_images = raw.get("images")
        if not isinstance(raw_images, list) or len(raw_images) != callback.count:
            raise PptSliceManifestError("PPT manifest 图片数量与 count 不一致")

        images: list[PptSliceImage] = []
        seen_paths: set[Path] = set()
        for item in raw_images:
            if not isinstance(item, dict):
                raise PptSliceManifestError("PPT manifest 图片条目必须是对象")
            try:
                frame_seq = int(item["frame_seq"])
                snap_time = int(item["snap_time"])
                image_path = Path(item["path"]).expanduser()
            except (KeyError, TypeError, ValueError) as exc:
                raise PptSliceManifestError("PPT manifest 图片条目字段不合法") from exc
            resolved_image = image_path.resolve()
            self._reject_symlink_ancestors(image_path)
            if (
                not image_path.is_absolute()
                or not resolved_image.is_relative_to(expected_slices)
                or image_path.is_symlink()
                or not image_path.is_file()
            ):
                raise PptSliceManifestError("PPT manifest 图片不在切片目录或不存在")
            if resolved_image in seen_paths:
                raise PptSliceManifestError("PPT manifest 包含重复图片路径")
            seen_paths.add(resolved_image)
            images.append(
                PptSliceImage(
                    ppt_image_id=make_ppt_image_id(
                        callback.task_id,
                        frame_seq=frame_seq,
                        snap_time=snap_time,
                    ),
                    frame_seq=frame_seq,
                    snap_time=snap_time,
                    path=resolved_image,
                )
            )

        return ValidatedPptSliceResult(
            task_id=callback.task_id,
            operator_task_id=callback.operator_task_id,
            path=expected_slices,
            manifest_path=expected_manifest,
            count=callback.count,
            images=tuple(images),
            dynamic_segments=tuple(callback.dynamic_segments),
        )


class PptTerminalRepository(Protocol):
    def get_node(self, node_id: int) -> Any: ...

    def complete_node(
        self,
        node_id: int,
        result: NodeResultWrite,
        *,
        reason: str,
    ) -> Any: ...

    def transition_node(
        self,
        node_id: int,
        status: NodeStatus,
        reason: str,
    ) -> Any: ...


class PptSliceTerminalHandler:
    """Persist a validated PPT terminal callback idempotently."""

    def __init__(
        self,
        *,
        repository: PptTerminalRepository,
        validator: PptSliceManifestValidator,
    ) -> None:
        self._repository = repository
        self._validator = validator

    @staticmethod
    def _node_identity(node: Any) -> tuple[str, str]:
        progress = node.progress if isinstance(node.progress, dict) else {}
        task_id = progress.get("task_id")
        operator_task_id = progress.get("operator_task_id")
        if not isinstance(task_id, str) or not isinstance(operator_task_id, str):
            raise PptSliceCallbackError("PPT 运行中节点缺少持久化任务身份")
        return task_id, operator_task_id

    @staticmethod
    def _duplicate_result(
        node: Any,
        callback: PptSliceTerminalCallback,
    ) -> PptTerminalHandleResult:
        if (
            node.status is NodeStatus.COMPLETED
            and callback.status == NodeStatus.COMPLETED
        ):
            persisted_result = node.result if isinstance(node.result, dict) else {}
            expected_segments = [
                segment.model_dump() for segment in callback.dynamic_segments
            ]
            if (
                not isinstance(node.artifact_path, str)
                or Path(node.artifact_path).expanduser().resolve()
                != Path(callback.path).expanduser().resolve()
                or node.artifact_count != callback.count
                or not isinstance(persisted_result.get("manifest_path"), str)
                or Path(cast(str, persisted_result["manifest_path"])).expanduser().resolve()
                != Path(callback.manifest_path).expanduser().resolve()
                or persisted_result.get("dynamic_segments") != expected_segments
            ):
                raise PptSliceCallbackError("PPT 重复完成回调与已持久化结果不一致")
            return PptTerminalHandleResult(completed=True, duplicate=True)
        if node.status is NodeStatus.FAILED and callback.status == NodeStatus.FAILED:
            expected_reason = callback.reason or "PPT 切片处理失败"
            if node.reason != expected_reason:
                raise PptSliceCallbackError("PPT 重复失败回调与已持久化原因不一致")
            return PptTerminalHandleResult(completed=False, duplicate=True)
        raise PptSliceCallbackError(
            f"PPT 节点终态与回调冲突: {node.status} -> {callback.status}"
        )

    def handle_callback(
        self,
        *,
        node_id: int,
        callback: PptSliceTerminalCallback,
    ) -> PptTerminalHandleResult:
        node = self._repository.get_node(node_id)
        expected_task_id, expected_operator_task_id = self._node_identity(node)
        return self.handle(
            node_id=node_id,
            expected_task_id=expected_task_id,
            expected_operator_task_id=expected_operator_task_id,
            callback=callback,
        )

    def reconcile(self, *, node_id: int) -> PptTerminalHandleResult:
        node = self._repository.get_node(node_id)
        if node.status is NodeStatus.COMPLETED:
            return PptTerminalHandleResult(completed=True, duplicate=True)
        expected_task_id, expected_operator_task_id = self._node_identity(node)
        callback = self._validator.load_terminal_callback(
            task_id=expected_task_id,
            operator_task_id=expected_operator_task_id,
        )
        if callback is None:
            return PptTerminalHandleResult(completed=False, duplicate=False)
        return self.handle(
            node_id=node_id,
            expected_task_id=expected_task_id,
            expected_operator_task_id=expected_operator_task_id,
            callback=callback,
        )

    def handle(
        self,
        *,
        node_id: int,
        expected_task_id: str,
        expected_operator_task_id: str,
        callback: PptSliceTerminalCallback,
    ) -> PptTerminalHandleResult:
        if callback.task_id != expected_task_id:
            raise PptSliceCallbackError("PPT 终态回调 task_id 不匹配")
        if callback.operator_task_id != expected_operator_task_id:
            raise PptSliceCallbackError("PPT 终态回调 operator_task_id 不匹配")

        node = self._repository.get_node(node_id)
        if node.status in {
            NodeStatus.COMPLETED,
            NodeStatus.FAILED,
            NodeStatus.CANCELLED,
        }:
            return self._duplicate_result(node, callback)
        if node.status is not NodeStatus.RUNNING:
            raise PptSliceCallbackError(
                f"PPT 节点当前状态不接受终态回调: {node.status}"
            )

        if callback.status == NodeStatus.FAILED:
            reason = callback.reason or "PPT 切片处理失败"
            try:
                self._repository.transition_node(node_id, NodeStatus.FAILED, reason)
            except InvalidNodeTransition as exc:
                current = self._repository.get_node(node_id)
                if current.status is NodeStatus.FAILED:
                    return self._duplicate_result(current, callback)
                raise PptSliceCallbackError(
                    f"PPT 节点终态与回调冲突: {current.status} -> {callback.status}"
                ) from exc
            return PptTerminalHandleResult(completed=False, duplicate=False)
        if callback.status != NodeStatus.COMPLETED:
            raise PptSliceCallbackError(f"PPT 终态状态不合法: {callback.status}")

        validated = self._validator.validate(callback)
        terminal_progress = (
            dict(node.progress) if isinstance(node.progress, dict) else {}
        )
        terminal_progress.update(
            {
                "task_id": validated.task_id,
                "operator_task_id": validated.operator_task_id,
                "lease_status": "TERMINAL_PERSISTED",
                "completed_count": validated.count,
                "total_count": validated.count,
            }
        )
        result = NodeResultWrite(
            result={
                "manifest_path": str(validated.manifest_path),
                "images": [
                    {
                        "ppt_image_id": image.ppt_image_id,
                        "frame_seq": image.frame_seq,
                        "snap_time": image.snap_time,
                        "path": str(image.path),
                    }
                    for image in validated.images
                ],
                "dynamic_segments": [
                    segment.model_dump() for segment in validated.dynamic_segments
                ],
            },
            artifact_path=str(validated.path),
            artifact_count=validated.count,
            progress=terminal_progress,
        )
        try:
            self._repository.complete_node(node_id, result, reason="PPT 切片处理完成")
        except InvalidNodeTransition as exc:
            # 终态回调与对账可并发命中同一 manifest，只放行已持久化的同终态。
            current = self._repository.get_node(node_id)
            if current.status is NodeStatus.COMPLETED:
                return self._duplicate_result(current, callback)
            raise PptSliceCallbackError(
                f"PPT 节点终态与回调冲突: {current.status} -> {callback.status}"
            ) from exc
        return PptTerminalHandleResult(
            completed=True,
            duplicate=False,
            path=validated.path,
            count=validated.count,
        )


class PptCapacityClient(Protocol):
    def renew(
        self,
        lease_id: str,
        *,
        ttl_seconds: int,
    ) -> Awaitable[object]: ...

    def release(self, lease_id: str) -> Awaitable[object]: ...


class PptCapacityLeaseError(RuntimeError):
    pass


class PptCapacityHttpClient:
    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        control_service_url: str,
    ) -> None:
        self._http = http_client
        self._control_service_url = control_service_url.rstrip("/")

    async def renew(
        self,
        lease_id: str,
        *,
        ttl_seconds: int,
    ) -> dict[str, Any]:
        try:
            response = await self._http.post(
                f"{self._control_service_url}/internal/operator-instances/lease/renew",
                json={"lease_id": lease_id, "ttl_seconds": ttl_seconds},
            )
            response.raise_for_status()
            raw_body: object = response.json()
            if not isinstance(raw_body, dict):
                raise TypeError("续约响应必须是 JSON 对象")
            body = cast(dict[str, Any], raw_body)
            if body.get("lease_id") != lease_id:
                raise ValueError("续约响应 lease_id 不一致")
            return body
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            raise PptCapacityLeaseError(f"PPT 容量租约续约失败: {lease_id}") from exc

    async def release(self, lease_id: str) -> dict[str, Any]:
        try:
            response = await self._http.post(
                f"{self._control_service_url}/internal/operator-instances/release",
                json={"lease_id": lease_id},
            )
            response.raise_for_status()
            raw_body: object = response.json()
            if not isinstance(raw_body, dict):
                raise TypeError("释放响应必须是 JSON 对象")
            body = cast(dict[str, Any], raw_body)
            if body.get("lease_id") != lease_id:
                raise ValueError("释放响应 lease_id 不一致")
            return body
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            raise PptCapacityLeaseError(f"PPT 容量租约释放失败: {lease_id}") from exc


class PptCapacityLeaseKeeper:
    """Keep asynchronous PPT capacity reserved until terminal persistence commits."""

    def __init__(
        self,
        *,
        client: PptCapacityClient,
        lease_id: str,
        ttl_seconds: int,
        renew_interval_seconds: float,
        on_renewal_failure: Callable[[Exception], Awaitable[None]] | None = None,
    ) -> None:
        if ttl_seconds <= 0 or renew_interval_seconds <= 0:
            raise ValueError("PPT 容量租约参数必须大于 0")
        if renew_interval_seconds >= ttl_seconds:
            raise ValueError("PPT 容量续约间隔必须小于租约 TTL")
        self._client = client
        self._lease_id = lease_id
        self._ttl_seconds = ttl_seconds
        self._renew_interval_seconds = renew_interval_seconds
        self._on_renewal_failure = on_renewal_failure
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._released = False
        self._renewal_error: Exception | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(
                self._renew_loop(),
                name=f"renew-ppt-lease-{self._lease_id}",
            )

    async def _renew_loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self._renew_interval_seconds,
                )
                return
            except TimeoutError:
                try:
                    await self._client.renew(
                        self._lease_id,
                        ttl_seconds=self._ttl_seconds,
                    )
                except Exception as exc:  # noqa: BLE001 - capacity client is a protocol
                    self._renewal_error = exc
                    if self._on_renewal_failure is not None:
                        try:
                            await self._on_renewal_failure(exc)
                        except Exception:  # noqa: BLE001, S110 - retain renewal root cause
                            pass
                    return

    async def stop_renewal(self) -> None:
        """Stop heartbeats without releasing work still running in the operator."""

        self._stop.set()
        if self._task is not None:
            await self._task

    async def release_after_terminal_persistence(self) -> None:
        if self._released:
            return
        self._stop.set()
        try:
            if self._task is not None:
                await self._task
        finally:
            await self._client.release(self._lease_id)
            self._released = True
        if self._renewal_error is not None:
            raise self._renewal_error

    @property
    def released(self) -> bool:
        return self._released
