import asyncio
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from orchestrator_service.app.domain.ppt_work import PptImageWork, PptWorkLimits
from orchestrator_service.app.infrastructure.ppt_text import OcrAdapter, PptTextPipeline

from packages.platform_common.operator_registry import CapacityLease, WorkContext
from packages.platform_common.repository import (
    NodeResultWrite,
    NodeWorkItemRecord,
    NodeWorkItemWrite,
    WorkItemProgress,
)
from packages.platform_contracts.status import NodeStatus


class FakeRepository:
    def __init__(self) -> None:
        self.items: dict[int, dict[str, dict[str, Any] | None]] = {}
        self.completed_nodes: dict[int, NodeResultWrite] = {}

    def create_node_work_items(
        self,
        task_node_id: int,
        items: list[NodeWorkItemWrite],
    ) -> list[NodeWorkItemRecord]:
        node_items = self.items.setdefault(task_node_id, {})
        for item in items:
            node_items.setdefault(item.item_key, item.result)
        return []

    def complete_node_work_item(
        self,
        task_node_id: int,
        item_key: str,
        result: dict[str, Any],
        *,
        reason: str,
    ) -> WorkItemProgress:
        del reason
        self.items[task_node_id][item_key] = result
        completed_count = sum(value is not None for value in self.items[task_node_id].values())
        return WorkItemProgress(completed_count, len(self.items[task_node_id]))

    def list_node_work_items(self, task_node_id: int) -> list[NodeWorkItemRecord]:
        return [
            NodeWorkItemRecord(
                id=index + 1,
                task_node_id=task_node_id,
                item_key=item_key,
                ordinal=index,
                status=(
                    NodeStatus.COMPLETED
                    if result is not None
                    else NodeStatus.PENDING
                ),
                reason="已完成" if result is not None else "等待处理",
                result=result,
            )
            for index, (item_key, result) in enumerate(
                self.items.get(task_node_id, {}).items()
            )
        ]

    def complete_node(
        self,
        node_id: int,
        result: NodeResultWrite,
        *,
        reason: str,
    ) -> object:
        del reason
        self.completed_nodes[node_id] = result
        return object()


class FakeOcrAdapter:
    async def recognize(
        self,
        instance_url: str,
        work: PptImageWork,
        *,
        enable_formula: bool = False,
    ) -> dict[str, Any]:
        assert instance_url.startswith("http://ocr-")
        assert enable_formula is False
        return {"ppt_image_id": work.ppt_image_id, "text": f"文本-{work.ordinal}"}


class FakeLeaseClient:
    def __init__(self) -> None:
        self.acquired: list[tuple[str, WorkContext]] = []
        self.released: list[str] = []
        self.active = 0
        self.peak = 0

    async def acquire(
        self,
        capability: str,
        *,
        ttl_seconds: int | None = None,
        work_context: WorkContext | None = None,
    ) -> CapacityLease:
        assert ttl_seconds == 60
        assert work_context is not None
        self.acquired.append((capability, work_context))
        self.active += 1
        self.peak = max(self.peak, self.active)
        index = len(self.acquired) - 1
        assert capability == "ocr"
        service_url = f"http://ocr-{index % 2}:8000"
        return CapacityLease(
            lease_id=f"lease-{index}",
            instance_id=f"instance-{index}",
            capability=capability,
            service_url=service_url,
            expires_at=datetime.now(UTC) + timedelta(seconds=60),
            work_context=work_context,
        )

    async def run_with_renewal(
        self,
        lease: CapacityLease,
        operation: Awaitable[dict[str, Any]],
        *,
        ttl_seconds: int | None = None,
        hard_timeout_seconds: float,
    ) -> dict[str, Any]:
        assert ttl_seconds == 60
        assert hard_timeout_seconds == 600
        return await operation

    async def release(self, lease_id: str) -> None:
        self.released.append(lease_id)
        self.active -= 1


@pytest.mark.asyncio
async def test_ppt_ocr_pipeline_persists_per_slide_results_and_progress() -> None:
    repository = FakeRepository()
    leases = FakeLeaseClient()
    pipeline = PptTextPipeline(
        repository,
        leases,
        FakeOcrAdapter(),
        PptWorkLimits(batch_size=2, max_concurrency=2),
    )
    work = [
        PptImageWork("ppt-001", Path("/ppt-001.jpg"), 0),
        PptImageWork("ppt-002", Path("/ppt-002.jpg"), 1),
    ]

    ocr_results = await pipeline.run_ocr(
        task_id="course-001",
        node_id=11,
        work=work,
    )
    assert list(ocr_results) == ["ppt-001", "ppt-002"]
    assert repository.completed_nodes[11].result == ocr_results
    assert repository.completed_nodes[11].progress == {
        "completed_count": 2,
        "total_count": 2,
    }
    assert [capability for capability, _ in leases.acquired] == ["ocr", "ocr"]
    assert {context.item_id for _, context in leases.acquired} == {
        "ppt-001",
        "ppt-002",
    }
    assert leases.peak == 2
    assert len(leases.released) == 2


@pytest.mark.asyncio
async def test_ppt_text_pipeline_keeps_completed_items_when_later_item_fails() -> None:
    class FailingOcrAdapter(FakeOcrAdapter):
        failed = False

        async def recognize(
            self,
            instance_url: str,
            work: PptImageWork,
            *,
            enable_formula: bool = False,
        ) -> dict[str, Any]:
            if work.ppt_image_id == "ppt-002" and not self.failed:
                self.failed = True
                raise RuntimeError("OCR 暂时不可用")
            return await super().recognize(
                instance_url,
                work,
                enable_formula=enable_formula,
            )

    repository = FakeRepository()
    leases = FakeLeaseClient()
    pipeline = PptTextPipeline(
        repository,
        leases,
        FailingOcrAdapter(),
        PptWorkLimits(batch_size=1, max_concurrency=1),
    )
    work = [
        PptImageWork("ppt-001", Path("/ppt-001.jpg"), 0),
        PptImageWork("ppt-002", Path("/ppt-002.jpg"), 1),
    ]

    with pytest.raises(RuntimeError, match="OCR 暂时不可用"):
        await pipeline.run_ocr(
            task_id="course-001",
            node_id=11,
            work=work,
        )

    assert repository.items[11]["ppt-001"] == {
        "ppt_image_id": "ppt-001",
        "text": "文本-0",
    }
    assert repository.items[11]["ppt-002"] is None
    assert 11 not in repository.completed_nodes
    assert len(leases.released) == 2

    recovered = await pipeline.run_ocr(
        task_id="course-001",
        node_id=11,
        work=work,
    )

    assert list(recovered) == ["ppt-001", "ppt-002"]
    assert [context.item_id for _, context in leases.acquired] == [
        "ppt-001",
        "ppt-002",
        "ppt-002",
    ]
    assert repository.completed_nodes[11].result == recovered


@pytest.mark.asyncio
async def test_ppt_text_pipeline_releases_item_lease_when_cancelled() -> None:
    entered = asyncio.Event()

    class BlockingOcrAdapter(FakeOcrAdapter):
        async def recognize(
            self,
            instance_url: str,
            work: PptImageWork,
            *,
            enable_formula: bool = False,
        ) -> dict[str, Any]:
            del instance_url, work, enable_formula
            entered.set()
            await asyncio.Future()
            raise AssertionError("unreachable")

    repository = FakeRepository()
    leases = FakeLeaseClient()
    pipeline = PptTextPipeline(
        repository,
        leases,
        BlockingOcrAdapter(),
        PptWorkLimits(batch_size=1, max_concurrency=1),
    )
    task = asyncio.create_task(
        pipeline.run_ocr(
            task_id="course-001",
            node_id=11,
            work=[PptImageWork("ppt-001", Path("/ppt-001.jpg"), 0)],
        )
    )
    await entered.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert leases.released == ["lease-0"]
    assert leases.active == 0
    assert repository.items[11]["ppt-001"] is None
    assert 11 not in repository.completed_nodes


@pytest.mark.asyncio
async def test_ppt_text_pipeline_reuses_one_lease_during_transport_retry(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "ppt-001.jpg"
    image_path.write_bytes(b"jpeg-content")
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadError("", request=request)
        return httpx.Response(
            200,
            json={
                "err_no": 0,
                "err_msg": "",
                "key": ["ppt-001"],
                "value": ["[]"],
                "formula_results": [],
            },
        )

    repository = FakeRepository()
    leases = FakeLeaseClient()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        pipeline = PptTextPipeline(
            repository,
            leases,
            OcrAdapter(
                client,
                transport_max_attempts=2,
                transport_retry_delay_seconds=0,
            ),
            PptWorkLimits(batch_size=1, max_concurrency=1),
        )
        results = await pipeline.run_ocr(
            task_id="course-001",
            node_id=11,
            work=[PptImageWork("ppt-001", image_path, 0)],
        )

    assert attempts == 2
    assert len(leases.acquired) == 1
    assert leases.released == ["lease-0"]
    assert results["ppt-001"]["text"] == ""
