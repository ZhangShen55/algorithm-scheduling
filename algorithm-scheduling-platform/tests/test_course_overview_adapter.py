import json

import httpx
import pytest

from packages.platform_common.repository import NodeResultWrite
from services.orchestrator_service.course_overview import (
    CourseOverviewAdapter,
    CourseOverviewAdapterError,
    CourseOverviewPipeline,
    build_course_overview_request,
)


def test_build_course_overview_request_maps_real_asr_segments() -> None:
    asr_response = {
        "language": "auto",
        "segments": [
            {
                "segment_text": "第一章 函数",
                "bg": "0.17",
                "ed": "1.13",
                "speed": 230,
                "segment_words": [],
                "role": "teacher",
                "emotion": "平淡",
            },
            {
                "segment_text": "函数与映射",
                "bg": "1.13",
                "ed": "3.50",
                "role": "teacher",
            },
        ],
        "text": "第一章 函数。函数与映射。",
    }

    request = build_course_overview_request(asr_response)

    assert request == {
        "textSegments": [
            {"text": "第一章 函数", "bg": 0.17, "ed": 1.13},
            {"text": "函数与映射", "bg": 1.13, "ed": 3.5},
        ]
    }


def test_build_course_overview_request_can_pass_existing_model_option() -> None:
    request = build_course_overview_request(
        {
            "segments": [
                {"segment_text": "第一章", "bg": "0", "ed": "1"},
            ]
        },
        model="qwen-course",
    )

    assert request["model"] == "qwen-course"


@pytest.mark.parametrize(
    "asr_response, message",
    [
        ({"segments": []}, "ASR 响应没有可用于课程脑图的 segments"),
        (
            {"segments": [{"segment_text": "异常时间", "bg": "2", "ed": "1"}]},
            "ASR 第 1 个片段时间范围不合法",
        ),
    ],
)
def test_build_course_overview_request_rejects_unusable_segments(
    asr_response: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(CourseOverviewAdapterError, match=message):
        build_course_overview_request(asr_response)


@pytest.mark.asyncio
async def test_course_overview_pipeline_preserves_complete_generic_response() -> None:
    asr_response = {
        "segments": [
            {"segment_text": "第一章函数", "bg": "0.10", "ed": "2.20"},
        ]
    }
    overview_response = {
        "model": "qwen-course",
        "id": "chatcmpl-overview-001",
        "result": {
            "overview": {
                "full_overview": "本课程介绍函数。",
                "key_points": ["函数"],
                "document_skims": [
                    {"time": "0-2", "overview": "函数", "content": "第一章函数"}
                ],
                "mindmap": {
                    "overall_label": "函数",
                    "total_time": "0-2",
                    "nodes": [],
                },
            },
            "finished_time": 1_750_000_000,
            "process_time_ms": 1234,
            "finished_reason": "stop",
        },
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 30,
            "total_tokens": 130,
        },
    }
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=overview_response)

    class RecordingRepository:
        completed: NodeResultWrite | None = None

        def complete_node(
            self,
            node_id: int,
            result: NodeResultWrite,
            *,
            reason: str,
        ) -> object:
            assert node_id == 22
            assert reason == "课程脑图生成完成"
            self.completed = result
            return object()

    repository = RecordingRepository()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        pipeline = CourseOverviewPipeline(repository, CourseOverviewAdapter(client))
        result = await pipeline.run(
            node_id=22,
            instance_url="http://text-analysis:8000",
            asr_response=asr_response,
            model="qwen-course",
        )

    assert captured == {
        "path": "/v1/course_overviews",
        "body": {
            "textSegments": [
                {"text": "第一章函数", "bg": 0.1, "ed": 2.2},
            ],
            "model": "qwen-course",
        },
    }
    assert result == overview_response
    assert repository.completed is not None
    assert repository.completed.result == overview_response
