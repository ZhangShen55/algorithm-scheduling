"""Client used by algorithm operators to join the platform registry."""

from packages.operator_registry_client.client import (
    OperatorRegistryClient,
    OperatorRegistryClientConfig,
    OperatorRuntimeStatus,
)
from packages.operator_registry_client.lifecycle import OperatorLifecycle
from packages.operator_registry_client.ops import OperatorOpsStatus, create_operator_ops_router
from packages.operator_registry_client.runtime import OperatorRuntime, install_operator_runtime

__all__ = [
    "OperatorRegistryClient",
    "OperatorRegistryClientConfig",
    "OperatorRuntimeStatus",
    "OperatorLifecycle",
    "OperatorOpsStatus",
    "OperatorRuntime",
    "create_operator_ops_router",
    "install_operator_runtime",
]
