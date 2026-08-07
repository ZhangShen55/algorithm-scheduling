from pathlib import Path
from typing import Any

import pytest
from orchestrator_service.app.domain.ppt_work import PptImageWork, PptWorkLimits
from orchestrator_service.app.infrastructure.ppt_text import PptTextPipeline

from packages.platform_common.repository import (
    NodeResultWrite,
    NodeWorkItemRecord,
    NodeWorkItemWrite,
    WorkItemProgress,
)


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
        assert instance_url == "http://ocr:8000"
        assert enable_formula is False
        return {"ppt_image_id": work.ppt_image_id, "text": f"文本-{work.ordinal}"}


class FakeKeywordAdapter:
    async def extract(
        self,
        instance_url: str,
        *,
        ppt_image_id: str,
        text: str,
    ) -> dict[str, Any]:
        assert instance_url == "http://text-analysis:8000"
        return {
            "ppt_image_id": ppt_image_id,
            "keyword_response": {"result": {"keywords": [text]}},
        }


@pytest.mark.asyncio
async def test_ppt_text_pipeline_persists_per_slide_results_and_progress() -> None:
    repository = FakeRepository()
    pipeline = PptTextPipeline(
        repository,
        FakeOcrAdapter(),
        FakeKeywordAdapter(),
        PptWorkLimits(batch_size=2, max_concurrency=2),
    )
    work = [
        PptImageWork("ppt-001", Path("/ppt-001.jpg"), 0),
        PptImageWork("ppt-002", Path("/ppt-002.jpg"), 1),
    ]

    ocr_results = await pipeline.run_ocr(
        node_id=11,
        work=work,
        instance_url="http://ocr:8000",
    )
    keyword_results = await pipeline.run_keywords(
        node_id=12,
        work=work,
        ocr_results=ocr_results,
        instance_url="http://text-analysis:8000",
    )

    assert list(ocr_results) == ["ppt-001", "ppt-002"]
    assert keyword_results["ppt-001"]["ppt_image_id"] == "ppt-001"
    assert repository.completed_nodes[11].result == ocr_results
    assert repository.completed_nodes[11].progress == {
        "completed_count": 2,
        "total_count": 2,
    }
    assert repository.completed_nodes[12].result == keyword_results
    assert repository.completed_nodes[12].progress == {
        "completed_count": 2,
        "total_count": 2,
    }
    assert repository.items[12]["ppt-002"] == keyword_results["ppt-002"]


@pytest.mark.asyncio
async def test_ppt_text_pipeline_keeps_completed_items_when_later_item_fails() -> None:
    class FailingOcrAdapter(FakeOcrAdapter):
        async def recognize(
            self,
            instance_url: str,
            work: PptImageWork,
            *,
            enable_formula: bool = False,
        ) -> dict[str, Any]:
            if work.ppt_image_id == "ppt-002":
                raise RuntimeError("OCR 暂时不可用")
            return await super().recognize(
                instance_url,
                work,
                enable_formula=enable_formula,
            )

    repository = FakeRepository()
    pipeline = PptTextPipeline(
        repository,
        FailingOcrAdapter(),
        FakeKeywordAdapter(),
        PptWorkLimits(batch_size=1, max_concurrency=1),
    )
    work = [
        PptImageWork("ppt-001", Path("/ppt-001.jpg"), 0),
        PptImageWork("ppt-002", Path("/ppt-002.jpg"), 1),
    ]

    with pytest.raises(RuntimeError, match="OCR 暂时不可用"):
        await pipeline.run_ocr(
            node_id=11,
            work=work,
            instance_url="http://ocr:8000",
        )

    assert repository.items[11]["ppt-001"] == {
        "ppt_image_id": "ppt-001",
        "text": "文本-0",
    }
    assert repository.items[11]["ppt-002"] is None
    assert 11 not in repository.completed_nodes
