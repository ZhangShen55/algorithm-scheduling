## ADDED Requirements

### Requirement: ai_quality 必须通过 TIAS HTTP 接口完成推理调度
MUST：以下场景为本要求的强制验收条件。
ai_quality 必须把 TIAS 视为独立 HTTP 服务，课堂质量推理链路不得直接导入 TIAS 的实现模块。

#### Scenario: 远程推理模式启用
- **WHEN** `TiasInferenceMode` 配置为 `remote`
- **THEN** ai_quality 必须调用 TIAS HTTP 推理接口完成学生帧和教师帧分析

#### Scenario: 不存在本地 TIAS 推理模块导入
- **WHEN** ai_quality 运行在远程推理模式
- **THEN** 它不得导入 `app.services.student_behavior_service`、`app.services.teacher_behavior_service` 或迁移后的 `tias.services.*` 作为推理实现

### Requirement: ai_quality 必须把帧切分为可配置小批次
MUST：以下场景为本要求的强制验收条件。
ai_quality 必须在调度给 TIAS 前，把抽取出的学生帧和教师帧切分为可配置的小批次。

#### Scenario: 帧被切分为多个批次
- **WHEN** Kafka 任务产生的帧数超过 `TiasBatchSize`
- **THEN** ai_quality 必须创建多个批次，并保留每帧的 `frame_index` 和 `timestamp_seconds`

#### Scenario: 批大小可配置
- **WHEN** 配置中的 `TiasBatchSize` 被调整
- **THEN** 后续任务必须使用新的批大小进行调度

### Requirement: ai_quality 必须输出简洁关键流程日志
MUST：以下场景为本要求的强制验收条件。
ai_quality 必须用简洁中文记录关键流程日志，并使用稳定英文 key 便于检索。

#### Scenario: Kafka 任务被消费
- **WHEN** ai_quality 消费到 Kafka 任务
- **THEN** 它必须记录 `task_id`、`course_id`、`student_count`、Kafka topic、partition 和 offset

#### Scenario: TIAS 实例被选中
- **WHEN** ai_quality 把批次调度给 TIAS
- **THEN** 它必须记录 `task_id`、`batch_id`、`stream_type`、选中的 `instance_id` 和简洁选择原因

#### Scenario: 批次完成或失败
- **WHEN** TIAS 批次调用成功或失败
- **THEN** ai_quality 必须记录耗时、失败类型、失败原因、重试决策和最终任务状态，不输出冗余逐框检测明细

#### Scenario: Kafka offset 被提交
- **WHEN** ai_quality 提交 Kafka offset
- **THEN** 它必须记录 `task_id`、最终状态、Kafka topic、partition 和 offset

### Requirement: ai_quality 必须按健康状态和负载指标选择 TIAS 实例
MUST：以下场景为本要求的强制验收条件。
ai_quality 必须为每个小批次按实例健康状态、能力、并发容量、队列状态、耗时和失败指标选择 TIAS 实例。

#### Scenario: 存在健康实例
- **WHEN** 多个已注册 TIAS 实例都能处理该批次
- **THEN** ai_quality 必须优先选择 `running_batches < max_concurrent_batches` 的实例，再按更低的 `running_batches`、本进程近期选择次数、`avg_latency_ms`、`p95_latency_ms`、`queued_batches` 和 `recent_failure_count` 排序

#### Scenario: 实例缺少所需能力
- **WHEN** 某个 TIAS 实例没有声明批次所需能力
- **THEN** ai_quality 必须把该实例排除在本次批次调度之外

#### Scenario: 实例心跳已过期
- **WHEN** 某个 TIAS 实例心跳租约已过期
- **THEN** ai_quality 必须把该实例排除在调度之外

#### Scenario: 所有实例都已满载
- **WHEN** 所有健康 TIAS 实例的 `running_batches` 都大于或等于 `max_concurrent_batches`，且没有可用本地队列
- **THEN** ai_quality 必须按配置等待并重试，不能立即提交 Kafka offset

### Requirement: ai_quality 必须区分可重试失败
MUST：以下场景为本要求的强制验收条件。
当选中 TIAS 返回忙碌、不可用、超时或服务端错误时，ai_quality 必须支持换实例重试。

#### Scenario: TIAS 返回忙碌
- **WHEN** TIAS 对某个批次返回可重试忙碌响应
- **THEN** ai_quality 必须临时标记该实例忙碌，并在重试次数内把批次调度到其他可用实例

#### Scenario: TIAS 返回参数错误
- **WHEN** TIAS 返回不可重试的客户端参数错误
- **THEN** ai_quality 必须直接判定该批次失败，不再切换其他实例重试

#### Scenario: 达到重试上限
- **WHEN** 某个批次所有重试都失败
- **THEN** ai_quality 必须将课堂质量任务标记为失败，并把失败原因写入 `lesson_ai_workflow`

### Requirement: ai_quality 必须对不稳定 TIAS 实例做熔断
MUST：以下场景为本要求的强制验收条件。
ai_quality 必须在 TIAS 实例近期连续失败时，临时避开该实例。

#### Scenario: 达到失败阈值
- **WHEN** 某个 TIAS 实例达到配置的近期失败阈值
- **THEN** ai_quality 必须把该实例标记为熔断打开，并在冷却期内排除调度

#### Scenario: 冷却期结束
- **WHEN** 熔断冷却期结束
- **THEN** ai_quality 可以允许一次探测请求，或在心跳健康时重新把该实例纳入调度

### Requirement: ai_quality 必须确定性合并批次结果
MUST：以下场景为本要求的强制验收条件。
ai_quality 必须基于帧身份和帧顺序，把学生和教师批次结果合并回课程级指标。

#### Scenario: 批次结果乱序返回
- **WHEN** TIAS 批次响应的返回顺序和下发顺序不同
- **THEN** ai_quality 必须按 `stream_type` 和 `frame_index` 排序合并结果

#### Scenario: 成功响应缺少帧结果
- **WHEN** 某个成功批次响应遗漏了请求中的帧结果
- **THEN** ai_quality 必须按配置把该批次视为失败或部分失败，并记录明确错误

### Requirement: ai_quality 必须在远程调度成功后持久化课堂质量结果
MUST：以下场景为本要求的强制验收条件。
远程 TIAS 调度成功后，ai_quality 必须继续写入当前课堂质量产出表。

#### Scenario: 任务成功
- **WHEN** 所有必要的学生和教师批次都处理成功
- **THEN** ai_quality 必须写入行为时间线、核心快照、学生行为统计、指标得分和 `lesson_ai_workflow` 成功状态

#### Scenario: 任务失败
- **WHEN** 远程 TIAS 调度永久失败
- **THEN** ai_quality 必须标记 `lesson_ai_workflow` 失败，并按既有失败策略提交 Kafka offset

### Requirement: ai_quality 不得写入 lesson_ai_job
MUST：以下场景为本要求的强制验收条件。
`lesson_ai_job` 由上游生产者服务负责，ai_quality 不得插入、更新或改写该表状态。

#### Scenario: 任务开始处理
- **WHEN** ai_quality 开始处理 Kafka 课堂质量任务
- **THEN** 它只能更新 `lesson_ai_workflow` 的运行状态，不得更新 `lesson_ai_job`

#### Scenario: 任务最终成功或失败
- **WHEN** ai_quality 完成任务或判定任务最终失败
- **THEN** 它只能更新 `lesson_ai_workflow` 的最终状态、失败阶段和失败原因，不得更新 `lesson_ai_job`

#### Scenario: Kafka offset 提交判定
- **WHEN** ai_quality 判断是否提交 Kafka offset
- **THEN** 判定依据必须是 `lesson_ai_workflow` 已写入最终成功或最终失败状态，而不是 `lesson_ai_job` 状态

### Requirement: ai_quality 必须支持静态 TIAS 兜底实例
MUST：以下场景为本要求的强制验收条件。
ai_quality 必须支持静态 TIAS 实例列表，作为本地开发或注册机制不可用时的兜底方案。

#### Scenario: 注册表为空且配置了兜底实例
- **WHEN** Redis 中没有可用注册实例，且配置了静态兜底实例列表
- **THEN** ai_quality 可以直接查询静态 TIAS 实例，并使用其中健康的实例进行调度

#### Scenario: 注册表存在健康实例
- **WHEN** Redis 注册表中存在健康 TIAS 实例
- **THEN** ai_quality 必须优先使用注册实例，而不是静态兜底实例

### Requirement: ai_quality 必须通过多 TIAS 端到端联调
MUST：以下场景为本要求的强制验收条件。
ai_quality 必须支持 4 个 TIAS 实例注册后，从 Kafka `classroom_cv_task` 消费多任务并完成远程推理调度。

#### Scenario: Redis 由本地 Docker 提供
- **WHEN** 本地没有可用 Redis
- **THEN** 测试环境可以用 Docker 拉起 Redis，并配置 ai_quality 使用该 Redis 作为 TIAS 共享注册表

#### Scenario: 4 个 TIAS 实例注册成功
- **WHEN** 本地启动 4 个 TIAS 实例
- **THEN** Redis 注册表中必须能看到 4 个可调度实例，且心跳 TTL 持续刷新

#### Scenario: 6 个 Kafka 任务被消费
- **WHEN** 向 `10.67.65.8:9092` 的 `classroom_cv_task` 发送 6 个不同 `task_id` 的测试消息
- **THEN** ai_quality 必须消费这 6 个任务，并且不得从 `classroom_asr_task` 读取本次测试任务

#### Scenario: 多实例负载分布可观测
- **WHEN** 6 个任务被切分为多个 TIAS 推理批次
- **THEN** 批次应分布到多个 TIAS 实例，日志必须记录每个批次选中的 `instance_id` 和选择原因

#### Scenario: NFS 快照挂载可用
- **WHEN** `10.80.5.131:/image` 被挂载到项目 `mnt` 目录
- **THEN** ai_quality 必须把快照文件写入该挂载目录，并在数据库中保存可回溯的相对路径

#### Scenario: 多任务最终完成
- **WHEN** 6 个任务全部处理结束
- **THEN** 每个任务必须写入 `lesson_ai_workflow` 最终状态、行为时间线、核心快照、学生行为统计和指标得分结果，并按 offset 策略提交 Kafka offset
