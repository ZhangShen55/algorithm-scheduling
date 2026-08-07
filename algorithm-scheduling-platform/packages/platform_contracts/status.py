from enum import IntEnum, StrEnum


class NodeStatus(IntEnum):
    UNREQUESTED = 0
    PENDING = 10
    WAITING_PREREQUISITE = 20
    WAITING_OPERATOR = 30
    QUEUED = 40
    RUNNING = 50
    COMPLETED = 60
    FAILED = 70
    CANCELLED = 80


class TaskType(StrEnum):
    PPT = "PPT"
    ASR = "ASR"
    TEACHER_BEHAVIOR = "TEACHER_BEHAVIOR"
    STUDENT_BEHAVIOR = "STUDENT_BEHAVIOR"


class Priority(StrEnum):
    URGENT = "URGENT"
    NORMAL = "NORMAL"


_STATUS_TEXT = {
    NodeStatus.UNREQUESTED: "未请求",
    NodeStatus.PENDING: "待处理",
    NodeStatus.WAITING_PREREQUISITE: "等待前置节点",
    NodeStatus.WAITING_OPERATOR: "等待算子",
    NodeStatus.QUEUED: "已排队",
    NodeStatus.RUNNING: "处理中",
    NodeStatus.COMPLETED: "已完成",
    NodeStatus.FAILED: "处理失败",
    NodeStatus.CANCELLED: "已取消",
}


def status_text(status: NodeStatus | int) -> str:
    return _STATUS_TEXT[NodeStatus(status)]
