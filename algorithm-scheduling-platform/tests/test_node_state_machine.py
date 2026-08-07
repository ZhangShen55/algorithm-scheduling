import pytest

from packages.platform_common.state_machine import InvalidNodeTransition, validate_node_transition
from packages.platform_contracts.status import NodeStatus


def test_node_state_machine_accepts_normal_execution_path() -> None:
    validate_node_transition(NodeStatus.PENDING, NodeStatus.QUEUED)
    validate_node_transition(NodeStatus.QUEUED, NodeStatus.RUNNING)
    validate_node_transition(NodeStatus.RUNNING, NodeStatus.COMPLETED)


def test_node_state_machine_rejects_terminal_state_regression() -> None:
    with pytest.raises(InvalidNodeTransition, match="不允许"):
        validate_node_transition(NodeStatus.COMPLETED, NodeStatus.RUNNING)
