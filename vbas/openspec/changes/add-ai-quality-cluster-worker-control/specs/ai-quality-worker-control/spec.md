## ADDED Requirements

### Requirement: ai_quality 必须用 Redis 保存 Worker 集群期望状态
ai_quality MUST 将 Worker 集群是否消费 Kafka 的期望状态保存到 Redis，由 API 写入，由 Worker 读取。

#### Scenario: resume 控制
- **WHEN** 调用 `POST /api/worker-control/resume` 且鉴权通过
- **THEN** 系统必须将 Redis 中的 `desired_state` 设置为 `RUNNING`，递增控制版本，并返回最新控制状态

#### Scenario: pause 控制
- **WHEN** 调用 `POST /api/worker-control/pause` 且鉴权通过
- **THEN** 系统必须将 Redis 中的 `desired_state` 设置为 `PAUSED`，递增控制版本，并返回最新控制状态

#### Scenario: drain 控制
- **WHEN** 调用 `POST /api/worker-control/drain` 且鉴权通过
- **THEN** 系统必须将 Redis 中的 `desired_state` 设置为 `DRAINING`，递增控制版本，并返回最新控制状态

#### Scenario: 查询控制状态
- **WHEN** 调用 `GET /api/worker-control/state`
- **THEN** 系统必须返回当前 `desired_state`、version、updated_at、updated_by 和 reason

### Requirement: ai_quality Worker 必须注册自身并上报心跳
ai_quality Worker MUST 在独立进程启动后注册自身运行状态，并周期性刷新 Redis 心跳。

#### Scenario: Worker 启动注册
- **WHEN** 执行 `python -m ai_quality.app worker`
- **THEN** Worker 必须生成或读取 `worker_id`，并向 Redis 写入自身状态和 TTL

#### Scenario: Worker 周期心跳
- **WHEN** Worker 进程存活
- **THEN** Worker 必须按配置周期更新 actual_state、desired_state、topic、consumer_group、assigned_partitions、current_task_id、current_offset、processed_count、failed_count、last_error 和 last_heartbeat_at

#### Scenario: Worker 心跳过期
- **WHEN** Redis 中某个 Worker 状态 key 已过期
- **THEN** API 查询 Worker 列表时必须把该 Worker 视为不可用，并清理注册表集合中的残留 ID

### Requirement: ai_quality API 必须提供 Worker 查询接口
ai_quality API MUST 允许查询 Worker 注册表和单个 Worker 运行状态。

#### Scenario: 查询所有 Worker
- **WHEN** 调用 `GET /api/workers`
- **THEN** 系统必须返回所有心跳未过期 Worker 的状态列表

#### Scenario: 查询单个 Worker
- **WHEN** 调用 `GET /api/workers/{worker_id}` 且 Worker 存在
- **THEN** 系统必须返回该 Worker 的完整运行状态

#### Scenario: 查询不存在的 Worker
- **WHEN** 调用 `GET /api/workers/{worker_id}` 且 Worker 不存在或心跳已过期
- **THEN** 系统必须返回 404

### Requirement: ai_quality Worker 必须按 desired_state 控制 Kafka 消费
ai_quality Worker MUST 周期读取 Redis 中的 `desired_state`，并根据状态决定是否 poll Kafka。

#### Scenario: RUNNING 状态
- **WHEN** `desired_state=RUNNING`
- **THEN** Worker 必须允许 poll Kafka 并处理课堂视觉任务

#### Scenario: PAUSED 状态
- **WHEN** `desired_state=PAUSED` 且 Worker 当前没有任务
- **THEN** Worker 必须不 poll Kafka，并继续上报 actual_state=`PAUSED`

#### Scenario: DRAINING 状态且有当前任务
- **WHEN** `desired_state=DRAINING` 且 Worker 正在处理任务
- **THEN** Worker 必须继续完成当前任务、写入最终状态、提交 offset，然后不再 poll 新消息

#### Scenario: DRAINING 状态且无当前任务
- **WHEN** `desired_state=DRAINING` 且 Worker 没有当前任务
- **THEN** Worker 必须不 poll Kafka，并上报 actual_state=`PAUSED`

#### Scenario: STOPPED 状态
- **WHEN** `desired_state=STOPPED`
- **THEN** Worker 必须停止消费循环或保持空转心跳，具体行为由配置决定，但不得继续 poll Kafka

### Requirement: ai_quality Worker 控制必须保留长任务 offset 语义
ai_quality Worker MUST 在 pause、drain 或 stop 控制下保持既有 offset 提交口径。

#### Scenario: 当前任务成功
- **WHEN** Worker 在 DRAINING 期间完成当前任务且写入 `lesson_ai_workflow` 成功终态
- **THEN** Worker 必须提交对应 Kafka offset

#### Scenario: 当前任务最终失败
- **WHEN** Worker 在 DRAINING 期间达到最终失败且写入 `lesson_ai_workflow` 失败终态
- **THEN** Worker 必须提交对应 Kafka offset

#### Scenario: 当前任务未完成
- **WHEN** Worker 收到 pause 或 drain 控制但当前任务未完成
- **THEN** Worker 不得提前提交 Kafka offset

### Requirement: ai_quality Worker 数量必须独立于 API worker 数量
ai_quality MUST 将 API 进程数量和 Kafka Worker 进程数量作为独立部署参数。

#### Scenario: API 使用多个实例
- **WHEN** 部署多个 ai_quality-api 实例
- **THEN** Kafka Worker 数量不得由 API 实例数或 Uvicorn worker 数自动决定

#### Scenario: Worker 多实例消费
- **WHEN** 部署多个 ai_quality-worker 进程且它们使用同一个 Kafka consumer group
- **THEN** Kafka 必须按 topic partition 分配消费，实际并发上限受 partition 数限制

#### Scenario: partition 数不足
- **WHEN** Worker 数量大于 `classroom_cv_task` partition 数
- **THEN** 文档和状态接口必须说明部分 Worker 可能空闲，这不是故障
