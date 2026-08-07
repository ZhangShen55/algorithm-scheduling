from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = PROJECT_ROOT / "deploy/docker-compose.infrastructure.yml"
DEPLOYMENT_DOC = PROJECT_ROOT / "deploy/README.md"


def test_compose_defines_persistent_postgres_kafka_and_redis() -> None:
    document = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))

    assert set(document["services"]) == {"postgres", "kafka", "redis"}
    assert set(document["volumes"]) == {"postgres_data", "kafka_data", "redis_data"}
    for service in document["services"].values():
        assert service["restart"] == "unless-stopped"
        assert "healthcheck" in service


def test_compose_documents_stable_host_ports() -> None:
    document = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))

    assert "5432:5432" in document["services"]["postgres"]["ports"]
    assert "9092:9092" in document["services"]["kafka"]["ports"]
    assert "6379:6379" in document["services"]["redis"]["ports"]

    instructions = DEPLOYMENT_DOC.read_text(encoding="utf-8")
    assert "127.0.0.1:5432" in instructions
    assert "127.0.0.1:9092" in instructions
    assert "127.0.0.1:6379" in instructions
    assert "docker compose" in instructions


def test_kafka_advertises_distinct_host_and_container_listeners() -> None:
    document = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    environment = document["services"]["kafka"]["environment"]

    assert "EXTERNAL://:9092" in environment["KAFKA_LISTENERS"]
    assert "INTERNAL://:29092" in environment["KAFKA_LISTENERS"]
    assert "EXTERNAL://127.0.0.1:9092" in environment["KAFKA_ADVERTISED_LISTENERS"]
    assert "INTERNAL://kafka:29092" in environment["KAFKA_ADVERTISED_LISTENERS"]
    assert environment["KAFKA_INTER_BROKER_LISTENER_NAME"] == "INTERNAL"

    instructions = DEPLOYMENT_DOC.read_text(encoding="utf-8")
    assert "kafka:29092" in instructions
