from __future__ import annotations

from uuid import uuid4

import pytest

from scripts.milestone_2b_case_runners import infrastructure

pytestmark = pytest.mark.integration


def _scenario(case_id: str) -> dict[str, object]:
    run_id = f"infra-{uuid4().hex[:12]}"
    names = infrastructure._expected_names(run_id, case_id)
    return {
        "schema_version": 1,
        "case_id": case_id,
        "mode": "controlled_input",
        "run_id": run_id,
        "mutation": {"case": case_id},
        "control_url": "http://127.0.0.1:18100",
        "orchestrator_url": "http://127.0.0.1:18101",
        "facerec_url": "http://127.0.0.1:18003",
        "mongodb_credentials": "m2b_test_invalid:m2b_test_invalid",
        **names,
    }


@pytest.mark.parametrize(
    "case_id",
    ["INF-008", "INF-009", "INF-010", "INF-011", "INF-012"],
)
def test_infrastructure_flow_case_uses_real_isolated_postgres_and_kafka(
    case_id: str,
) -> None:
    scenario = _scenario(case_id)

    result = infrastructure.evaluate_scenario(case_id, scenario)

    assert result["status"] == "通过", result
    observed = result["observed"]
    assert observed["database"] == scenario["database"]
    assert observed["kafka_topic"] == scenario["kafka_topic"]
    assert observed["kafka_group"] == scenario["kafka_group"]
    assert observed["postgres_repository"] == "CourseRepository"
    assert observed["kafka_producer"] == "AioKafkaProducerAdapter"
    if case_id == "INF-008":
        assert observed["kafka_consumer"] == "AioKafkaConsumerAdapter"
        assert len(observed["delivered_offsets"]) == 2
        assert observed["duplicate_nodes"] is False
        assert observed["dag_node_ids"] == observed["replayed_dag_node_ids"]
    elif case_id == "INF-009":
        assert observed["kafka_consumer"] == "AioKafkaConsumerAdapter"
        assert observed["failure_type"] == "RepositoryNotFoundError"
        assert observed["committed_offset_before_recovery"] is None
        assert observed["failed_offset"] == observed["redelivered_offset"]
    elif case_id == "INF-010":
        assert observed["send_error_type"] in {
            "MessageSizeTooLargeError",
            "RecordTooLargeError",
        }
        assert observed["publish_once_returned"] == 0
        assert observed["outbox"]["published_at"] is None
        assert observed["outbox"]["publish_attempts"] == 1
        assert observed["outbox"]["last_error"]
    elif case_id == "INF-011":
        assert observed["kafka_consumer"] == "AioKafkaConsumerAdapter"
        assert len(observed["delivered_offsets"]) == 2
        assert len(set(observed["delivered_event_ids"])) == 1
        assert observed["outbox_before_recovery"]["published_at"] is None
        assert observed["outbox_before_recovery"]["publish_attempts"] == 0
        assert observed["outbox_after_recovery"]["published_at"] is not None
        assert observed["outbox_after_recovery"]["publish_attempts"] == 1
        assert observed["duplicate_dag"] is False
    else:
        assert observed["kafka_consumer"] == "AioKafkaConsumerAdapter"
        assert observed["committed_offset_before_exit"] is None
        assert observed["first_delivered_offset"] == observed["replayed_offset"]
        assert observed["duplicate_dag"] is False
