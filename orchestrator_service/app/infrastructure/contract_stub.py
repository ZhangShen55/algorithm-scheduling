from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import httpx
from pydantic import BaseModel, Field

from packages.platform_common.repository import NodeResultWrite


@dataclass(frozen=True, slots=True)
class NodeExecutionContext:
    task_id: str
    task_type: str
    node_code: str
    request_payload: dict[str, Any]
    effective_params: dict[str, Any] | None


class ContractStubResponse(BaseModel):
    result: dict[str, Any] | list[Any] | None = None
    artifact_path: str | None = None
    artifact_count: int | None = None
    progress: dict[str, Any] = Field(default_factory=dict)
    effective_params: dict[str, Any] | None = None


class ContractStubAdapter:
    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http_client = http_client

    async def execute(
        self,
        service_url: str,
        context: NodeExecutionContext,
    ) -> NodeResultWrite:
        response = await self._http_client.post(
            f"{service_url.rstrip('/')}/execute",
            json=asdict(context),
        )
        response.raise_for_status()
        parsed = ContractStubResponse.model_validate(response.json())
        return NodeResultWrite(
            result=parsed.result,
            artifact_path=parsed.artifact_path,
            artifact_count=parsed.artifact_count,
            progress=parsed.progress,
            effective_params=parsed.effective_params,
        )
