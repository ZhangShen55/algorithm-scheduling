BEGIN;

CREATE TABLE task_node_dependencies (
    node_id bigint NOT NULL REFERENCES task_nodes(id) ON DELETE CASCADE,
    prerequisite_node_id bigint NOT NULL REFERENCES task_nodes(id) ON DELETE CASCADE,
    PRIMARY KEY (node_id, prerequisite_node_id),
    CHECK (node_id <> prerequisite_node_id)
);

CREATE INDEX idx_task_node_dependencies_prerequisite
    ON task_node_dependencies (prerequisite_node_id, node_id);

COMMIT;
