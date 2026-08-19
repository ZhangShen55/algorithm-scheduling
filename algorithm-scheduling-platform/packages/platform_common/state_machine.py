from packages.platform_contracts.status import NodeStatus


class InvalidNodeTransition(ValueError):
    pass


_ALLOWED_TRANSITIONS: dict[NodeStatus, frozenset[NodeStatus]] = {
    NodeStatus.PENDING: frozenset(
        {
            NodeStatus.WAITING_OPERATOR,
            NodeStatus.QUEUED,
            NodeStatus.CANCELLED,
        }
    ),
    NodeStatus.WAITING_PREREQUISITE: frozenset(
        {NodeStatus.PENDING, NodeStatus.CANCELLED}
    ),
    NodeStatus.WAITING_OPERATOR: frozenset(
        {NodeStatus.PENDING, NodeStatus.QUEUED, NodeStatus.CANCELLED}
    ),
    NodeStatus.QUEUED: frozenset(
        {NodeStatus.PENDING, NodeStatus.RUNNING, NodeStatus.CANCELLED}
    ),
    NodeStatus.RUNNING: frozenset(
        {
            NodeStatus.WAITING_OPERATOR,
            NodeStatus.COMPLETED,
            NodeStatus.FAILED,
            NodeStatus.CANCELLED,
        }
    ),
    NodeStatus.COMPLETED: frozenset(),
    NodeStatus.FAILED: frozenset(),
    NodeStatus.CANCELLED: frozenset(),
    NodeStatus.UNREQUESTED: frozenset(),
}


def validate_node_transition(current: NodeStatus, target: NodeStatus) -> None:
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise InvalidNodeTransition(
            f"节点状态不允许从 {current.value} 转换到 {target.value}"
        )
