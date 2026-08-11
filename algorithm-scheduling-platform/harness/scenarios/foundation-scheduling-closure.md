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

里程碑 1 和里程碑 2A 均符合。2A 已达到真实 PostgreSQL、Redis、Kafka、两个独立平台服务进程和独立 HTTP 契约 Stub 的基础调度闭环；不表示真实 PPT、OCR、离线 ASR、VBas 或视觉链路已接入。

## 里程碑 2 分层验收

### 2A：真实 Broker 与契约 Stub

- `scripts/run_milestone_2a.py` 启动并等待真实 PostgreSQL、Redis、Kafka 后，运行真实进程 Harness；测试使用每次唯一且以 `_test` 结尾的 PostgreSQL 数据库、Redis DB 14 的 UUID 前缀、唯一 Kafka Topic/Consumer Group 和临时端口。
- NORMAL 与 URGENT 的 ASR-only 请求均经过 `POST /api/course-jobs`、事务 Outbox、真实 Kafka、幂等 DAG、Control HTTP 租约和独立 Stub `/execute`，测试没有调用 Repository 完成节点。
- 未注册实例时，两个 `ASR_TRANSCRIPTION` 均由 Worker 推进到状态 30；注册 `asr_offline`、`text_analysis` 并首次心跳后，GET 轨迹实际观察到节点状态 10、50、60，任务类型最终为 60。
- URGENT 的 `ASR_TRANSCRIPTION` Stub 调用先于 NORMAL；四次 Stub 调用覆盖两个任务各自的 `ASR_TRANSCRIPTION` 和 `COURSE_OVERVIEW`，GET 结果包含 Stub 返回值。
- 首次消费提交 offset 2；停止 orchestrator 后注入重复 Kafka 消息并将一条 Outbox 恢复为未发布，再启动同一 Consumer Group，提交 offset 推进到 4。重复处理后仍只有 2 个任务类型、4 个节点，Outbox 重新发布项的 `publish_attempts` 为 2。
- Control、orchestrator 和独立 Stub 均以真实 `/health` HTTP 200 作为启动条件，orchestrator 两次启动还分别通过 `/ops/readiness` HTTP 200；非 200 响应不能作为进程就绪证据。
- 两次 orchestrator 启动保留不同 PID、启动序号、健康响应、停止日志和真实退出码；首次停止日志不会被第二次启动覆盖，所有 Uvicorn run 均已停止且无 Traceback。
- 终态后 Redis `lease:*` key 和实例租约集合均为空；本次唯一 Consumer Group 在服务停止后按完整名称删除并验证不存在，再清理本次唯一 Topic。完整 JSON 证据写入 gitignore 的 `harness/reports/milestone-2a/`，包括容器镜像/健康、隔离标识、健康/readiness 响应、两次进程身份和日志、请求响应、Outbox、offset、状态轨迹、Stub 调用、租约、Consumer Group 清理和最终 GET。

### 2B：首个真实同步算子

- 优先从 OCR 或离线 ASR 选择一个，使用真实注册、首次心跳、容量和推理响应替换 Stub。ScreenDet 只属于 `online-gateway-service`，不进入离线 2B DAG。
- 平台任务状态由 orchestrator 根据调用事实推进；算子不直接写 PostgreSQL，也不直接汇报课程节点状态给 control。
- 验证同能力多实例按请求分发、过载快速失败、错误分类、关联日志和结果适配。
- PPT 等异步长任务使用 `operator_task_id`、续租、终态通知和恢复对账，待内部契约冻结后单独接入。
