import pytest

from services.control_service.main import app as control_app
from services.online_gateway_service.main import app as online_app
from services.orchestrator_service.main import app as orchestrator_app
from services.vision_orchestrator_service.main import app as vision_app

pytestmark = pytest.mark.contract


@pytest.mark.parametrize(
    ("app", "expected_title"),
    (
        (control_app, "control-service"),
        (orchestrator_app, "orchestrator-service"),
        (vision_app, "vision-orchestrator-service"),
        (online_app, "online-gateway-service"),
    ),
)
def test_service_entrypoint_exposes_health_contract(app, expected_title: str) -> None:  # type: ignore[no-untyped-def]
    paths = {route.path for route in app.routes}

    assert app.title == expected_title
    assert "/health" in paths
