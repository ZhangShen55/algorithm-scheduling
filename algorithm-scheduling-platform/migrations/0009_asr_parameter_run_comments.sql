BEGIN;

COMMENT ON TABLE task_type_runs IS
    '课程任务类型的参数执行版本表，当前用于离线 ASR 的参数指纹、状态和历史结果';
COMMENT ON COLUMN task_type_runs.run_id IS
    '参数执行版本唯一标识，随 Outbox 和 DAG 节点传播';
COMMENT ON COLUMN task_type_runs.course_task_type_id IS
    '所属课程任务类型内部主键';
COMMENT ON COLUMN task_type_runs.params_fingerprint IS
    '完整 effective_params 的稳定 SHA-256 指纹';
COMMENT ON COLUMN task_type_runs.effective_params IS
    '该执行版本实际传给 ASR 算子的完整参数快照';
COMMENT ON COLUMN task_type_runs.status IS
    '执行版本整数状态：10 等待、20 前置等待、30 等待算子、40 已排队、50 处理中、60 完成、70 失败、80 取消';
COMMENT ON COLUMN task_type_runs.reason IS
    '执行版本当前状态的中文原因说明';
COMMENT ON COLUMN task_type_runs.result IS
    '该参数版本对应的完整 ASR 结构化结果';
COMMENT ON COLUMN task_type_runs.created_at IS
    '参数执行版本创建时间，带时区';
COMMENT ON COLUMN task_type_runs.started_at IS
    '参数执行版本开始处理时间，带时区';
COMMENT ON COLUMN task_type_runs.finished_at IS
    '参数执行版本进入终态时间，带时区';

COMMENT ON COLUMN task_nodes.run_id IS
    '节点所属参数执行版本；非参数版本任务使用全零 UUID';

COMMIT;
