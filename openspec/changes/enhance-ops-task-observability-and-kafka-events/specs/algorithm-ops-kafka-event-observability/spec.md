## ADDED Requirements

### Requirement: Control Service 提供课程任务 Outbox 事件列表
Control Service SHALL 提供 `GET /ops/kafka/events`，从 PostgreSQL `outbox_events` 分页读取课程任务发布事件，并支持精确 `task_id`、`task_id_like`、`event_type`、`publish_status`、创建时间范围、分页和排序。接口 SHALL 返回筛选后的总数，不得要求浏览器直接连接 Kafka 或 orchestrator-service。

#### Scenario: 查看最近发布事件
- **WHEN** 运维人员打开 Kafka 发布记录且未设置筛选
- **THEN** 页面按创建时间倒序显示最近 Outbox 事件及分页总数

#### Scenario: 按课程任务追踪事件
- **WHEN** 运维人员筛选 `task_id=test_all_0903_15`
- **THEN** 列表只显示该课程四类任务对应的 Outbox 发布记录

### Requirement: Outbox 发布状态按持久化事实派生
事件摘要 SHALL 返回 `event_id`、`aggregate_type`、`aggregate_id`、`event_type`、任务标识、任务类型、`created_at`、`available_at`、`claimed_at`、`published_at`、`publish_attempts`、受限 `last_error` 和派生 `publish_status`，但 MUST 不返回 `claim_token`。`PUBLISHED` 仅表示 Producer 收到 Broker 确认，不得表示消费者已经处理。

#### Scenario: Broker 已确认事件
- **WHEN** Outbox 事件具有非空 `published_at`
- **THEN** 页面显示“Broker 已确认”、确认时间和尝试次数，不显示“消费完成”

#### Scenario: 发布失败等待重试
- **WHEN** 事件尚未发布且 `last_error` 非空
- **THEN** 页面显示“失败待重试”和错误摘要，并且不提供重新发布按钮

#### Scenario: 隐藏内部领取令牌
- **WHEN** 事件正在被 Publisher 领取
- **THEN** 接口可以返回“发布中”和领取时间，但响应中不存在 `claim_token`

### Requirement: Kafka 事件 payload 按需读取并默认收起
Control Service SHALL 提供 `GET /ops/kafka/events/{event_id}` 返回单条事件 envelope 和 payload，并 SHALL 提供 `GET /ops/course-jobs/{task_id}/events` 返回课程事件时间线。前端 MUST 默认收起 payload，只有用户展开时才请求详情，并以格式化 JSON 显示；接口和页面不得包含媒体二进制、Base64 或凭据。

#### Scenario: 展开课程任务发布内容
- **WHEN** 运维人员展开 `COURSE_TASK_REQUESTED` 事件
- **THEN** 页面显示 `event_id`、`aggregate_type`、`aggregate_id`、`event_type` 以及包含 `task_id/task_type/priority/submission_id` 等字段的格式化 payload

#### Scenario: 事件不存在
- **WHEN** 请求不存在的 `event_id`
- **THEN** Control Service 返回 `404`，页面保留事件列表并显示详情不可用

### Requirement: 第一版明确 Outbox 观测边界
页面 SHALL 将事件区命名为课程任务 Outbox 或任务发布记录，并 SHALL 明确该列表不覆盖视觉命令、视觉事件及其他绕过该 Outbox 的 Kafka 消息。由于当前未持久化 Producer 元数据，页面 MUST 不显示或猜测 `topic`、`partition`、`offset`。

#### Scenario: 查看发布元数据边界
- **WHEN** 一个事件已收到 Broker 确认但数据库没有 Topic、Partition 或 Offset
- **THEN** 页面只显示可证实的 Outbox 和确认时间，不生成虚假的 Kafka 位置信息

### Requirement: Kafka 事件查询失败不影响其他观测页面
Outbox 事件 Repository 失败时接口 SHALL 返回可诊断的 `503`；前端 SHALL 将发布记录标记为不可用，但运行总览、实例、任务详情、网关和 GPU 数据仍可继续展示各自成功读取的内容。

#### Scenario: Outbox 数据库查询失败
- **WHEN** Kafka 事件列表查询发生数据库错误
- **THEN** 发布记录区域显示读取失败，其他数据源成功得到的观测结果不被清空
