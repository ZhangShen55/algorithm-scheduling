BEGIN;

CREATE TABLE course_jobs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_id text NOT NULL UNIQUE,
    input_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (length(btrim(task_id)) > 0)
);

CREATE TABLE course_task_types (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_id text NOT NULL REFERENCES course_jobs(task_id) ON DELETE RESTRICT,
    task_type text NOT NULL,
    status smallint NOT NULL DEFAULT 10,
    priority text NOT NULL DEFAULT 'NORMAL',
    reason text NOT NULL DEFAULT '任务已接收，等待处理',
    request_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    effective_params jsonb,
    requested_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (task_id, task_type),
    CHECK (task_type IN ('PPT', 'ASR', 'TEACHER_BEHAVIOR', 'STUDENT_BEHAVIOR')),
    CHECK (status IN (10, 20, 30, 40, 50, 60, 70, 80)),
    CHECK (priority IN ('URGENT', 'NORMAL')),
    CHECK (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at)
);

CREATE TABLE task_nodes (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    course_task_type_id bigint NOT NULL
        REFERENCES course_task_types(id) ON DELETE CASCADE,
    node_code text NOT NULL,
    status smallint NOT NULL DEFAULT 20,
    priority text NOT NULL DEFAULT 'NORMAL',
    reason text NOT NULL DEFAULT '等待前置节点完成',
    required_capability text,
    prerequisite_count integer NOT NULL DEFAULT 0,
    completed_prerequisite_count integer NOT NULL DEFAULT 0,
    attempt integer NOT NULL DEFAULT 0,
    ready_at timestamptz,
    claimed_by text,
    claim_token uuid,
    claimed_at timestamptz,
    started_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (course_task_type_id, node_code),
    CHECK (length(btrim(node_code)) > 0),
    CHECK (status IN (10, 20, 30, 40, 50, 60, 70, 80)),
    CHECK (priority IN ('URGENT', 'NORMAL')),
    CHECK (prerequisite_count >= 0),
    CHECK (completed_prerequisite_count >= 0),
    CHECK (completed_prerequisite_count <= prerequisite_count),
    CHECK (attempt >= 0),
    CHECK (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at)
);

CREATE TABLE node_results (
    task_node_id bigint PRIMARY KEY REFERENCES task_nodes(id) ON DELETE CASCADE,
    result jsonb,
    artifact_path text,
    artifact_count integer,
    progress jsonb NOT NULL DEFAULT '{}'::jsonb,
    effective_params jsonb,
    result_version integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (artifact_path IS NULL OR artifact_path LIKE '/%'),
    CHECK (artifact_count IS NULL OR artifact_count >= 0),
    CHECK (result_version > 0)
);

CREATE TABLE node_work_items (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_node_id bigint NOT NULL REFERENCES task_nodes(id) ON DELETE CASCADE,
    item_key text NOT NULL,
    ordinal integer NOT NULL,
    status smallint NOT NULL DEFAULT 10,
    reason text NOT NULL DEFAULT '子任务等待处理',
    result jsonb,
    attempt integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (task_node_id, item_key),
    UNIQUE (task_node_id, ordinal),
    CHECK (length(btrim(item_key)) > 0),
    CHECK (ordinal >= 0),
    CHECK (status IN (10, 20, 30, 40, 50, 60, 70, 80)),
    CHECK (attempt >= 0)
);

CREATE TABLE outbox_events (
    event_id uuid PRIMARY KEY,
    aggregate_type text NOT NULL,
    aggregate_id text NOT NULL,
    event_type text NOT NULL,
    payload jsonb NOT NULL,
    available_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    publish_attempts integer NOT NULL DEFAULT 0,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (length(btrim(aggregate_type)) > 0),
    CHECK (length(btrim(aggregate_id)) > 0),
    CHECK (length(btrim(event_type)) > 0),
    CHECK (publish_attempts >= 0)
);

CREATE TABLE operator_instances (
    instance_id text PRIMARY KEY,
    operator_code text NOT NULL,
    capabilities jsonb NOT NULL DEFAULT '[]'::jsonb,
    service_url text NOT NULL,
    model_version text,
    api_version text,
    declared_capacity integer NOT NULL,
    labels jsonb NOT NULL DEFAULT '{}'::jsonb,
    desired_state text NOT NULL DEFAULT 'ONLINE',
    last_registered_at timestamptz NOT NULL DEFAULT now(),
    last_heartbeat_at timestamptz,
    unregistered_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (length(btrim(instance_id)) > 0),
    CHECK (operator_code IN (
        'asr_offline', 'asr_online', 'ppt_slice', 'ocr',
        'text_analysis', 'vbas', 'facerec', 'screen_det'
    )),
    CHECK (service_url ~ '^https?://'),
    CHECK (declared_capacity > 0),
    CHECK (desired_state IN ('ONLINE', 'DRAINING', 'OFFLINE'))
);

CREATE TABLE operator_instance_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    instance_id text NOT NULL,
    event_type text NOT NULL,
    event_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    CHECK (length(btrim(instance_id)) > 0),
    CHECK (length(btrim(event_type)) > 0)
);

CREATE TABLE visual_fallback_values (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    course_task_type_id bigint NOT NULL
        REFERENCES course_task_types(id) ON DELETE CASCADE,
    metric_code text NOT NULL,
    value numeric(8, 6) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (course_task_type_id, metric_code),
    CHECK (metric_code IN ('FRONT_OCCUPANCY_RATIO', 'BACK_OCCUPANCY_RATIO')),
    CHECK (value >= 0 AND value <= 1)
);

COMMIT;
