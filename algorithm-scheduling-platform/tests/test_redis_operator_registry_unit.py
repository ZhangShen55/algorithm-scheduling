from typing import Any, cast

import pytest

from packages.platform_common.operator_registry import CapacityLeaseNotFoundError
from packages.platform_common.redis_operator_registry import (
    _REGISTER_SCRIPT,
    RedisOperatorRegistry,
)


class MissingLeaseRedis:
    def eval(self, *args: object) -> int:
        del args
        return 0


class ActiveLeaseRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def eval(self, *args: object) -> int:
        self.calls.append(args)
        return 2


def test_release_missing_lease_raises_explicit_error() -> None:
    registry = RedisOperatorRegistry(cast(Any, MissingLeaseRedis()))

    with pytest.raises(CapacityLeaseNotFoundError, match="missing-lease"):
        registry.release("missing-lease")


def test_registration_script_preserves_existing_draining_lifecycle() -> None:
    assert "existing_lifecycle == 'DRAINING'" in _REGISTER_SCRIPT
    assert "registration_lifecycle = existing_lifecycle" in _REGISTER_SCRIPT
    assert "'lifecycle', registration_lifecycle" in _REGISTER_SCRIPT


def test_active_lease_count_uses_production_redis_script() -> None:
    client = ActiveLeaseRedis()
    registry = RedisOperatorRegistry(cast(Any, client), key_prefix="m2b:test:")

    assert registry.active_lease_count("vbas-gpu0") == 2
    assert client.calls[0][1:] == (
        1,
        "m2b:test:leases:vbas-gpu0",
        "m2b:test:lease:",
    )
