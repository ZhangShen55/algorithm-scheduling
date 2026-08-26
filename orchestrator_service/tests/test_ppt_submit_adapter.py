from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from orchestrator_service.app.infrastructure.ppt_slice import (
    PptSliceAdapter,
    PptSliceCallbackError,
)
from packages.platform_contracts.status import NodeStatus


def _accepted_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        request=request,
        json={
            "task_id": "course-001",
            "operator_task_id": "ppt-node-11",
            "status": NodeStatus.RUNNING,
            "reason": "",
        },
    )


async def _submit(adapter: PptSliceAdapter) -> object:
    return await adapter.submit(
        instance_url="http://ppt-slice-cpu0:9001",
        local_video_path=Path("/data/course/course-001/slides.mp4"),
        task_id="course-001",
        operator_task_id="ppt-node-11",
        callback_url="http://orchestrator-service:18101/internal/ppt-slice/callback/11",
        threshold=0.99,
    )


@pytest.mark.asyncio
async def test_ppt_submit_retries_one_transient_read_error() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadError("", request=request)
        return _accepted_response(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        accepted = await _submit(
            PptSliceAdapter(
                client,
                transport_max_attempts=2,
                transport_retry_delay_seconds=0,
            )
        )

    assert accepted.status == NodeStatus.RUNNING
    assert attempts == 2


@pytest.mark.asyncio
async def test_ppt_submit_reports_exhausted_network_error_in_chinese() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadError("", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = PptSliceAdapter(
            client,
            transport_max_attempts=2,
            transport_retry_delay_seconds=0,
        )
        with pytest.raises(
            PptSliceCallbackError,
            match="PPT 切片算子提交网络调用失败（ReadError）",
        ):
            await _submit(adapter)

    assert attempts == 2


@pytest.mark.asyncio
async def test_ppt_submit_does_not_retry_timeout() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("提交超时", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = PptSliceAdapter(
            client,
            transport_max_attempts=3,
            transport_retry_delay_seconds=0,
        )
        with pytest.raises(
            PptSliceCallbackError,
            match="PPT 切片算子提交超时（ReadTimeout）",
        ):
            await _submit(adapter)

    assert attempts == 1
