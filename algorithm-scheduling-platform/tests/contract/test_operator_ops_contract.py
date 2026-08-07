import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.operator_registry_client.ops import OperatorOpsStatus, create_operator_ops_router

pytestmark = pytest.mark.contract


def test_operator_ops_endpoints_distinguish_liveness_and_model_readiness() -> None:
    state = {"lifecycle": "ONLINE"}
    app = FastAPI()
    app.include_router(
        create_operator_ops_router(
            status_provider=lambda: OperatorOpsStatus(
                lifecycle=state["lifecycle"],
                model_ready=False,
                inflight=0,
                declared_capacity=1,
            ),
            drain_callback=lambda: state.update(lifecycle="DRAINING"),
        )
    )

    with TestClient(app) as client:
        health = client.get("/ops/health")
        status = client.get("/ops/status")
        drained = client.post("/ops/drain")

    assert health.status_code == 200
    assert health.json() == {"status": "alive"}
    assert status.status_code == 200
    assert status.json()["model_ready"] is False
    assert drained.json()["lifecycle"] == "DRAINING"
