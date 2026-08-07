from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = PROJECT_ROOT / "deploy/docker-compose.platform.yml"


def test_platform_compose_closes_the_four_service_single_machine_topology() -> None:
    compose = COMPOSE_PATH.read_text(encoding="utf-8")

    assert "docker-compose.infrastructure.yml" in compose
    for service in (
        "control-service",
        "orchestrator-service",
        "vision-orchestrator-service",
        "online-gateway-service",
    ):
        assert f"  {service}:" in compose
    assert compose.count("restart: unless-stopped") >= 1
    assert compose.count("healthcheck:") >= 4
    assert compose.count("resources:") >= 1
    assert "algorithm-platform" in compose


def test_platform_compose_uses_container_addresses_and_shared_storage() -> None:
    compose = COMPOSE_PATH.read_text(encoding="utf-8")

    assert "postgres:5432" in compose
    assert "kafka:29092" in compose
    assert "redis:6379" in compose
    assert "http://control-service:18100" in compose
    assert "http://orchestrator-service:18101" in compose
    assert "${COURSE_ROOT:-/data/course}:/data/course" in compose
    assert "${RESULT_ROOT:-/data/result}:/data/result" in compose


def test_all_platform_dockerfiles_use_repo_root_context_and_one_worker() -> None:
    for service in (
        "control_service",
        "orchestrator_service",
        "vision_orchestrator_service",
        "online_gateway_service",
    ):
        dockerfile = (
            PROJECT_ROOT / "services" / service / "docker" / "Dockerfile"
        ).read_text(encoding="utf-8")
        assert "COPY packages " in dockerfile, service
        assert "COPY services " in dockerfile, service
        assert '"--workers", "1"' in dockerfile, service
