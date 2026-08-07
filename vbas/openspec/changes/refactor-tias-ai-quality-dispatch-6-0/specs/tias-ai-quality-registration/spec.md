## ADDED Requirements

### Requirement: TIAS 必须主动注册到 ai_quality
MUST：以下场景为本要求的强制验收条件。
TIAS 必须在启动后主动注册到 ai_quality，并且只有注册和心跳有效的实例才可进入可调度集合。

#### Scenario: TIAS 使用配置中的 ai_quality 入口
- **WHEN** TIAS 启动
- **THEN** TIAS 必须从 `config.toml` 读取 `AiQualityBaseUrl`，并使用该地址调用注册、心跳和注销接口

#### Scenario: TIAS 注册成功
- **WHEN** TIAS 启动且模型初始化完成
- **THEN** TIAS 必须调用 ai_quality 注册接口，并上报 `instance_id`、`base_url`、服务版本、模型版本、能力清单、`max_concurrent_batches` 和 `max_queue_size`

#### Scenario: 注册信息被共享保存
- **WHEN** ai_quality 收到合法注册请求
- **THEN** ai_quality 必须把实例记录写入或更新到 Redis，使所有 ai_quality worker 都读取同一份实例状态

### Requirement: ai_quality 必须提供 TIAS 注册 HTTP 接口
MUST：以下场景为本要求的强制验收条件。
ai_quality 必须提供 FastAPI HTTP 入口，用于接收 TIAS 注册、心跳和注销请求。

#### Scenario: 注册 HTTP 接口可用
- **WHEN** ai_quality HTTP 服务启动
- **THEN** 它必须暴露 TIAS 实例注册、心跳和注销接口

#### Scenario: Kafka worker 读取共享注册表
- **WHEN** ai_quality Kafka worker 需要调度一个推理批次
- **THEN** 它必须从 Redis 读取 TIAS 实例状态，不得依赖进程内注册表

### Requirement: TIAS 必须向 ai_quality 上报心跳状态
MUST：以下场景为本要求的强制验收条件。
TIAS 必须按配置周期向 ai_quality 发送心跳请求，心跳中包含当前运行状态、队列状态、耗时指标和失败指标。

#### Scenario: 心跳更新实例状态
- **WHEN** ai_quality 收到已注册 TIAS 实例的合法心跳
- **THEN** ai_quality 必须更新 Redis 中的实例状态、队列指标、耗时指标、失败指标、`last_heartbeat_at` 和租约过期时间

#### Scenario: 心跳上报不可用状态
- **WHEN** TIAS 正在排空、忙碌或降级
- **THEN** 心跳必须上报对应 `status`，使 ai_quality 停止或减少向该实例调度新批次

### Requirement: ai_quality 必须让过期 TIAS 实例失效
MUST：以下场景为本要求的强制验收条件。
当 TIAS 心跳租约过期时，ai_quality 必须把该实例视为不可调度。

#### Scenario: 心跳租约过期
- **WHEN** Redis 中的 `ai_quality:tias:instance:{instance_id}` 因 TTL 过期而不存在
- **THEN** ai_quality 必须把该实例视为不可用于新批次调度

#### Scenario: 过期实例恢复心跳
- **WHEN** 已过期的 TIAS 实例重新发送合法心跳
- **THEN** ai_quality 必须更新实例状态，并且只有当状态和能力都合法时才允许重新调度

### Requirement: ai_quality 必须支持 TIAS 显式注销
MUST：以下场景为本要求的强制验收条件。
ai_quality 必须支持 TIAS 在优雅关闭或排空时发送显式注销或下线通知。

#### Scenario: TIAS 注销
- **WHEN** TIAS 在优雅关闭期间发送注销请求
- **THEN** ai_quality 必须把该实例标记为 `DOWN`，或从可调度集合中移除

#### Scenario: TIAS 关闭前进入排空
- **WHEN** TIAS 准备停止接收新任务
- **THEN** 它必须先上报 `DRAINING`，使 ai_quality 停止分配新批次

### Requirement: 注册和心跳字段必须稳定且有中文语义
MUST：以下场景为本要求的强制验收条件。
注册和心跳契约必须使用稳定字段，并在设计文档中说明字段中文含义。

#### Scenario: 注册字段被解析
- **WHEN** ai_quality 解析注册或心跳请求
- **THEN** 它必须校验实例身份字段、访问地址字段、能力字段和容量字段后再更新注册表

#### Scenario: 可选耗时指标缺失
- **WHEN** TIAS 在启动初期无法上报可选耗时指标
- **THEN** ai_quality 必须接受 `null` 或缺失的耗时指标字段，不能因此拒绝注册或心跳
