> **后续范围调整已废止（2026-08-21）**
>
> 本规格中 `PPT_KEYWORDS`、`COURSE_OVERVIEW` 与 Text Analysis 运行时目标不再属于当前平台；
> PPT/OCR、ASR-only 及其他运行时合同继续有效，现行边界见
> `retire-text-analysis-from-scheduling-platform`。

## ADDED Requirements

### Requirement: Control 只在事务内写入 Outbox
`control-service` SHALL 在同一个 PostgreSQL 事务中持久化课程任务事实和 Outbox 事件，且 SHALL NOT 在 API 请求进程中直接发布 Kafka。Outbox Publisher SHALL 属于 `orchestrator-service` 的独立后台循环。

#### Scenario: API 提交时 Kafka 不可用
- **WHEN** A 服务提交有效任务但 Kafka 暂时不可访问
- **THEN** 任务事实与 Outbox 仍原子提交，API 可查询已接纳状态，事件等待 orchestrator Publisher 在 Kafka 恢复后发布

### Requirement: 基础调度闭环不依赖真实 PPT
平台 SHALL 使用集成测试专用契约 Stub 验证 control 与 orchestrator 的基础运行时，真实 PPT、OCR、ASR、视觉和在线算子 SHALL NOT 成为该里程碑的启动或完成前提。

#### Scenario: PPT 算子仍在独立优化
- **WHEN** PostgreSQL、Redis、Kafka、control、orchestrator 和契约 Stub 已就绪，但真实 PPT 未部署
- **THEN** 基础闭环仍能验证 Outbox 发布、Kafka 消费、DAG、状态 30、租约、节点完成和查询结果

### Requirement: Orchestrator 运行时启动真实后台循环
`orchestrator-service` SHALL 在应用 lifespan 期间启动真实的 Kafka Producer、课程命令 Consumer、Outbox Publisher、节点 Dispatcher、节点 Executor、任务状态 Aggregator 和终态清理循环，并 SHALL 优雅关闭全部资源。

#### Scenario: 服务在依赖健康时启动
- **WHEN** PostgreSQL、Redis 和 Kafka 可访问，并启动 orchestrator
- **THEN** 只有 Publisher、Consumer 和 Executor 循环全部运行后，就绪状态才变为健康

#### Scenario: Kafka Consumer 循环意外退出
- **WHEN** 必需的后台循环意外终止
- **THEN** 就绪状态变为不健康，服务退出或被重启，而不是继续作为只有健康接口的空进程运行

### Requirement: 课程命令使用真实 Kafka Broker
平台 SHALL 将 Outbox 事件发布到真实 Kafka topic，并通过具有提交语义的 Consumer Group 消费。只有幂等管道初始化成功后，SHALL 提交 offset。

#### Scenario: Kafka 不可用时 API 完成提交
- **WHEN** Kafka 不可用期间 control-service 提交任务和 Outbox 事件
- **THEN** 事件保持待发布，并在 Kafka 恢复后发布和消费

### Requirement: 节点执行产生状态和结果
orchestrator SHALL 按优先级和能力领取就绪节点、申请算子租约、执行必要的媒体准备和适配器调用、持久化真实节点结果、释放后续节点、推导任务类型终态并释放租约。

#### Scenario: PPT 管道完成
- **WHEN** A 服务提交 PPT 任务，并且所需的契约兼容算子已经就绪
- **THEN** Worker 产生的结果从切片依次推进到 OCR 和关键词，不由测试或算子直接更新 Repository 状态

#### Scenario: PPT 算子发布持久化共享路径结果
- **WHEN** PPT 算子完成平台内部异步切片任务
- **THEN** 它原子发布 `/data/result/{task_id}/ppt/manifest.json`，发送一次不含 Base64 图片字节的终态元数据回调；orchestrator 只有校验 manifest 和文件后才将 `PPT_SLICE` 标记为完成

#### Scenario: PPT 异步容量保持预留
- **WHEN** PPT 提交已受理，但终态回调尚未完成提交
- **THEN** orchestrator 续约选中的算子租约，直到终态持久化或发生终态错误后才释放

#### Scenario: 算子不可用
- **WHEN** 就绪节点没有可用的就绪算子容量
- **THEN** 节点保持状态 30，同时无关能力继续执行

### Requirement: 视觉运行时组合自适应分析
`vision-orchestrator-service` SHALL 消费课程级视觉命令，并使用具体 Analyzer 执行本地抽帧、缓存、自适应规划、容量路由的 VBas 调用、聚合、证据发布、进度事件和结构化结果持久化。

#### Scenario: 教师板书需要细化
- **WHEN** 粗扫发现板书候选
- **THEN** 视觉运行时执行更密集的同步 VBas 检测轮次，在发布完成事件前持久化细化后的区间和选定证据

### Requirement: 运行时负责清理、审计和指标
真实 Worker 执行 SHALL 在对应生命周期边界触发终态工作区清理、节点审计日志、任务/节点/Outbox/Kafka/算子/租约指标和任务状态聚合。

#### Scenario: 所有已请求管道进入终态
- **WHEN** 持久化结果文件存在，并且所有已请求节点均进入终态
- **THEN** Worker 只删除 `/data/course/{task_id}`，保留 `/data/result/{task_id}`，并记录清理结果

### Requirement: 在线 HTTP 资源优雅关闭
`online-gateway-service` SHALL 在应用关闭期间关闭共享 HTTP Client 和 WebSocket 相关资源。

#### Scenario: 网关停止
- **WHEN** online-gateway 收到优雅关闭信号
- **THEN** 共享 HTTP 连接池关闭且不泄漏资源
