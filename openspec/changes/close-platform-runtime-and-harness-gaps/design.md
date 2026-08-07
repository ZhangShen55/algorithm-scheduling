## Context

原设计确定四个可部署服务：`control-service`、`orchestrator-service`、`vision-orchestrator-service`、`online-gateway-service`；PostgreSQL 保存业务事实和 Outbox，Kafka 传课程级命令，Redis 保存算子 TTL 运行态和容量租约，算法实例继续使用现有 HTTP/WebSocket 协议。

复审按“设计存在、组件存在、运行时接线、真实环境证据”四个层级检查。当前结论如下：

| 设计项 | 当前实现证据 | 结论 |
|---|---|---|
| A 面稀疏提交、四 task type、幂等追加、HTTP 200 业务码 | `services/control_service/api.py`、PostgreSQL repository 与 API 测试 | 符合 |
| 整数状态机、Outbox 事务、优先级领取 | repository/state machine 与 PostgreSQL 并发测试 | 符合组件层 |
| Outbox 发布与 Kafka 消费 | 有 `OutboxPublisher`、`PipelineInitializer` 和 Producer Protocol；无 Kafka 客户端依赖、broker adapter、consumer loop | 不符合运行闭环 |
| `orchestrator-service` 可部署执行 | `main.py` 只有通用 FastAPI `/health`，未装配 Publisher、Consumer、Dispatcher、媒体和适配器 | 不符合 |
| 视觉自适应分析 | 扫描、缓存、区间、VBas client、证据和事件处理器分别存在；无组合 `VisualAnalyzer` 和常驻 consumer | 部分符合 |
| 在线图片与实时 ASR | 请求级租约、完整请求不拆分、WebSocket 粘性已接入真实 FastAPI 路由 | 基本符合 |
| 算子注册与容量 | Redis TTL、原子租约、ops 路由和八个源码入口已接入 | 部分符合 |
| 注册审计 | migration 有 PostgreSQL 表，运行代码没有写入这些表 | 不符合 |
| 算子容器可启动 | 源码导入平台 registry client，但 Dockerfile 未安装平台 wheel | 不符合部署闭环 |
| 临时目录清理 | `TerminalWorkspaceCleaner` 有单测，无终态执行入口 | 部分符合 |
| 指标和审计 | 指标/日志函数存在，只有少数在线/Outbox/运维路径调用 | 部分符合 |
| 五类端到端验收 | 测试使用内存 Producer，并直接调用 repository 把所有节点改成完成 | 不属于真实端到端 |
| 单机部署 | 有基础设施和算子 Compose，无四个平台服务 Compose | 部分符合 |

因此原架构方向继续采用，但旧 change 的 `70/70` 只能解释为“计划组件已生成”，不能解释为“平台可以按设计运行”。在本变更完成前，不归档为生产完成状态。

## Goals / Non-Goals

**Goals:**

- 四个服务入口启动后自动建立并管理其真实后台资源和循环。
- A 提交后不需要测试代码人工改状态，任务可经 Kafka、Worker、契约算子替身完成并查询。
- 视觉服务真正组合抽帧、自适应检测、VBas 租约、聚合和结果持久化。
- 平台和八个算子可通过明确 Compose/镜像步骤重复部署。
- PostgreSQL、Redis、Kafka、临时文件和长期结果的所有权与原设计一致。
- 用 Harness 记录变更证据与可重复验收，用 `AGENTS.md` 约束未来代理不再误报完成度。

**Non-Goals:**

- 不改变 A 面字段、路径、状态码、业务码和 task type。
- 不改变现有算法推理接口或模型逻辑。
- 不引入 Kubernetes、通用工作流产品、Service Mesh 或多机高可用。
- 不在本变更增加失败重试、人工补跑、取消和强制重算产品规则。
- 不把逐次改动流水账写进 `AGENTS.md`。

## Decisions

### 1. 保留四服务边界，补运行时装配而不是再次合并

`orchestrator-service` 负责通用离线 DAG，`vision-orchestrator-service` 负责长周期视觉迭代，`online-gateway-service` 保持低延迟同步入口，`control-service` 保持状态和控制面。问题在于装配缺失，不在服务边界本身。

备选“重新合并为一个进程”会破坏已经确认的故障域和伸缩边界，并让视觉长任务影响在线请求，因此不采用。

### 2. 用 FastAPI lifespan 管理后台资源

服务启动时建立 SQLAlchemy engine、Kafka producer/consumer、共享 HTTP client 和停止事件；使用 TaskGroup 启动后台循环；关闭时先停止消费，再等待当前消息处理边界，最后关闭 producer、consumer、HTTP client 和 engine。

健康检查分为：

- `/health`：进程存活。
- `/ops/readiness`：PostgreSQL/Redis/Kafka 和必要循环是否 ready。

入口契约测试必须验证运行时组件存在，不能只验证 `/health` 路由。

### 3. 引入正式 Kafka adapter 与 topic 契约

使用成熟 Python Kafka 客户端实现 `send_and_wait` 和消费循环。topic 至少包括：

```text
algorithm.course.commands
algorithm.visual.commands
algorithm.visual.events
```

Consumer 只有在处理成功后提交 offset；业务幂等由数据库唯一键保证。消息只携带 ID、本地路径和元数据，不携带媒体字节。

### 4. 通用节点执行器是 orchestrator 的核心闭环

执行器按 capability 读取 URGENT/NORMAL 节点，申请实例租约，并按 node code 调用既有组件：

```text
PPT_SLICE       -> 下载 P -> PPT 算子共享目录落盘 -> manifest 校验/终态回调
PPT_OCR         -> 按 ppt_image_id 调 OCR
PPT_KEYWORDS    -> 按 ppt_image_id 调 /v1/extract_keywords
ASR_TRANSCRIPTION -> 下载/共享 T -> WAV -> ASR v1.1.8
COURSE_OVERVIEW -> ASR segments -> /v1/course_overviews
TEACHER/STUDENT -> 发布课程级视觉命令，等待视觉完成事件
```

节点完成后统一计算 task type 状态；所选 task type 全终态后触发安全清理。测试不得再直接调用 repository 模拟执行器职责。

PPT 内部协议不保留旧 Base64 逐图回调。`ppt_slice` 在单个 Uvicorn Worker 内按配置支持 N 路并发，将普通图片直接写入 `/data/result/{task_id}/ppt/slices`，全部文件关闭后以临时文件原子替换生成 `manifest.json`，最后只回调一次 `task_id`、`operator_task_id`、`path`、`manifest_path`、`count` 和终态。`orchestrator-service` 校验任务身份、目录边界、manifest 状态、条目数和文件存在性后，在同一数据库事务中完成 `PPT_SLICE` 并释放 `PPT_OCR`。重复回调必须幂等；回调丢失时由运行中节点与 manifest 对账恢复。

由于 PPT 提交接口异步返回，容量租约不能在“已受理”响应后释放。`orchestrator-service` 在节点 RUNNING 期间续约所选实例，完成事务提交后释放；`ppt_slice` 心跳同时报告真实 `inflight`。单容器一个 Uvicorn Worker 与进程内 N 路视频处理并发不冲突。

### 5. 视觉组合器实现 VisualAnalyzer

新增组合类负责：

1. 从 `local_video_path` 按计划抽帧到 `/data/course/{task_id}`。
2. 使用缓存去重帧和推理。
3. 通过 control-service 租约同步调用 VBas。
4. 执行粗扫、候选扩展、10/5/2/1 秒细化和 gap merge。
5. 聚合教师区间或学生人数/区域指标。
6. 发布精选证据到 `/data/result/{task_id}/vision`。
7. 持久化进度和最终结构化结果。

Kafka 仍只在课程级边界使用，不把单帧循环拆到 Kafka。

### 6. Redis 负责实时态，PostgreSQL 负责审计事实

在现有 Redis registry 外增加审计 repository/decorator：注册、心跳摘要、desired lifecycle、unregister 和运维排空写入 PostgreSQL。Redis TTL 到期决定实时 OFFLINE；PostgreSQL 用于运维历史，不参与每次原子 lease 的热路径。

### 7. 平台和算子部署一起闭环

新增四个平台服务 Compose，与基础设施/算子使用同一 network 和共享挂载。八个算子镜像必须安装版本化 `algorithm-scheduling-platform` wheel；构建验证直接在镜像内执行 `import packages.operator_registry_client`。

主机模式 Kafka 使用 `127.0.0.1:9092`；容器模式提供独立 internal listener 和 service-name advertised address，不能混用。

### 8. Harness 和 AGENTS 分工

根 `AGENTS.md` 只增加平台项目地图和跨项目边界。平台级 `AGENTS.md` 固定以下 durable rules：

- 四服务职责和禁止跨层逻辑。
- A/算子/Kafka/文件契约不可擅改。
- `student_count`、`front_points`、`back_point` 和 `vbas` 命名不可改。
- `enabled_task_types` 表示交付能力边界；当前实例注册情况只决定动态 readiness 和状态 30，不得因短时无实例而改变是否接受已启用任务。
- 完成度必须有真实运行时证据。
- 修改哪些模块必须运行哪些 Harness。

`harness/` 保存可变化的证据：

```text
harness/README.md
harness/architecture-review.md
harness/change-ledger.md
harness/verification.md
harness/scenarios/*.md
```

每项调整记录“原状态、目标、文件、契约影响、测试、环境证据、剩余风险”，不把这些流水信息复制到 `AGENTS.md`。

### 9. 重新定义端到端完成门槛

真实调度端到端必须同时满足：

- PostgreSQL、Redis、Kafka 为真实进程。
- control/orchestrator/vision/online 四个入口真实启动。
- Consumer 真实接收 broker 消息。
- 算法可以是契约替身，但必须通过 HTTP/WebSocket 和 control-service lease 调用。
- 查询结果由 Worker 产生，测试不得人工调用 `complete_node` 或 `update_task_type_state`。
- 验证重启、重复消息、URGENT、等待算子、清理和指标。

## Risks / Trade-offs

- [真实 E2E 变慢且更容易受环境影响] → 单元测试继续覆盖算法细节，Harness 将 broker E2E 独立分层并输出诊断。
- [后台循环异常导致进程存活但不工作] → readiness 跟踪每个循环状态，TaskGroup 异常触发服务退出，由 Docker 重启。
- [视觉任务执行时间长] → 消息处理采用显式并发上限和幂等进度，不用短 Kafka poll timeout 持有整个任务。
- [Kafka 依赖下载或启动失败] → 固定镜像/客户端版本，保留镜像缓存和清晰的环境预检；没有 broker 证据不得勾选 E2E。
- [算子 wheel 与算法环境冲突] → wheel 只包含轻量共享客户端依赖，构建阶段执行导入和 ops contract 测试。
- [PostgreSQL 心跳写放大] → 只按配置周期写摘要或状态变化事件，Redis 保持高频 TTL。

## Migration Plan

1. 建立平台 `AGENTS.md` 和 Harness 基线，先记录当前不符合项。
2. 引入 Kafka adapter、配置和 runtime lifecycle，接通课程命令初始化。
3. 接通通用节点执行器和 task type 状态汇总，先跑 PPT/ASR。
4. 实现视觉组合器并接通 visual commands/events。
5. 接入清理、审计和全部指标。
6. 增加平台 Compose、Kafka internal listener 和算子 wheel 构建。
7. 使用真实 PostgreSQL/Redis/Kafka 和契约算子替身跑五类场景、重启和优先级验收。
8. Harness 证据全部通过后，更新旧完成度说明并决定是否同步/归档两个 change。

回滚以服务为单位：A 未切流前保留现有旧链路；数据库变更只增加审计数据；关闭新 Worker 不删除 Outbox 和任务事实；不在回滚时删除 `/data/result`。

## Open Questions

- Kafka 客户端最终选用 `aiokafka` 还是 `confluent-kafka`，需结合目标 Python/GPU 镜像的 wheel 可用性验证。
- PPT 终态回调由 `orchestrator-service` 的既有 FastAPI 端口暴露内部入口，不新增独立回调服务；共享 manifest 是耐久完成证据，回调是低延迟通知。
- 长视觉任务的 Kafka offset/任务并发策略采用短消息转内部数据库队列，还是延长处理模型。
- 八个算子镜像由统一父镜像安装 wheel，还是各 Dockerfile 显式 COPY/install。
