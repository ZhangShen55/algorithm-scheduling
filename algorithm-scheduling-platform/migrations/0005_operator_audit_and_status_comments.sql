BEGIN;

CREATE INDEX idx_operator_instance_events_instance_time
    ON operator_instance_events (instance_id, occurred_at DESC, id DESC);

COMMENT ON COLUMN course_task_types.status IS '任务类型整数状态：10 等待、20 前置等待、30 等待算子、40 已排队、50 处理中、60 完成、70 失败、80 取消';
COMMENT ON COLUMN task_nodes.status IS '节点整数状态：10 就绪、20 前置等待、30 等待算子、40 已排队、50 处理中、60 完成、70 失败、80 取消';
COMMENT ON COLUMN node_work_items.status IS '子项整数状态：10 等待、20 前置等待、30 等待算子、40 已排队、50 处理中、60 完成、70 失败、80 取消';

COMMIT;
