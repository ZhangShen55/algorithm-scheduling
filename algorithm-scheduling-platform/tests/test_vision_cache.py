import asyncio
from pathlib import Path

import pytest

from services.vision_orchestrator_service.cache import (
    FrameCacheKey,
    InferenceCacheKey,
    VisionAnalysisCache,
    VisionStream,
)


def test_frame_cache_keys_task_stream_and_timestamp(tmp_path: Path) -> None:
    cache = VisionAnalysisCache(max_inference_entries=10)
    teacher_key = FrameCacheKey("course-001", VisionStream.TEACHER, 20_000)
    student_key = FrameCacheKey("course-001", VisionStream.STUDENT, 20_000)
    teacher_frame = tmp_path / "teacher-20000.jpg"
    student_frame = tmp_path / "student-20000.jpg"

    cache.remember_frame(teacher_key, teacher_frame)
    cache.remember_frame(student_key, student_frame)

    assert cache.get_frame(teacher_key) == teacher_frame
    assert cache.get_frame(student_key) == student_frame


@pytest.mark.asyncio
async def test_concurrent_inference_requests_compute_same_key_once() -> None:
    cache = VisionAnalysisCache(max_inference_entries=10)
    key = InferenceCacheKey(
        frame=FrameCacheKey("course-001", VisionStream.TEACHER, 1_200_000),
        capability="teacher_behavior",
        model_version="vbas-2.1",
        roi_version="roi-v1",
    )
    calls = 0

    async def compute() -> dict[str, object]:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return {"writing": True}

    results = await asyncio.gather(
        cache.get_or_compute_inference(key, compute),
        cache.get_or_compute_inference(key, compute),
    )

    assert calls == 1
    assert results == [{"writing": True}, {"writing": True}]


@pytest.mark.asyncio
async def test_model_and_roi_versions_isolate_inference_cache() -> None:
    cache = VisionAnalysisCache(max_inference_entries=10)
    frame = FrameCacheKey("course-001", VisionStream.STUDENT, 30_000)
    calls = 0

    async def compute() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"person_count": calls}

    first = await cache.get_or_compute_inference(
        InferenceCacheKey(frame, "student_behavior", "vbas-2.1", "roi-v1"),
        compute,
    )
    different_model = await cache.get_or_compute_inference(
        InferenceCacheKey(frame, "student_behavior", "vbas-2.2", "roi-v1"),
        compute,
    )
    different_roi = await cache.get_or_compute_inference(
        InferenceCacheKey(frame, "student_behavior", "vbas-2.2", "roi-v2"),
        compute,
    )

    assert first == {"person_count": 1}
    assert different_model == {"person_count": 2}
    assert different_roi == {"person_count": 3}
