BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE task_type_runs (
    run_id uuid PRIMARY KEY,
    course_task_type_id bigint NOT NULL
        REFERENCES course_task_types(id) ON DELETE CASCADE,
    params_fingerprint text NOT NULL,
    effective_params jsonb NOT NULL,
    status smallint NOT NULL DEFAULT 10,
    reason text NOT NULL DEFAULT '等待离线语音转写',
    result jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    CHECK (length(btrim(params_fingerprint)) > 0),
    CHECK (status IN (10, 20, 30, 40, 50, 60, 70, 80)),
    CHECK (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at)
);

CREATE INDEX idx_task_type_runs_lookup
    ON task_type_runs (course_task_type_id, params_fingerprint, created_at DESC);

CREATE INDEX idx_task_type_runs_status
    ON task_type_runs (course_task_type_id, status, created_at DESC);

CREATE UNIQUE INDEX uq_task_type_runs_active_fingerprint
    ON task_type_runs (course_task_type_id, params_fingerprint)
    WHERE status NOT IN (70, 80);

INSERT INTO task_type_runs (
    run_id,
    course_task_type_id,
    params_fingerprint,
    effective_params,
    status,
    reason,
    created_at,
    started_at,
    finished_at
)
SELECT
    gen_random_uuid(),
    id,
    encode(digest(COALESCE(
        effective_params,
        '{"language":"auto","showSpk":false,"showEmotion":false,"showRoleIdentify":false,"wordTimestamps":false,"hotWords":[]}'::jsonb
    )::text, 'sha256'), 'hex'),
    COALESCE(
        effective_params,
        '{"language":"auto","showSpk":false,"showEmotion":false,"showRoleIdentify":false,"wordTimestamps":false,"hotWords":[]}'::jsonb
    ),
    status,
    reason,
    requested_at,
    started_at,
    finished_at
FROM course_task_types
WHERE task_type = 'ASR';

ALTER TABLE task_nodes ADD COLUMN run_id uuid;

UPDATE task_nodes AS node
SET run_id = run.run_id
FROM task_type_runs AS run
JOIN course_task_types AS task_type
  ON task_type.id = run.course_task_type_id
WHERE node.course_task_type_id = run.course_task_type_id
  AND task_type.task_type = 'ASR';

UPDATE task_nodes
SET run_id = '00000000-0000-0000-0000-000000000000'
WHERE run_id IS NULL;

ALTER TABLE task_nodes ALTER COLUMN run_id SET NOT NULL;

ALTER TABLE task_nodes
    DROP CONSTRAINT task_nodes_course_task_type_id_node_code_key;

ALTER TABLE task_nodes
    ADD CONSTRAINT task_nodes_course_type_run_node_key
    UNIQUE (course_task_type_id, run_id, node_code);

COMMIT;
