from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Engine, RowMapping, text

from packages.platform_common.operator_registry import (
    OperatorInstance,
    OperatorInstanceNotFoundError,
    OperatorLifecycle,
)

JsonObject = dict[str, Any]


@dataclass(frozen=True, slots=True)
class OperatorInstanceEvent:
    id: int
    instance_id: str
    event_type: str
    event_payload: JsonObject
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class OperatorInstanceAuditSnapshot:
    instance_id: str
    operator_code: str
    capabilities: tuple[str, ...]
    service_url: str
    model_version: str | None
    api_version: str | None
    declared_capacity: int
    labels: JsonObject
    desired_state: str
    last_registered_at: datetime
    last_heartbeat_at: datetime | None
    unregistered_at: datetime | None
    created_at: datetime
    updated_at: datetime


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _event_from_row(row: RowMapping) -> OperatorInstanceEvent:
    return OperatorInstanceEvent(
        id=int(row["id"]),
        instance_id=str(row["instance_id"]),
        event_type=str(row["event_type"]),
        event_payload=dict(row["event_payload"]),
        occurred_at=row["occurred_at"],
    )


class OperatorAuditRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def record_registration(self, instance: OperatorInstance) -> None:
        declaration = {
            "operator_code": instance.operator_code.value,
            "capabilities": instance.capabilities,
            "service_url": instance.service_url,
            "model_version": instance.model_version,
            "api_version": instance.api_version,
            "declared_capacity": instance.declared_capacity,
            "labels": instance.labels,
            "desired_state": instance.lifecycle.value,
        }
        parameters = {
            "instance_id": instance.instance_id,
            "operator_code": instance.operator_code.value,
            "capabilities": _json(instance.capabilities),
            "service_url": instance.service_url,
            "model_version": instance.model_version,
            "api_version": instance.api_version,
            "declared_capacity": instance.declared_capacity,
            "labels": _json(instance.labels),
            "desired_state": instance.lifecycle.value,
        }

        with self._engine.begin() as connection:
            inserted = connection.execute(
                text(
                    """
                    INSERT INTO operator_instances (
                        instance_id,
                        operator_code,
                        capabilities,
                        service_url,
                        model_version,
                        api_version,
                        declared_capacity,
                        labels,
                        desired_state
                    )
                    VALUES (
                        :instance_id,
                        :operator_code,
                        CAST(:capabilities AS jsonb),
                        :service_url,
                        :model_version,
                        :api_version,
                        :declared_capacity,
                        CAST(:labels AS jsonb),
                        :desired_state
                    )
                    ON CONFLICT (instance_id) DO NOTHING
                    RETURNING instance_id
                    """
                ),
                parameters,
            ).scalar_one_or_none()

            if inserted is None:
                desired_state = connection.execute(
                    text(
                        """
                        UPDATE operator_instances
                        SET operator_code = :operator_code,
                            capabilities = CAST(:capabilities AS jsonb),
                            service_url = :service_url,
                            model_version = :model_version,
                            api_version = :api_version,
                            declared_capacity = :declared_capacity,
                            labels = CAST(:labels AS jsonb),
                            last_registered_at = now(),
                            unregistered_at = NULL,
                            updated_at = now()
                        WHERE instance_id = :instance_id
                        RETURNING desired_state
                        """
                    ),
                    parameters,
                ).scalar_one()
                declaration["desired_state"] = str(desired_state)

            self._append_event(
                connection,
                instance_id=instance.instance_id,
                event_type="REGISTERED" if inserted is not None else "REREGISTERED",
                event_payload=declaration,
            )

    def get_desired_lifecycle(self, instance_id: str) -> OperatorLifecycle:
        with self._engine.connect() as connection:
            desired_state = connection.execute(
                text(
                    """
                    SELECT desired_state
                    FROM operator_instances
                    WHERE instance_id = :instance_id
                    """
                ),
                {"instance_id": instance_id},
            ).scalar_one_or_none()
        if desired_state is None:
            raise OperatorInstanceNotFoundError(instance_id)
        return OperatorLifecycle(str(desired_state))

    def get_instance_snapshot(self, instance_id: str) -> OperatorInstanceAuditSnapshot:
        """Read persisted audit facts without reinterpreting retired operator codes."""
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT instance_id,
                           operator_code,
                           capabilities,
                           service_url,
                           model_version,
                           api_version,
                           declared_capacity,
                           labels,
                           desired_state,
                           last_registered_at,
                           last_heartbeat_at,
                           unregistered_at,
                           created_at,
                           updated_at
                    FROM operator_instances
                    WHERE instance_id = :instance_id
                    """
                ),
                {"instance_id": instance_id},
            ).mappings().one_or_none()
        if row is None:
            raise OperatorInstanceNotFoundError(instance_id)
        return OperatorInstanceAuditSnapshot(
            instance_id=str(row["instance_id"]),
            operator_code=str(row["operator_code"]),
            capabilities=tuple(str(value) for value in row["capabilities"]),
            service_url=str(row["service_url"]),
            model_version=(
                None if row["model_version"] is None else str(row["model_version"])
            ),
            api_version=None if row["api_version"] is None else str(row["api_version"]),
            declared_capacity=int(row["declared_capacity"]),
            labels=dict(row["labels"]),
            desired_state=str(row["desired_state"]),
            last_registered_at=row["last_registered_at"],
            last_heartbeat_at=row["last_heartbeat_at"],
            unregistered_at=row["unregistered_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def record_heartbeat_summary(
        self,
        instance_id: str,
        *,
        inflight: int,
        model_ready: bool,
        min_interval_seconds: float,
    ) -> bool:
        if min_interval_seconds < 0:
            raise ValueError("心跳审计间隔不能小于 0")

        with self._engine.begin() as connection:
            recorded_at = connection.execute(
                text(
                    """
                    UPDATE operator_instances
                    SET last_heartbeat_at = now(),
                        updated_at = now()
                    WHERE instance_id = :instance_id
                      AND (
                          last_heartbeat_at IS NULL
                          OR last_heartbeat_at <= now() - make_interval(
                              secs => CAST(:min_interval_seconds AS double precision)
                          )
                      )
                    RETURNING last_heartbeat_at
                    """
                ),
                {
                    "instance_id": instance_id,
                    "min_interval_seconds": min_interval_seconds,
                },
            ).scalar_one_or_none()

            if recorded_at is None:
                self._require_instance(connection, instance_id)
                return False

            self._append_event(
                connection,
                instance_id=instance_id,
                event_type="HEARTBEAT_SUMMARY",
                event_payload={
                    "inflight": inflight,
                    "model_ready": model_ready,
                },
            )
            return True

    def record_lifecycle(
        self,
        instance_id: str,
        lifecycle: OperatorLifecycle,
        *,
        source: str,
        reason: str | None = None,
    ) -> bool:
        with self._engine.begin() as connection:
            previous = connection.execute(
                text(
                    """
                    SELECT desired_state
                    FROM operator_instances
                    WHERE instance_id = :instance_id
                    FOR UPDATE
                    """
                ),
                {"instance_id": instance_id},
            ).scalar_one_or_none()
            if previous is None:
                raise OperatorInstanceNotFoundError(instance_id)
            if previous == lifecycle.value:
                return False

            connection.execute(
                text(
                    """
                    UPDATE operator_instances
                    SET desired_state = :desired_state,
                        updated_at = now()
                    WHERE instance_id = :instance_id
                    """
                ),
                {
                    "instance_id": instance_id,
                    "desired_state": lifecycle.value,
                },
            )
            self._append_event(
                connection,
                instance_id=instance_id,
                event_type="LIFECYCLE_CHANGED",
                event_payload={
                    "previous_lifecycle": previous,
                    "lifecycle": lifecycle.value,
                    "source": source,
                    "reason": reason,
                },
            )
            return True

    def record_unregistration(self, instance_id: str, *, source: str) -> bool:
        with self._engine.begin() as connection:
            snapshot = connection.execute(
                text(
                    """
                    SELECT desired_state, unregistered_at
                    FROM operator_instances
                    WHERE instance_id = :instance_id
                    FOR UPDATE
                    """
                ),
                {"instance_id": instance_id},
            ).mappings().one_or_none()
            if snapshot is None:
                raise OperatorInstanceNotFoundError(instance_id)
            if snapshot["unregistered_at"] is not None:
                return False

            connection.execute(
                text(
                    """
                    UPDATE operator_instances
                    SET unregistered_at = now(),
                        updated_at = now()
                    WHERE instance_id = :instance_id
                    """
                ),
                {"instance_id": instance_id},
            )
            self._append_event(
                connection,
                instance_id=instance_id,
                event_type="UNREGISTERED",
                event_payload={
                    "previous_lifecycle": snapshot["desired_state"],
                    "lifecycle": OperatorLifecycle.OFFLINE.value,
                    "desired_lifecycle": snapshot["desired_state"],
                    "source": source,
                },
            )
            return True

    def list_events(
        self,
        instance_id: str,
        *,
        limit: int = 100,
    ) -> list[OperatorInstanceEvent]:
        if limit <= 0:
            raise ValueError("事件查询数量必须大于 0")

        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT id, instance_id, event_type, event_payload, occurred_at
                    FROM operator_instance_events
                    WHERE instance_id = :instance_id
                    ORDER BY occurred_at DESC, id DESC
                    LIMIT :limit
                    """
                ),
                {"instance_id": instance_id, "limit": limit},
            ).mappings()
            return [_event_from_row(row) for row in rows]

    @staticmethod
    def _append_event(
        connection: Connection,
        *,
        instance_id: str,
        event_type: str,
        event_payload: JsonObject,
    ) -> None:
        connection.execute(
            text(
                """
                INSERT INTO operator_instance_events (
                    instance_id, event_type, event_payload
                )
                VALUES (
                    :instance_id, :event_type, CAST(:event_payload AS jsonb)
                )
                """
            ),
            {
                "instance_id": instance_id,
                "event_type": event_type,
                "event_payload": _json(event_payload),
            },
        )

    @staticmethod
    def _require_instance(connection: Connection, instance_id: str) -> None:
        exists = connection.execute(
            text(
                """
                SELECT 1
                FROM operator_instances
                WHERE instance_id = :instance_id
                """
            ),
            {"instance_id": instance_id},
        ).scalar_one_or_none()
        if exists is None:
            raise OperatorInstanceNotFoundError(instance_id)
