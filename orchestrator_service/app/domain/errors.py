class CapacityUnavailableError(RuntimeError):
    """The requested operator capability currently has no leaseable instance."""


class LeaseRenewalError(RuntimeError):
    """租约无法在 TTL 安全窗口内确认续期。"""
