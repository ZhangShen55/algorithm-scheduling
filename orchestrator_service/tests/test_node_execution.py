from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from orchestrator_service.app.infrastructure.contract_stub import NodeExecutionContext
from orchestrator_service.app.infrastructure.node_execution import NodeExecutionRouter
from orchestrator_service.app.infrastructure.ppt_slice import PptSliceAccepted
from packages.platform_common.repository import NodeRecord, NodeResultWrite
from packages.platform_contracts.status import NodeStatus, Priority


class Repository:
    def __init__(self, nodes: list[NodeRecord] | None = None) -> None:
        self.nodes = nodes or []

    def list_nodes(self, course_task_type_id: int) -> list[NodeRecord]:
        assert course_task_type_id == 7
        return self.nodes


class UnusedPipeline:
    pass


class Fallback:
    async def execute(
        self, service_url: str | None, context: NodeExecutionContext
    ) -> NodeResultWrite:
        raise AssertionError((service_url, context.node_code))


class Downloader:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.calls: list[tuple[str, str, str, str | None]] = []

    async def download(
        self,
        task_id: str,
        source_url: str,
        media_role: str,
        *,
        download_group_id: str | None = None,
    ) -> Any:
        self.calls.append((task_id, source_url, media_role, download_group_id))
        return SimpleNamespace(path=self.path)


class Extractor:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.calls: list[tuple[str, Path, str | None]] = []

    async def extract(
        self,
        task_id: str,
        source_video_path: Path,
        *,
        download_group_id: str | None = None,
    ) -> Any:
        self.calls.append((task_id, source_video_path, download_group_id))
        return SimpleNamespace(path=self.path)


class AsrAdapter:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[tuple[str, Path, dict[str, Any]]] = []

    async def transcribe(
        self,
        instance_url: str,
        audio_path: Path,
        *,
        effective_params: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append((instance_url, audio_path, effective_params))
        return self.response


class OverviewAdapter:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, Any], str | None]] = []

    async def generate(
        self,
        instance_url: str,
        asr_response: dict[str, Any],
        *,
        model: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append((instance_url, asr_response, model))
        return self.response


class PptAdapter:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def submit(self, **kwargs: Any) -> PptSliceAccepted:
        self.calls.append(kwargs)
        return PptSliceAccepted(
            task_id=str(kwargs["task_id"]),
            operator_task_id=str(kwargs["operator_task_id"]),
            status=NodeStatus.RUNNING,
            reason="",
        )


def _context(node_code: str) -> NodeExecutionContext:
    return NodeExecutionContext(
        task_id="course-001",
        task_type="ASR",
        node_code=node_code,
        request_payload={"teacher_video_path": "http://media/teacher.mp4"},
        effective_params={
            "language": "zh",
            "showSpk": True,
            "showEmotion": True,
            "showRoleIdentify": False,
            "wordTimestamps": False,
            "hotWords": [],
        },
        node_id=11,
        course_task_type_id=7,
    )


def _node(node_code: str, result: dict[str, Any]) -> NodeRecord:
    return NodeRecord(
        id=10,
        course_task_type_id=7,
        node_code=node_code,
        status=NodeStatus.COMPLETED,
        priority=Priority.NORMAL,
        reason="完成",
        required_capability="asr_offline",
        result=result,
        artifact_path=None,
        artifact_count=None,
        progress={},
        effective_params=None,
        updated_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_asr_node_downloads_extracts_and_calls_one_real_adapter(tmp_path: Path) -> None:
    video_path = tmp_path / "teacher.mp4"
    audio_path = tmp_path / "teacher.wav"
    downloader = Downloader(video_path)
    extractor = Extractor(audio_path)
    asr_response = {"text": "课堂文本", "segments": [{"segment_text": "课堂文本"}]}
    asr = AsrAdapter(asr_response)
    router = NodeExecutionRouter(
        Repository(),
        ocr_pipeline=UnusedPipeline(),  # type: ignore[arg-type]
        keyword_pipeline=UnusedPipeline(),  # type: ignore[arg-type]
        fallback=Fallback(),
        media_downloader=downloader,
        audio_extractor=extractor,
        asr_adapter=asr,
    )

    result = await router.execute("http://asr-gpu0:8083", _context("ASR_TRANSCRIPTION"))

    assert result.result == asr_response
    assert result.effective_params == _context("ASR_TRANSCRIPTION").effective_params
    assert downloader.calls == [
        ("course-001", "http://media/teacher.mp4", "teacher", "asr-7")
    ]
    assert extractor.calls == [("course-001", video_path, "asr-7")]
    assert asr.calls == [
        ("http://asr-gpu0:8083", audio_path, _context("ASR_TRANSCRIPTION").effective_params)
    ]


@pytest.mark.asyncio
async def test_course_overview_uses_persisted_asr_result_without_extra_work() -> None:
    asr_result = {
        "text": "课堂文本",
        "segments": [{"segment_text": "课堂文本", "bg": 0, "ed": 1}],
    }
    overview_result = {"id": "overview-1", "result": {"overview": {}}}
    overview = OverviewAdapter(overview_result)
    router = NodeExecutionRouter(
        Repository([_node("ASR_TRANSCRIPTION", asr_result)]),
        ocr_pipeline=UnusedPipeline(),  # type: ignore[arg-type]
        keyword_pipeline=UnusedPipeline(),  # type: ignore[arg-type]
        fallback=Fallback(),
        course_overview_adapter=overview,
    )

    result = await router.execute(
        "http://text-analysis-cpu0:8000",
        _context("COURSE_OVERVIEW"),
    )

    assert result.result == overview_result
    assert overview.calls == [("http://text-analysis-cpu0:8000", asr_result, None)]


@pytest.mark.asyncio
async def test_ppt_slice_downloads_local_video_and_returns_async_acceptance(
    tmp_path: Path,
) -> None:
    local_video = tmp_path / "slides.mp4"
    downloader = Downloader(local_video)
    ppt = PptAdapter()
    router = NodeExecutionRouter(
        Repository(),
        ocr_pipeline=UnusedPipeline(),  # type: ignore[arg-type]
        keyword_pipeline=UnusedPipeline(),  # type: ignore[arg-type]
        fallback=Fallback(),
        media_downloader=downloader,
        ppt_slice_adapter=ppt,
        ppt_callback_base_url="http://orchestrator-service:18101/",
        ppt_terminal_callback_path="/internal/ppt-slice/callback",
        ppt_slice_threshold=0.97,
    )
    context = NodeExecutionContext(
        task_id="course-001",
        task_type="PPT",
        node_code="PPT_SLICE",
        request_payload={"slides_video_path": "http://media/slides.mp4"},
        effective_params={},
        node_id=11,
        course_task_type_id=7,
    )

    accepted = await router.execute("http://ppt-slice-cpu0:9001", context)

    assert accepted.task_id == "course-001"
    assert accepted.operator_task_id == "ppt-node-11"
    assert accepted.progress == {"source_video_path": str(local_video)}
    assert downloader.calls == [
        ("course-001", "http://media/slides.mp4", "slides", "ppt-7")
    ]
    assert ppt.calls == [
        {
            "instance_url": "http://ppt-slice-cpu0:9001",
            "local_video_path": local_video,
            "task_id": "course-001",
            "operator_task_id": "ppt-node-11",
            "callback_url": (
                "http://orchestrator-service:18101/internal/ppt-slice/callback/11"
            ),
            "threshold": 0.97,
        }
    ]
