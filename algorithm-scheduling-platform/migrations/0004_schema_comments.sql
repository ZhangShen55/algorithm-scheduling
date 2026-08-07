BEGIN;

COMMENT ON TABLE course_jobs IS '课程主任务表，一行对应 A 服务提供的一个全局唯一课程 task_id';
COMMENT ON COLUMN course_jobs.id IS '平台内部自增主键';
COMMENT ON COLUMN course_jobs.task_id IS 'A 服务提供的全局唯一课程任务标识，也是北向查询标识';
COMMENT ON COLUMN course_jobs.input_snapshot IS '最近一次已接纳请求中的课程级公共输入快照，不替代各任务类型自己的请求参数';
COMMENT ON COLUMN course_jobs.created_at IS '课程主任务首次创建时间，带时区';
COMMENT ON COLUMN course_jobs.updated_at IS '课程主任务最近更新时间，带时区';

COMMENT ON TABLE course_task_types IS '课程任务类型表，一行对应某课程的一条 PPT、ASR、教师行为或学生行为处理管道';
COMMENT ON COLUMN course_task_types.id IS '课程任务类型内部自增主键';
COMMENT ON COLUMN course_task_types.task_id IS '关联 course_jobs.task_id 的课程任务标识';
COMMENT ON COLUMN course_task_types.task_type IS '任务类型枚举：PPT、ASR、TEACHER_BEHAVIOR 或 STUDENT_BEHAVIOR';
COMMENT ON COLUMN course_task_types.status IS '任务类型整数状态：10 等待、20 前置等待、30 等待算子、40 运行、50 部分完成、60 完成、70 失败、80 取消';
COMMENT ON COLUMN course_task_types.priority IS '非抢占优先级：URGENT 或 NORMAL';
COMMENT ON COLUMN course_task_types.reason IS '面向 A 服务和运维人员的中文状态原因';
COMMENT ON COLUMN course_task_types.request_payload IS '该任务类型执行所需的原始请求参数快照';
COMMENT ON COLUMN course_task_types.effective_params IS '实际生效的算法参数，例如离线 ASR 的最终参数';
COMMENT ON COLUMN course_task_types.requested_at IS '该任务类型首次请求时间，带时区';
COMMENT ON COLUMN course_task_types.started_at IS '该任务类型首次进入运行状态的时间，带时区';
COMMENT ON COLUMN course_task_types.finished_at IS '该任务类型进入终态的时间，带时区';
COMMENT ON COLUMN course_task_types.updated_at IS '该任务类型最近更新时间，带时区';

COMMENT ON TABLE task_nodes IS '任务 DAG 节点表，保存可领取、可执行和可查询的节点运行状态';
COMMENT ON COLUMN task_nodes.id IS '任务节点内部自增主键';
COMMENT ON COLUMN task_nodes.course_task_type_id IS '所属课程任务类型的内部主键';
COMMENT ON COLUMN task_nodes.node_code IS '节点稳定代码，例如 PPT_SLICE、PPT_OCR 或 ASR_TRANSCRIPTION';
COMMENT ON COLUMN task_nodes.status IS '节点整数状态：10 就绪、20 前置等待、30 等待算子、40 运行、50 部分完成、60 完成、70 失败、80 取消';
COMMENT ON COLUMN task_nodes.priority IS '节点继承的非抢占优先级：URGENT 或 NORMAL';
COMMENT ON COLUMN task_nodes.reason IS '当前节点状态的中文原因说明';
COMMENT ON COLUMN task_nodes.required_capability IS '执行节点所需的算子能力代码，纯平台节点可为空';
COMMENT ON COLUMN task_nodes.prerequisite_count IS '节点前置依赖总数';
COMMENT ON COLUMN task_nodes.completed_prerequisite_count IS '已完成的前置依赖数量';
COMMENT ON COLUMN task_nodes.attempt IS '节点已开始执行的尝试次数，第一版不等同于自动重试策略';
COMMENT ON COLUMN task_nodes.ready_at IS '节点进入可领取状态的时间，带时区';
COMMENT ON COLUMN task_nodes.claimed_by IS '领取该节点的 orchestrator 执行器实例标识';
COMMENT ON COLUMN task_nodes.claim_token IS '本次节点领取的唯一令牌，用于防止过期执行器回写';
COMMENT ON COLUMN task_nodes.claimed_at IS '节点最近一次被领取的时间，带时区';
COMMENT ON COLUMN task_nodes.started_at IS '节点实际开始执行时间，带时区';
COMMENT ON COLUMN task_nodes.finished_at IS '节点进入终态的时间，带时区';
COMMENT ON COLUMN task_nodes.created_at IS '节点创建时间，带时区';
COMMENT ON COLUMN task_nodes.updated_at IS '节点最近更新时间，带时区';

COMMENT ON TABLE node_results IS '节点结果表，与任务节点一对零或一，保存结构化结果、长期文件元数据和进度';
COMMENT ON COLUMN node_results.task_node_id IS '关联 task_nodes.id，同时作为本表主键';
COMMENT ON COLUMN node_results.result IS 'OCR、关键词、ASR、课程脑图、行为区间或人数统计等结构化 JSON 结果';
COMMENT ON COLUMN node_results.artifact_path IS '已长期保留文件的绝对路径，仅用于确实落盘的结果文件';
COMMENT ON COLUMN node_results.artifact_count IS 'artifact_path 所指长期结果文件的数量';
COMMENT ON COLUMN node_results.progress IS '节点可查询进度，例如已完成数量和总数量';
COMMENT ON COLUMN node_results.effective_params IS '节点调用算子时实际生效的参数';
COMMENT ON COLUMN node_results.result_version IS '节点结果结构版本号';
COMMENT ON COLUMN node_results.created_at IS '节点结果首次创建时间，带时区';
COMMENT ON COLUMN node_results.updated_at IS '节点结果最近更新时间，带时区';

COMMENT ON TABLE node_work_items IS '节点动态子项表，例如按 ppt_image_id 保存单张切片的 OCR 或关键词处理状态';
COMMENT ON COLUMN node_work_items.id IS '节点子项内部自增主键';
COMMENT ON COLUMN node_work_items.task_node_id IS '所属任务节点的内部主键';
COMMENT ON COLUMN node_work_items.item_key IS '节点内稳定唯一的子项标识，例如 ppt_image_id';
COMMENT ON COLUMN node_work_items.ordinal IS '子项在原始输入中的零基顺序';
COMMENT ON COLUMN node_work_items.status IS '子项整数状态：10 等待、20 前置等待、30 等待算子、40 运行、50 部分完成、60 完成、70 失败、80 取消';
COMMENT ON COLUMN node_work_items.reason IS '当前子项状态的中文原因说明';
COMMENT ON COLUMN node_work_items.result IS '单个子项的结构化 JSON 结果';
COMMENT ON COLUMN node_work_items.attempt IS '子项已开始执行的尝试次数';
COMMENT ON COLUMN node_work_items.created_at IS '子项创建时间，带时区';
COMMENT ON COLUMN node_work_items.updated_at IS '子项最近更新时间，带时区';

COMMENT ON TABLE outbox_events IS '事务 Outbox 事件表，与任务事实同事务写入，由 orchestrator-service 的 Publisher 发布到 Kafka';
COMMENT ON COLUMN outbox_events.event_id IS '事件全局唯一标识，同时作为消息幂等键';
COMMENT ON COLUMN outbox_events.aggregate_type IS '事件所属聚合类型，例如 COURSE_JOB';
COMMENT ON COLUMN outbox_events.aggregate_id IS '事件所属聚合标识，课程事件通常使用 task_id';
COMMENT ON COLUMN outbox_events.event_type IS '事件类型，用于 Publisher 选择消息契约和主题';
COMMENT ON COLUMN outbox_events.payload IS '只包含标识、路径和编排元数据的 JSON 消息体，不保存媒体二进制';
COMMENT ON COLUMN outbox_events.available_at IS '事件允许首次或再次发布的时间，带时区';
COMMENT ON COLUMN outbox_events.published_at IS '收到 Kafka 发布确认后的时间，未发布时为空';
COMMENT ON COLUMN outbox_events.publish_attempts IS 'Publisher 已尝试发布的次数';
COMMENT ON COLUMN outbox_events.last_error IS '最近一次发布失败的错误摘要';
COMMENT ON COLUMN outbox_events.created_at IS 'Outbox 事件创建时间，带时区';
COMMENT ON COLUMN outbox_events.claim_token IS 'Publisher 本次并发领取事件的唯一令牌';
COMMENT ON COLUMN outbox_events.claimed_at IS 'Publisher 最近一次领取事件的时间，带时区';

COMMENT ON TABLE operator_instances IS '算子实例持久化审计快照表，Redis 仍是实时心跳和容量租约的权威来源';
COMMENT ON COLUMN operator_instances.instance_id IS '可独立路由的算子端点唯一标识，对应进程、端口和 GPU 组合';
COMMENT ON COLUMN operator_instances.operator_code IS '算子代码，例如 asr_offline、ppt_slice、ocr、text_analysis 或 vbas';
COMMENT ON COLUMN operator_instances.capabilities IS '实例主动声明的能力代码 JSON 数组';
COMMENT ON COLUMN operator_instances.service_url IS '调度平台调用该算子实例的 HTTP 基础地址';
COMMENT ON COLUMN operator_instances.model_version IS '实例加载的算法模型版本';
COMMENT ON COLUMN operator_instances.api_version IS '实例业务接口契约版本';
COMMENT ON COLUMN operator_instances.declared_capacity IS '实例声明的最大并发容量，必须大于零';
COMMENT ON COLUMN operator_instances.labels IS 'GPU、设备、区域等用于路由和运维显示的 JSON 标签';
COMMENT ON COLUMN operator_instances.desired_state IS '平台运维意图：ONLINE、DRAINING 或 OFFLINE';
COMMENT ON COLUMN operator_instances.last_registered_at IS '实例最近一次成功注册时间，带时区';
COMMENT ON COLUMN operator_instances.last_heartbeat_at IS '持久化的最近心跳摘要时间，实时有效性仍由 Redis TTL 决定';
COMMENT ON COLUMN operator_instances.unregistered_at IS '实例主动注销时间，未注销时为空';
COMMENT ON COLUMN operator_instances.created_at IS '实例审计记录首次创建时间，带时区';
COMMENT ON COLUMN operator_instances.updated_at IS '实例审计快照最近更新时间，带时区';

COMMENT ON TABLE operator_instance_events IS '算子实例运维事件表，追加保存注册、重注册、排空、注销和租约异常等事实';
COMMENT ON COLUMN operator_instance_events.id IS '算子实例事件内部自增主键';
COMMENT ON COLUMN operator_instance_events.instance_id IS '事件关联的算子实例唯一标识';
COMMENT ON COLUMN operator_instance_events.event_type IS '运维事件类型';
COMMENT ON COLUMN operator_instance_events.event_payload IS '事件上下文和变化内容的 JSON 数据';
COMMENT ON COLUMN operator_instance_events.occurred_at IS '事件发生时间，带时区';

COMMENT ON TABLE visual_fallback_values IS '学生视觉任务缺少前排或后排区域时生成并稳定复用的展示兜底值';
COMMENT ON COLUMN visual_fallback_values.id IS '视觉兜底值内部自增主键';
COMMENT ON COLUMN visual_fallback_values.course_task_type_id IS '所属学生行为课程任务类型的内部主键';
COMMENT ON COLUMN visual_fallback_values.metric_code IS '兜底指标代码：FRONT_OCCUPANCY_RATIO 或 BACK_OCCUPANCY_RATIO';
COMMENT ON COLUMN visual_fallback_values.value IS '首次在配置范围内生成并持久化的比例值，取值范围为零到一';
COMMENT ON COLUMN visual_fallback_values.created_at IS '兜底值首次生成时间，带时区';

COMMENT ON TABLE task_node_dependencies IS '任务节点直接依赖关系表，保存 DAG 中节点与前置节点的多对多关系';
COMMENT ON COLUMN task_node_dependencies.node_id IS '依赖其他节点的当前任务节点内部主键';
COMMENT ON COLUMN task_node_dependencies.prerequisite_node_id IS '当前节点必须等待完成的前置任务节点内部主键';

COMMIT;
