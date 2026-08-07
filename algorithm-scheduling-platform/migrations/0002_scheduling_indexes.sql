BEGIN;

ALTER TABLE outbox_events
    ADD COLUMN claim_token uuid,
    ADD COLUMN claimed_at timestamptz;

CREATE INDEX idx_task_nodes_ready_claim
    ON task_nodes (
        required_capability,
        (CASE priority WHEN 'URGENT' THEN 0 ELSE 1 END),
        ready_at,
        id
    )
    WHERE status = 10;

CREATE INDEX idx_course_task_types_task_query
    ON course_task_types (task_id, requested_at, id);

CREATE INDEX idx_task_nodes_task_query
    ON task_nodes (course_task_type_id, created_at, id);

CREATE INDEX idx_outbox_events_pending_scan
    ON outbox_events (available_at, created_at, event_id)
    WHERE published_at IS NULL;

COMMIT;
