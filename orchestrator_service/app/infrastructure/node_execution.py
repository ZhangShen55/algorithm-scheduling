from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from packages.platform_common.media import DownloadedMedia
from packages.platform_common.repository import NodeRecord, NodeResultWrite

from ..domain.ppt_work import PptImageWork, PptSliceAsyncAccepted
from .contract_stub import NodeExecutionContext
from .ppt_slice import PptSliceAccepted
from .ppt_text import PptTextPipeline


class NodeLookupRepository(Protocol):
    def list_nodes(
        self,
        course_task_type_id: int,
        run_id: object | None = None,
    ) -> list[NodeRecord]: ...


class MediaDownloadClient(Protocol):
    async def download(
        self,
        task_id: str,
        source_url: str,
        media_role: str,
        *,
        download_group_id: str | None = None,
    ) -> DownloadedMedia: ...


class AudioExtractor(Protocol):
    async def extract(
        self,
        task_id: str,
        source_video_path: Path,
        *,
        download_group_id: str | None = None,
    ) -> Any: ...


class OfflineAsrClient(Protocol):
    async def transcribe(
        self,
        instance_url: str,
        audio_path: Path,
        *,
        effective_params: dict[str, Any],
    ) -> dict[str, Any]: ...


class PptSliceClient(Protocol):
    async def submit(
        self,
        *,
        instance_url: str,
        local_video_path: Path,
        task_id: str,
        operator_task_id: str,
        callback_url: str,
        threshold: float = 0.98,
    ) -> PptSliceAccepted: ...


class FallbackNodeAdapter(Protocol):
    async def execute(
        self,
        service_url: str | None,
        context: NodeExecutionContext,
    ) -> NodeResultWrite: ...


class NodeExecutionRouter:
    def __init__(
        self,
        repository: NodeLookupRepository,
        *,
        ocr_pipeline: PptTextPipeline,
        fallback: FallbackNodeAdapter,
        media_downloader: MediaDownloadClient | None = None,
        audio_extractor: AudioExtractor | None = None,
        asr_adapter: OfflineAsrClient | None = None,
        ppt_slice_adapter: PptSliceClient | None = None,
        ppt_callback_base_url: str = "",
        ppt_terminal_callback_path: str = "/internal/ppt-slice/callback",
        ppt_slice_threshold: float = 0.99,
    ) -> None:
        self._repository = repository
        self._ocr_pipeline = ocr_pipeline
        self._fallback = fallback
        self._media_downloader = media_downloader
        self._audio_extractor = audio_extractor
        self._asr_adapter = asr_adapter
        self._ppt_slice_adapter = ppt_slice_adapter
        self._ppt_callback_base_url = ppt_callback_base_url.rstrip("/")
        self._ppt_terminal_callback_path = "/" + ppt_terminal_callback_path.strip("/")
        self._ppt_slice_threshold = ppt_slice_threshold

    async def execute(
        self,
        service_url: str | None,
        context: NodeExecutionContext,
    ) -> NodeResultWrite | PptSliceAsyncAccepted:
        if context.node_code == "PPT_SLICE":
            return await self._execute_ppt_slice(service_url, context)
        if context.node_code == "PPT_OCR":
            return await self._execute_ocr(context)
        if context.node_code == "ASR_TRANSCRIPTION":
            return await self._execute_asr(service_url, context)
        return await self._fallback.execute(service_url, context)

    async def _execute_ppt_slice(
        self,
        service_url: str | None,
        context: NodeExecutionContext,
    ) -> PptSliceAsyncAccepted:
        if service_url is None:
            raise RuntimeError("PPT 切片节点缺少算子实例地址")
        if self._media_downloader is None or self._ppt_slice_adapter is None:
            raise RuntimeError("PPT 切片真实执行适配器尚未装配")
        if not self._ppt_callback_base_url:
            raise RuntimeError("PPT 终态回调地址尚未配置")
        source_url = context.request_payload.get("slides_video_path")
        if not isinstance(source_url, str) or not source_url:
            raise RuntimeError("PPT 切片节点缺少 slides_video_path")
        node_id = self._require_node_id(context)
        group_id = self._download_group_id(context)
        downloaded = await self._media_downloader.download(
            context.task_id,
            source_url,
            "slides",
            download_group_id=group_id,
        )
        operator_task_id = f"ppt-node-{node_id}"
        callback_url = (
            f"{self._ppt_callback_base_url}{self._ppt_terminal_callback_path}/{node_id}"
        )
        accepted = await self._ppt_slice_adapter.submit(
            instance_url=service_url,
            local_video_path=downloaded.path,
            task_id=context.task_id,
            operator_task_id=operator_task_id,
            callback_url=callback_url,
            threshold=self._ppt_slice_threshold,
        )
        return PptSliceAsyncAccepted(
            task_id=accepted.task_id,
            operator_task_id=accepted.operator_task_id,
            reason=accepted.reason or "PPT 切片任务已由算子受理",
            progress={"source_video_path": str(downloaded.path)},
        )

    async def _execute_asr(
        self,
        service_url: str | None,
        context: NodeExecutionContext,
    ) -> NodeResultWrite:
        if service_url is None:
            raise RuntimeError("ASR 节点缺少算子实例地址")
        if (
            self._media_downloader is None
            or self._audio_extractor is None
            or self._asr_adapter is None
        ):
            raise RuntimeError("ASR 真实执行适配器尚未装配")
        source_url = context.request_payload.get("teacher_video_path")
        if not isinstance(source_url, str) or not source_url:
            raise RuntimeError("ASR 节点缺少 teacher_video_path")
        if not isinstance(context.effective_params, dict):
            raise RuntimeError("ASR 节点缺少 effective_params")
        group_id = self._download_group_id(context)
        downloaded = await self._media_downloader.download(
            context.task_id,
            source_url,
            "teacher",
            download_group_id=group_id,
        )
        extracted = await self._audio_extractor.extract(
            context.task_id,
            downloaded.path,
            download_group_id=group_id,
        )
        response = await self._asr_adapter.transcribe(
            service_url,
            extracted.path,
            effective_params=context.effective_params,
        )
        return NodeResultWrite(
            result=response,
            effective_params=context.effective_params,
            progress={
                "source_video_path": str(downloaded.path),
                "audio_path": str(extracted.path),
            },
        )

    async def _execute_ocr(self, context: NodeExecutionContext) -> NodeResultWrite:
        node_id = self._require_node_id(context)
        work = self._ppt_work(context, source_node_code="PPT_SLICE")
        results = await self._ocr_pipeline.run_ocr(
            task_id=context.task_id,
            node_id=node_id,
            work=work,
            complete_node=False,
        )
        return self._node_result(results)

    def _ppt_work(
        self,
        context: NodeExecutionContext,
        *,
        source_node_code: str,
    ) -> list[PptImageWork]:
        source = self._node(context, source_node_code)
        if not isinstance(source.result, dict):
            raise RuntimeError(f"{source_node_code} 节点缺少结构化结果")
        images = source.result.get("images")
        if not isinstance(images, list):
            raise RuntimeError(f"{source_node_code} 节点缺少 images")
        work: list[PptImageWork] = []
        for ordinal, image in enumerate(images):
            if not isinstance(image, dict):
                raise RuntimeError("PPT 切片图片条目不是对象")
            image_id = image.get("ppt_image_id")
            image_path = image.get("path")
            if not isinstance(image_id, str) or not image_id:
                raise RuntimeError("PPT 切片缺少 ppt_image_id")
            if not isinstance(image_path, str):
                raise RuntimeError(f"PPT 切片缺少路径: {image_id}")
            path = Path(image_path)
            if not path.is_absolute():
                raise RuntimeError(f"PPT 切片路径必须是绝对路径: {image_id}")
            work.append(PptImageWork(image_id, path, ordinal))
        return work

    def _node(self, context: NodeExecutionContext, node_code: str) -> NodeRecord:
        nodes = self._repository.list_nodes(
            self._course_task_type_id(context),
            UUID(context.run_id) if context.run_id else None,
        )
        try:
            return next(node for node in nodes if node.node_code == node_code)
        except StopIteration as exc:
            raise RuntimeError(f"未找到前置节点: {node_code}") from exc

    def _course_task_type_id(self, context: NodeExecutionContext) -> int:
        if context.course_task_type_id is None:
            raise RuntimeError(
                f"节点执行上下文缺少 course_task_type_id: {context.node_code}"
            )
        return context.course_task_type_id

    @staticmethod
    def _download_group_id(context: NodeExecutionContext) -> str:
        if not context.submission_id:
            raise RuntimeError(f"节点执行上下文缺少 submission_id: {context.node_code}")
        return context.submission_id

    @staticmethod
    def _require_node_id(context: NodeExecutionContext) -> int:
        if context.node_id is None:
            raise RuntimeError(f"节点执行上下文缺少 node_id: {context.node_code}")
        return context.node_id

    @staticmethod
    def _node_result(results: dict[str, dict[str, object]]) -> NodeResultWrite:
        count = len(results)
        return NodeResultWrite(
            result=results,
            progress={"completed_count": count, "total_count": count},
        )
