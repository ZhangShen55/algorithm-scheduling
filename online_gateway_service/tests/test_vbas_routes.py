from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import json
from typing import Any

import httpx
from fastapi.testclient import TestClient

from app.api.routes import create_online_gateway_app
from app.infrastructure.capacity import CapacityLease

VALID_PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="


class Lease:
    @asynccontextmanager
    async def acquire(self, capability: str, **kwargs: object):
        assert kwargs.get("capacity_pool") == "online"
        yield CapacityLease(
            lease_id="lease-1", instance_id="vbas-1", capability=capability,
            service_url="http://vbas", expires_at=datetime.now(UTC) + timedelta(minutes=1),
        )


def test_vbas_routes_forward_raw_response_and_online_header() -> None:
    calls: list[tuple[str, dict[str, Any], str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append((request.url.path, body, request.headers["x-algorithm-work-type"]))
        return httpx.Response(200, request=request, json={"TaskResult": [], "FreeCapacity": 7})

    app = create_online_gateway_app()
    app.state.online_lease_client = Lease()
    app.state.online_http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    payload = {"ImageList": [{"ImageId": "1", "StoragePath": VALID_PNG}], "AnalysisRule": {"AlgParams": {"PolygonList": []}}}
    person_count_payload = {"ImageList": [{"ImageID": "1", "Data": VALID_PNG}], "AnalysisRule": {"AlgParams": {"PolygonList": []}}}
    try:
        with TestClient(app) as client:
            for path, expected in [
                ("/online/vbas/teacher", "/ImageDetect/teacher/v1.0.0"),
                ("/online/vbas/student", "/ImageDetect/student/v1.0.0"),
                ("/online/vbas/person-count", "/AE/SyncTasks2"),
            ]:
                response = client.post(
                    path,
                    json=person_count_payload if path.endswith("person-count") else payload,
                )
                assert response.status_code == 200
                assert response.json() == {"TaskResult": [], "FreeCapacity": 7}
                assert calls[-1][0] == expected
                assert calls[-1][2] == "online"
    finally:
        import asyncio
        asyncio.run(app.state.online_http_client.aclose())
