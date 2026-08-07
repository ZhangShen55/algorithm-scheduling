# 算法调度平台 PostgreSQL 逻辑数据模型

## 1. 设计边界

PostgreSQL 是课程任务、节点状态、结构化结果、Outbox 和运维审计事实的唯一持久化来源。
Redis 只保存带 TTL 的算子心跳、实时容量和租约，不作为任务状态或结果来源。Kafka 只传递
任务标识、优先级、本地文件路径和编排元数据，不保存媒体二进制与业务最终状态。

`task_id` 是 A 服务提供的全局课程标识。一个课程可以分多次追加任务类型，业务幂等键为
`(task_id, task_type)`。数据库内部使用 bigint 主键连接各表，北向协议始终使用原始
`task_id`。

所有时间字段使用带时区时间 `timestamptz`，统一保存 UTC；所有可变业务响应使用 JSONB，
但可调度字段、状态、优先级、时间和幂等键必须使用普通列，避免把调度查询隐藏在 JSONB 中。

## 2. 实体关系

```mermaid
erDiagram
    course_jobs ||--o{ course_task_types : contains
    course_task_types ||--o{ task_nodes : expands
    task_nodes ||--o| node_results : produces
    task_nodes ||--o{ node_work_items : contains
    course_task_types ||--o{ visual_fallback_values : owns
    course_task_types ||--o{ outbox_events : emits
    operator_instances ||--o{ operator_instance_events : audits

    course_jobs {
        bigint id PK
        text task_id UK
        jsonb input_snapshot
        timestamptz created_at
        timestamptz updated_at
    }
    course_task_types {
        bigint id PK
        text task_id FK
        text task_type
        smallint status
        text priority
        text reason
        jsonb request_payload
        jsonb effective_params
    }
    task_nodes {
        bigint id PK
        bigint course_task_type_id FK
        text node_code
        smallint status
        text priority
        text reason
        text required_capability
        integer attempt
    }
    node_results {
        bigint task_node_id PK_FK
        jsonb result
        text artifact_path
        integer artifact_count
        jsonb progress
        jsonb effective_params
    }
    node_work_items {
        bigint id PK
        bigint task_node_id FK
        text item_key
        smallint status
        jsonb result
    }
    outbox_events {
        uuid event_id PK
        text aggregate_type
        text aggregate_id
        text event_type
        jsonb payload
        timestamptz published_at
    }
    operator_instances {
        text instance_id PK
        text operator_code
        jsonb capabilities
        text service_url
        integer declared_capacity
        jsonb labels
    }
    operator_instance_events {
        bigint id PK
        text instance_id
        text event_type
        jsonb event_payload
        timestamptz occurred_at
    }
    visual_fallback_values {
        bigint id PK
        bigint course_task_type_id FK
        text metric_code
        numeric value
    }
```

## 3. 表职责与关键字段

### 3.1 `course_jobs`

一行表示一节课程，不表示一次 HTTP 提交。

- `task_id`：A 服务提供的唯一课程标识，全局唯一且不可修改。
- `input_snapshot`：保存最近一次已接纳请求中的公共扩展字段；业务必需路径仍复制到对应
  `course_task_types.request_payload`，使每条管道可独立执行。
- `created_at`、`updated_at`：用于课程查询与运维排序。

同一 `task_id` 的并发首次提交通过唯一约束收敛为一行。

### 3.2 `course_task_types`

一行表示一个课程的一条业务管道，`task_type` 仅允许 `PPT`、`ASR`、
`TEACHER_BEHAVIOR`、`STUDENT_BEHAVIOR`。

- 唯一键为 `(task_id, task_type)`；`task_id` 直接外键关联课程，完整落实北向幂等键。
- `status` 使用平台整数状态 10-80；未请求的 0 状态由查询层补齐，不写占位行。
- `priority` 仅允许 `URGENT`、`NORMAL`，默认 `NORMAL`。
- `reason` 保存面向 A 和运维人员的中文状态说明。
- `request_payload` 仅保存该任务类型实际需要的输入，如视频 URL、区域、多媒体参数。
- `effective_params` 保存实际生效配置；ASR 首次执行后不得被重复提交覆盖。
- `requested_at`、`started_at`、`finished_at`、`updated_at` 支撑状态查询和耗时统计。

### 3.3 `task_nodes`

一行表示 DAG 中可领取、可执行、可观测的节点。节点与任务类型是一对多关系。

- `(course_task_type_id, node_code)` 唯一，保证 Kafka 重复消费不会重复建节点。
- `status` 使用 10、20、30、40、50、60、70、80；节点不保存 0。
- `priority` 从任务类型继承，领取时按 `URGENT` 优先、同优先级 `ready_at/id` FIFO。
- `required_capability` 表示需要租约的能力；不需要算子的纯平台节点可以为空。
- `prerequisite_count` 与 `completed_prerequisite_count` 支持前置节点释放。
- `attempt`、`claimed_by`、`claim_token`、`claimed_at` 用于并发领取与进程恢复。
- `reason` 始终为中文；等待算子时明确指出所缺能力。

### 3.4 `node_results`

与节点一对零或一，隔离调度热行和可能很大的算法结果。

- `result`：OCR、关键词、完整 ASR v1.1.8、完整课程脑图、行为区间、人数统计等结构化
  JSONB 数据。
- `artifact_path`、`artifact_count`：只表达确实存在于共享文件系统的文件，也就是响应中的
  `path/count`；PPT 切片和精选视觉证据可以使用，OCR 等结构化结果不得伪装为文件。
- `progress`：保存 `completed_count`、`total_count` 等可查询进度。
- `effective_params`：保存节点实际调用参数，ASR 必须写入。
- `result_version`、`created_at`、`updated_at`：支持兼容演进与审计。

### 3.5 `node_work_items`

保存动态子项，例如每张 PPT 切片的 OCR 与关键词工作。

- `(task_node_id, item_key)` 唯一，其中 `item_key` 使用稳定的 `ppt_image_id`。
- `status`、`reason`、`result` 允许单项完成可见和部分进度聚合。
- `ordinal` 保留切片顺序，`attempt` 支撑后续显式重试策略。

### 3.6 `outbox_events`

与课程任务写入使用同一数据库事务，消除“数据库已提交但 Kafka 未发送”的窗口。

- `event_id` 是 Kafka 消息幂等键。
- `aggregate_type`、`aggregate_id`、`event_type` 用于路由和审计。
- `payload` 只允许元数据，不允许视频、音频、Base64 图片或图片二进制。
- `available_at`、`published_at`、`publish_attempts`、`last_error` 支撑扫描、确认和可观测性。
- Publisher 使用行级跳过锁并发领取，只有收到 Kafka 发布确认后设置 `published_at`。

### 3.7 `operator_instances`

保存实例的声明信息和最后已知审计快照，不承担实时容量判断。

- `instance_id` 对应一个独立端点/进程/端口/GPU。
- `operator_code` 使用 `asr_offline`、`asr_online`、`ppt_slice`、`ocr`、
  `text_analysis`、`vbas`、`facerec`、`screen_det` 等平台代码。
- `capabilities`、`service_url`、模型/API 版本、`declared_capacity`、`labels` 保存注册声明。
- `desired_state` 保存 `ONLINE`、`DRAINING`、`OFFLINE` 运维意图。
- `last_registered_at`、`last_heartbeat_at`、`unregistered_at` 仅用于审计显示。

是否能够接新请求必须同时检查 PostgreSQL 的运维意图与 Redis 中未过期心跳、模型就绪状态、
容量租约，不能只读取本表。

### 3.8 `operator_instance_events`

追加保存注册、重新注册、排空、注销和租约异常等运维事件。该表不使用外键强制实例仍存在，
从而保留实例删除或重建前的审计轨迹。

### 3.9 `visual_fallback_values`

仅在学生任务缺少前排或后排区域、而 A 仍要求展示对应比例时保存一次性兜底值。

- `(course_task_type_id, metric_code)` 唯一，首次在配置最小值与最大值之间生成并持久化。
- `metric_code` 只允许 `FRONT_OCCUPANCY_RATIO`、`BACK_OCCUPANCY_RATIO`。
- `value` 查询时稳定复用，不得每次随机生成。
- `front_region_provided`、`back_region_provided` 保存到学生任务的结构化结果中，明确区域是否
  由 A 提供；不增加 `is_estimated` 或 `source` 字段。

## 4. 状态与结果所有权

`control-service` 创建课程、任务类型与 Outbox，并提供一致性查询。`orchestrator-service`
创建和更新普通 DAG 节点。`vision-orchestrator-service` 更新被授予的视觉节点进度与结果，
但仍写入同一个 PostgreSQL。算子不得直接修改平台数据库。

结构化数据统一进入 `node_results.result` 或 `node_work_items.result`；长期文件进入
`/data/result/{task_id}` 后才写 `artifact_path`。临时文件 `/data/course/{task_id}` 不得作为
终态长期结果返回。

## 5. 事务与删除规则

- 创建/追加任务类型、写 Outbox 必须同事务提交。
- 节点状态与其结果元数据必须同事务提交，避免出现完成状态却查不到结果。
- 课程任务采用保留历史的逻辑生命周期；第一版不级联物理删除业务结果。
- 只有所有已请求任务进入终态、长期文件确认成功后，才删除 `/data/course/{task_id}`；
  `/data/result/{task_id}` 不随临时目录清理。
- Redis 数据过期只影响路由资格，不得改变 PostgreSQL 中已经完成的业务状态。
