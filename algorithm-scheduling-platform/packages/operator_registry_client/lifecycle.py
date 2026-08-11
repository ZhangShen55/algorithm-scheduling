from enum import Enum


class OperatorLifecycle(str, Enum):  # noqa: UP042 - wheel supports Python 3.10
    ONLINE = "ONLINE"
    DRAINING = "DRAINING"
    OFFLINE = "OFFLINE"
