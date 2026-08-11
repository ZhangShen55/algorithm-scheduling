# 场景：方案 C 基础调度闭环

## 目标边界

当前阶段由两个连续里程碑组成：

1. `control-service` 负责 PostgreSQL Repository、任务状态机、幂等提交/查询、事务内 Outbox，以及算子注册、心跳、排空和容量租约。
2. `orchestrator-service` 负责 Outbox Publisher、真实 Kafka Producer/Consumer、DAG 幂等初始化、节点领取、状态推进、容量租约和通用算子调用框架。

`control-service` 不直接发布 Kafka；Publisher 必须作为 `orchestrator-service` 的独立后台循环从 PostgreSQL 读取待发布 Outbox。真实 PPT 正在独立优化，不是本场景的依赖或完成条件。

## 前置条件

- 真实 PostgreSQL、Redis、Kafka 容器健康。
- `control-service` 与 `orchestrator-service` 以各自配置真实启动。
- 集成测试专用算子 Stub 使用稳定能力代码注册，并返回可持久化的契约结果。
- 目标数据库已按顺序执行 `0001-0005`；10 张调度表及所有表、字段中文注释可查询。

## 必须验证的流程

1. 调用 `POST /api/course-jobs` 后，课程事实、任务类型和 Outbox 在同一事务中可见。
2. Publisher 领取 Outbox，收到 Kafka 发布确认后才写 `published_at`。
3. Consumer 从真实 `algorithm.course.commands` 消费并幂等初始化 DAG，成功持久化后才提交 offset。
4. 无可用能力时节点进入状态 30；Stub 注册并获得容量后，节点依次进入运行和完成状态。
5. URGENT 只在等待节点中优先，不抢占已经运行的 NORMAL 节点。
6. `GET /api/course-jobs/{task_id}` 返回 Worker 真实推进的任务类型、节点、中文原因和 Stub 结果。
7. 重复 Outbox 发布、重复 Kafka 消息和服务重启不产生重复任务类型或重复节点。

## 禁止的验收捷径

- 测试不得直接调用 Repository 的完成节点或更新任务终态方法。
- 不得使用内存 Producer/Consumer 代替 Broker 证据。
- 不得把静态迁移测试、API 单测或 PPT 组件测试计为本场景通过。
- 不得要求真实 PPT 算子就绪后才开始本场景。

## 证据要求

记录容器版本和健康状态、API 请求/响应、Outbox 行、Kafka topic/offset、节点状态变化、所选 Stub 实例、Redis 租约和最终查询结果。所有命令必须可重复执行，跳过的集成测试不算通过。

## 里程碑 1 已验证边界

- FastAPI lifespan 在启动期创建并在关闭期释放 Engine/Redis，应用导入不建立网络连接。
- 任务幂等提交、后续追加 task type、URGENT/NORMAL 和中文 `reason` 已在真实 PostgreSQL 验证。
- 课程、task type 与 Outbox 同事务；Outbox 写失败时三者一起回滚，Control 不装配 Kafka Producer。
- 算子注册/重注册、心跳摘要、排空、注销和历史事件已写入 PostgreSQL；TTL 和租约热路径仍只访问 Redis。
- Redis 已验证并发注册/心跳/注销、过期租约、重注册清理、DRAINING 和 `max(active_leases, reported_inflight)` 容量语义。`register` 先返回 OFFLINE，首次成功心跳后才开放租约；客户端启动等待首次心跳，后续短暂 HTTP 故障会继续重试。
- `/health` 只表示存活；`/ops/readiness` 并行检查 PostgreSQL、Redis、10 张表、全部预期字段和中文说明、`0005` 索引/状态语义以及待补写心跳审计，不检查 Kafka。
- A 面数据库故障保持 HTTP 200 并返回业务码 `50000`；注册、心跳、生命周期和租约基础设施故障返回 HTTP 503。

## 当前结论

里程碑 1 符合；方案 C 整体仍为部分符合。真实 Kafka adapter、Publisher、Consumer、Dispatcher、Stub 调用和服务重启闭环尚未完成，因此不得宣称基础调度闭环完成。

## 里程碑 2 分层验收

### 2A：真实 Broker 与契约 Stub

- 实际启动 PostgreSQL、Redis、Kafka、`control-service`、`orchestrator-service` 和独立 HTTP Stub。
- A 请求可先由测试脚本发送，但任务必须经过 HTTP、Outbox、Kafka、DAG、租约和 Stub 调用，不得直接调用 Repository 完成节点。
- 留存 Outbox 发布确认、Kafka topic/offset、节点状态轨迹、Redis 租约、Stub 调用记录和最终查询结果。
- 覆盖状态 30 后恢复、URGENT/NORMAL、重复消息、重复发布和 Worker 重启恢复。

### 2B：首个真实同步算子

- 优先从 OCR 或 ScreenDet 选择一个，使用真实注册、首次心跳、容量和推理响应替换 Stub。
- 平台任务状态由 orchestrator 根据调用事实推进；算子不直接写 PostgreSQL，也不直接汇报课程节点状态给 control。
- 验证同能力多实例按请求分发、过载快速失败、错误分类、关联日志和结果适配。
- PPT 等异步长任务使用 `operator_task_id`、续租、终态通知和恢复对账，待内部契约冻结后单独接入。
