## ADDED Requirements

### Requirement: 明确可恢复的 PostgreSQL 事务错误有限重试
系统 SHALL 仅对 PostgreSQL `40P01` 和 `40001` 等明确声明为可恢复的事务错误执行配置化有限重试；每次尝试 MUST 使用新事务、指数退避和随机抖动。

#### Scenario: 首次死锁后重试成功
- **WHEN** 节点领取或状态协调首次返回 `40P01` 且后续尝试成功
- **THEN** 调度 SHALL 继续推进，节点不得被写成业务失败，Orchestrator 后台循环保持运行

#### Scenario: 可恢复事务错误持续超过上限
- **WHEN** 同一操作的可恢复事务错误耗尽配置化尝试次数
- **THEN** 系统 SHALL 保留节点可恢复状态、记录 operation/sqlstate/attempts 并降低 readiness，且不得把该错误转换为节点状态 70

#### Scenario: 不可恢复数据库错误
- **WHEN** 发生认证失败、迁移缺失、SQL 编程错误或状态机不变量错误
- **THEN** 系统 MUST 不执行无限重试，并 SHALL 进入可诊断的 fatal 处置

### Requirement: 后台循环不得形成存活但永久停摆状态
关键后台循环对可恢复基础设施异常 SHALL 持续退避重试；不可恢复异常导致循环无法继续时，Orchestrator MUST 受控退出容器主进程或由 supervisor 恢复循环，不得保持 `/health=200`、`/ops/readiness=503` 且所有后台循环永久停止的僵尸状态。

#### Scenario: 节点执行循环遇到瞬时死锁
- **WHEN** `node_executor` 收到已分类的瞬时数据库错误
- **THEN** Outbox、课程 Consumer、视觉发布、视觉结果 Consumer 和 PPT 对账不得因该单次错误全部停止，`node_executor` SHALL 在退避后继续

#### Scenario: 节点执行循环遇到 fatal 错误
- **WHEN** `node_executor` 发生不可恢复不变量错误
- **THEN** readiness SHALL 暴露 fatal 原因，容器主进程 MUST 退出以触发既有 Docker 重启策略，不能只设置全局停止事件后继续提供存活 HTTP

### Requirement: 单任务业务失败与基础设施失败分离
算子业务错误、参数错误和不可重试模型错误 SHALL 只影响对应节点；PostgreSQL、Kafka、Control Service 和运行时监督错误 MUST 按基础设施语义处理，不得伪装成算法节点失败。

#### Scenario: 一个 ASR 返回业务错误
- **WHEN** 单个 ASR 算子返回已定义的不可重试业务错误
- **THEN** 仅该 ASR 节点进入失败终态，其他 ASR、PPT 和视觉任务继续调度

#### Scenario: PostgreSQL 瞬时错误
- **WHEN** Repository 事务收到可恢复 SQLSTATE
- **THEN** 该节点不得生成“ASR/PPT/OCR 算法失败”原因，运维查询 SHALL 能区分基础设施重试与业务终态

### Requirement: 普通离线节点可以从过期领取状态恢复
Orchestrator 启动恢复器 SHALL 在领取者失效、领取超时且相关容量租约不存在或已过期时，把普通状态 40/50 节点恢复为状态 30；恢复 MUST 保留 attempt 和诊断事实。

#### Scenario: ASR 执行期间 Orchestrator 进程退出
- **WHEN** 一个 ASR 节点停在状态 40/50，原执行器已失效且归属租约过期
- **THEN** 新 Orchestrator SHALL 将节点恢复为可重新领取状态，并通过正常幂等路径继续处理

#### Scenario: 有效租约仍存在
- **WHEN** 状态 40/50 节点仍关联有效且归属一致的容量租约
- **THEN** 普通恢复器 MUST 不抢占或重复执行该节点

#### Scenario: PPT 异步节点恢复
- **WHEN** `PPT_SLICE` 已被算子受理但 Orchestrator 重启
- **THEN** 系统 MUST 使用既有 `operator_task_id`、progress、manifest、回调和对账恢复，不得按普通节点规则创建第二个切片任务

### Requirement: 运行状态和错误恢复可观测
`/ops/readiness`、结构化日志和指标 SHALL 暴露每个关键循环是否运行、最近瞬时错误类型、SQLSTATE、重试次数、恢复次数和 fatal 原因，并 MUST 遵守日志脱敏合同。

#### Scenario: 瞬时死锁恢复
- **WHEN** 调度经历一次 `40P01` 后恢复
- **THEN** 运维人员 SHALL 能看到一次数据库事务重试和恢复计数，日志不得包含视频 URL、完整请求体、ASR/OCR 文本或凭据

### Requirement: 真实数据库和远端负载验证是发布门禁
修复 MUST 通过真实 PostgreSQL 并发验证、服务 lifespan 验证和 `192.168.29.11` 三 GPU ASR/PPT/OCR 验证；Fake Repository 单元测试不得单独授权发布。

#### Scenario: ASR 十六并发一百任务重跑
- **WHEN** 使用全新任务前缀并发 16 提交 100 个真实 ASR 离线任务
- **THEN** 100 个提交 MUST 全部进入合法终态，Orchestrator readiness 全程不得因后台循环退出而永久变为 503，PostgreSQL 日志不得出现调度 SQL 死锁，报告 SHALL 记录总耗时、成功率、任务延迟和三实例租约分布

#### Scenario: 其他任务类型回归
- **WHEN** 分别执行 PPT Slice/PPT OCR 积压、教师/学生视觉和四任务类型混合负载
- **THEN** 所有泳道 SHALL 独立推进，一种能力的容量等待或瞬时错误不得永久终止其他泳道
