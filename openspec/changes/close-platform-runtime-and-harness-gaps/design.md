## 背景

原设计确定四个可部署服务：`control-service`、`orchestrator-service`、`vision-orchestrator-service`、`online-gateway-service`；PostgreSQL 保存业务事实和 Outbox，Kafka 传课程级命令，Redis 保存算子 TTL 运行态和容量租约，算法实例继续使用现有 HTTP/WebSocket 协议。

复审按“设计存在、组件存在、运行时接线、真实环境证据”四个层级检查。当前结论如下：

| 设计项 | 当前实现证据 | 结论 |
|---|---|---|
| A 面稀疏提交、四 task type、幂等追加、HTTP 200 业务码 | `control_service/app/api/control.py`、PostgreSQL repository 与 API 测试 | 符合 |
| 整数状态机、Outbox 事务、优先级领取 | repository/state machine 与 PostgreSQL 并发测试 | 符合组件层 |
| Outbox 发布与 Kafka 消费 | 有 `OutboxPublisher`、`PipelineInitializer` 和 Producer Protocol；无 Kafka 客户端依赖、broker adapter、consumer loop | 不符合运行闭环 |
| `orchestrator-service` 可部署执行 | `main.py` 只有通用 FastAPI `/health`，未装配 Publisher、Consumer、Dispatcher、媒体和适配器 | 不符合 |
| 视觉自适应分析 | 扫描、缓存、区间、VBas client、证据和事件处理器分别存在；无组合 `VisualAnalyzer` 和常驻 consumer | 部分符合 |
| 在线图片与实时 ASR | 请求级租约、完整请求不拆分、WebSocket 粘性已接入真实 FastAPI 路由 | 基本符合 |
| 算子注册与容量 | Redis TTL、原子租约、首次心跳激活、ops 路由和注册客户端已接入 | 里程碑 1 范围符合 |
| 注册审计 | PostgreSQL 已持久化注册/重注册、心跳摘要、生命周期和注销历史 | 里程碑 1 范围符合 |
| 算子容器可启动 | 源码导入平台 registry client，但 Dockerfile 未安装平台 wheel | 不符合部署闭环 |
| 临时目录清理 | `TerminalWorkspaceCleaner` 有单测，无终态执行入口 | 部分符合 |
| 指标和审计 | 指标/日志函数存在，只有少数在线/Outbox/运维路径调用 | 部分符合 |
| 五类端到端验收 | 测试使用内存 Producer，并直接调用 repository 把所有节点改成完成 | 不属于真实端到端 |
| 单机部署 | 有基础设施和算子 Compose，无四个平台服务 Compose | 部分符合 |

因此原架构方向继续采用，但旧 change 的 `70/70` 只能解释为“计划组件已生成”，不能解释为“平台可以按设计运行”。在本变更完成前，不归档为生产完成状态。

当前实施采用方案 C：一个基础调度阶段包含两个连续里程碑。里程碑 1 先验证 `control-service`
的 PostgreSQL 任务事实、事务 Outbox、注册审计和 Redis 容量；里程碑 2 再验证
`orchestrator-service` 的 Publisher、真实 Kafka Consumer、DAG、节点执行和通用算子调用框架。
真实 PPT 正在独立优化，不是这两个里程碑的完成前提。

## 目标 / 非目标

**Goals:**

- 四个服务入口启动后自动建立并管理其真实后台资源和循环。
- A 提交后不需要测试代码人工改状态，任务可经 Kafka、Worker、契约算子替身完成并查询。
- 视觉服务真正组合抽帧、自适应检测、VBas 租约、聚合和结果持久化。
- 平台和八个算子可通过明确 Compose/镜像步骤重复部署。
- PostgreSQL、Redis、Kafka、临时文件和长期结果的所有权与原设计一致。
- 10 张正式调度表及全部物理字段在 PostgreSQL 中具有可查询的中文说明。
- 用 Harness 记录变更证据与可重复验收，用 `AGENTS.md` 约束未来代理不再误报完成度。

**Non-Goals:**

- 不改变 A 面字段、路径、状态码、业务码和 task type。
- 不改变现有算法推理接口或模型逻辑。
- 不引入 Kubernetes、通用工作流产品、Service Mesh 或多机高可用。
- 不在本变更增加失败重试、人工补跑、取消和强制重算产品规则。
- 不把逐次改动流水账写进 `AGENTS.md`。

## 设计决策

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

### 4. 当前基础阶段采用方案 C

当前阶段必须完成两个连续、可独立验收的里程碑：

1. `control-service`：真实 PostgreSQL Repository、幂等提交/查询、整数状态、同事务 Outbox、
   算子注册审计以及 Redis TTL/容量租约。
2. `orchestrator-service`：独立 Outbox Publisher、真实 Kafka Producer/Consumer、DAG 幂等初始化、
   节点领取/推进、状态汇总、租约申请和通用算子调用框架。

基础验收使用真实 PostgreSQL、Redis、Kafka 和实际服务进程；算法端使用集成测试专用契约 Stub。
它必须证明 `POST -> PostgreSQL/Outbox -> Kafka -> DAG -> Stub -> GET`，并覆盖等待算子状态 30、
URGENT/NORMAL、重复消息和重启。测试不得直接调用 Repository 完成节点。

### 5. 通用节点执行器是 orchestrator 的核心闭环

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

真实 PPT、OCR、ASR 和视觉算子不是基础阶段的验收依赖。`ppt_slice` 内部协议冻结后，才按上述
执行器契约接入真实 PPT 管道。

### 6. 视觉组合器实现 VisualAnalyzer

新增组合类负责：

1. 从 `local_video_path` 按计划抽帧到 `/data/course/{task_id}`。
2. 使用缓存去重帧和推理。
3. 通过 control-service 租约同步调用 VBas。
4. 执行粗扫、候选扩展、10/5/2/1 秒细化和 gap merge。
5. 聚合教师区间或学生人数/区域指标。
6. 发布精选证据到 `/data/result/{task_id}/vision`。
7. 持久化进度和最终结构化结果。

Kafka 仍只在课程级边界使用，不把单帧循环拆到 Kafka。

### 7. Redis 负责实时态，PostgreSQL 负责审计事实

在现有 Redis registry 外增加审计 repository/decorator：注册、心跳摘要、desired lifecycle、unregister 和运维排空写入 PostgreSQL。Redis TTL 到期决定实时 OFFLINE；PostgreSQL 用于运维历史，不参与每次原子 lease 的热路径。注册只写声明并清理同 ID 旧心跳/租约，首次成功心跳后才允许路由；客户端启动必须等待该心跳，后续短暂 HTTP 故障按周期重试。现阶段同一 `instance_id` 只允许一个存活进程，世代令牌不在里程碑 1 范围内。

重新注册不得覆盖 PostgreSQL 中持久化的 `DRAINING/OFFLINE` 运维意图。受控部署或
stop/restart 验收在成功发布本轮容器账本后，必须按权威 Compose profile 或显式实例调用
管理面生命周期接口恢复 `ONLINE`，然后再验证首次就绪心跳；不得通过清库、删 Redis key
或让算子启动自动覆盖运维意图来绕过该步骤。

### 8. 平台和算子部署一起闭环

新增四个平台服务 Compose，与基础设施/算子使用同一 network 和共享挂载。八个算子镜像必须安装版本化 `algorithm-scheduling-platform` wheel；构建验证直接在镜像内执行 `import packages.operator_registry_client`。

主机模式 Kafka 使用 `127.0.0.1:9092`；容器模式提供独立 internal listener 和 service-name advertised address，不能混用。

### 9. Harness 和 AGENTS 分工

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

### 10. 重新定义端到端完成门槛

真实调度端到端必须同时满足：

- PostgreSQL、Redis、Kafka 为真实进程。
- control/orchestrator/vision/online 四个入口真实启动。
- Consumer 真实接收 broker 消息。
- 算法可以是契约替身，但必须通过 HTTP/WebSocket 和 control-service lease 调用。
- 查询结果由 Worker 产生，测试不得人工调用 `complete_node` 或 `update_task_type_state`。
- 验证重启、重复消息、URGENT、等待算子、清理和指标。

### 11. 数据库 DDL 通过前向迁移维护中文说明

正式结构由 `0001-0003` 定义 10 张调度表、依赖关系和调度索引；
`0004_schema_comments.sql` 对每张表和每个物理字段执行 `COMMENT ON TABLE/COLUMN`；
`0005_operator_audit_and_status_comments.sql` 增加算子历史索引，并修正 `40 已排队、50 处理中` 状态说明。以后新增字段
必须在新的前向迁移中同时增加注释，不回改已经执行过的旧迁移作为唯一交付方式。

2026-08-07 只读审计确认：本机 `algorithm` 业务库没有用户表；两个测试库只包含调度测试表，
其中 repository 测试库具有全部 10 张表。审计不授权自动执行迁移、删除表或清理数据。

### 12. 真实业务泳道按依赖分阶段关闭

2026-08-18 起，剩余实施固定拆成五个连续验收阶段：

1. 在新 SHA/release 中修复并重跑 FaceRec 三实例 GPU 证据，同时执行部署层用例。
2. 贯通 PPT、OCR、关键词以及离线 ASR、课程脑图，执行所属反例和压力用例。
3. 贯通教师/学生视觉命令、抽帧、自适应 VBas、聚合、证据和完成事件。
4. 贯通在线图片路由、实时 ASR WebSocket 粘性和 FaceRec 人脸管理代理。
5. 重新执行全部 217 条反例和 26 条压力用例，生成完整产品总报告。

继续使用本 OpenSpec，不创建重复变更。阶段可以在无依赖时继续推进，但前序失败不得标记完成。
历史 release 只读；修复后必须使用新的 Git SHA 和不可变报告目录。

### 13. 243 条用例是最终门槛，不是前置假设

217 条反例和 26 条压力用例中包含依赖真实泳道的场景，因此不能在泳道实现前伪造执行。
实施时先建立稳定 ID、所属阶段、前置条件、动作、预期、超时、清理和证据合同，再随阶段执行；
最终阶段必须重新运行完整集合，并断言没有“未执行及原因”。

没有已确认吞吐 SLO 的压力场景只判定请求正确性、容量合同、资源稳定性和恢复行为，同时报告
实际吞吐与延迟，不把测试值升级为产品承诺。任何失败使对应阶段和最终报告保持失败。

### 14. 已恢复前驱允许新 SHA 开启新的维护事务

跨 SHA 续跑需要区分“前驱维护仍活跃”和“前驱已经成功恢复”。前者继续只读继承原
snapshot/paused authority，不重新暂停业务；后者的 canonical paused ledger 已按恢复合同归档，
当前新 SHA 必须建立自己的 snapshot/paused，不能复制归档或伪造旧 ledger。

已恢复前驱只有在以下事实全部成立时才可作为新事务起点：snapshot 是当前部署身份所有的
`0600` 单链接普通文件；恰有一个命名合法的 `0400` 单链接 audit；不存在残留 archive metadata；
audit 为空时获准选择的 `ocr-v6-amd` 在 snapshot 中原本不是 running，audit 非空时全部记录均为
终态且 restart policy 已恢复；容器当前 ID、名称、镜像、挂载、端口、标签、策略和状态与上述
事实一致。当前 release 仍从立即前驱解析算子 baseline/new 所有权，并只授权经权威 Compose 与
Docker inspect 推导的现有监听端点。任何 partial、多个 audit、可写/链接归档、active 状态或
容器漂移均 fail closed；旧 release 始终只读。

新事务在任何 snapshot/pause 前，先在当前 release 原子发布
`operator-maintenance-predecessor.json`。该 marker 只记录立即前驱 root/SHA，必须由当前 UID
所有、为非 symlink `0400` 单链接普通文件，且不可替换。marker-only 表示 snapshot 尚未开始；
marker + snapshot 表示 snapshot 已完成但 pause 尚未完成；marker + snapshot/paused 表示本地
active 维护事务可直接复用。续跑携带的 `PREVIOUS_RELEASE_ROOT` 必须与 marker 精确一致；marker
缺失、可写、symlink、额外硬链接或绑定不一致均 fail closed。这样在 snapshot 或 pause 后中断时，
同一新 SHA 可以继续原事务，而不会重做 snapshot、重新暂停或改写旧 release。

provenance 的 authority 允许在后继执行期间从 active 变为 completed。解析器仍要求 provenance
自身 schema/source/path 严格匹配；active snapshot/paused 无论在 provenance 发布时还是后续每次
解析时，都必须执行与 direct/reuse-local 相同的权限、schema、唯一 stopped 记录、hash、policy 和
当前 Docker binding 完整校验。当 canonical paused 已不存在时，只能通过同一 authority root 的
`0600` 单链接 snapshot、唯一 `0400` 单链接终态 audit、无 archive metadata 以及当前容器恢复事实
来确认 completed authority。active/archive 混合、任一 partial 或容器漂移均拒绝。因此
A（active direct）→B（provenance）→restore A→C（新 SHA）可以安全开启 C 的新事务，同时 A、B
保持全程只读。

marker 不是 completed predecessor 的缓存替代物。marker-only、snapshot-only 和 reuse-local 每次
解析都必须重新读取 marker 所指的立即前驱，并复核 completed snapshot、唯一终态 audit、无残留
archive metadata 及容器 binding。前两种状态要求容器仍处于 predecessor 的恢复事实；reuse-local
允许容器已被当前事务暂停，但当前 active snapshot 必须与 predecessor 的恢复 binding 完全一致，
随后 paused ledger 还必须证明同一容器已完整进入 stopped 状态。任一旧 archive 篡改、当前容器
漂移或跨事务 binding 不连续均 fail closed，验证过程不得创建 metadata 或改写旧 release。

reuse-local 的 direct snapshot/paused 不是“文件存在即有效”。两者必须为当前 UID 所有的 `0600`
单链接普通文件；snapshot schema、容器 ID/名称、Compose 身份必须完整且唯一；paused 必须非空，
且 canonical 场景只允许唯一 `ocr-v6-amd` 的 `stopped` 记录。binding、snapshot SHA-256、原始
running 状态、restart policy neutralization 和当前 Docker exited binding 必须逐项一致。
`pending_stop`、`restoring`、`restored`、`not_stopped`、空 ledger、audit/archive metadata 混合及
任一身份/hash 漂移都不是可复用的 active transaction。`publish-provenance` 只允许 active
snapshot/paused authority；如果 paused 已归档为 completed audit，即使 completed authority 本身
可信，也必须拒绝发布新的 active provenance。

## 风险与权衡

- [真实 E2E 变慢且更容易受环境影响] → 单元测试继续覆盖算法细节，Harness 将 broker E2E 独立分层并输出诊断。
- [后台循环异常导致进程存活但不工作] → readiness 跟踪每个循环状态，TaskGroup 异常触发服务退出，由 Docker 重启。
- [视觉任务执行时间长] → 消息处理采用显式并发上限和幂等进度，不用短 Kafka poll timeout 持有整个任务。
- [Kafka 依赖下载或启动失败] → 固定镜像/客户端版本，保留镜像缓存和清晰的环境预检；没有 broker 证据不得勾选 E2E。
- [算子 wheel 与算法环境冲突] → wheel 只包含轻量共享客户端依赖，构建阶段执行导入和 ops contract 测试。
- [PostgreSQL 心跳写放大] → 只按配置周期写摘要或状态变化事件，Redis 保持高频 TTL。

## 迁移计划

1. 建立平台 `AGENTS.md`、Harness 和四服务部署骨架，记录当前不符合项。
2. 完成方案 C 里程碑 1：control 的真实 Repository、状态、事务 Outbox、注册审计和 Redis 容量。
3. 完成方案 C 里程碑 2：Kafka adapter、Publisher、Consumer、DAG、Dispatcher 和契约 Stub 调用。
4. 使用真实 PostgreSQL/Redis/Kafka 验证基础闭环、重启、重复消息、URGENT 和等待算子。
5. 等 PPT 契约冻结后接入 PPT/OCR/关键词，再接 ASR 管道。
6. 实现视觉组合器并接通 visual commands/events。
7. 接入清理、审计、全部指标、算子 wheel 和完整部署闭环。
8. 使用真实基础设施和契约算子替身跑五类完整场景，Harness 全部通过后再决定同步/归档。

上述 1-4 已形成方案 C 基础闭环和三卡部署基线。后续执行顺序由设计决策 12 取代旧计划中
“先接任一真实算子再逐步扩展”的宽泛表述；完整数据流和验收门槛见
`docs/superpowers/specs/2026-08-18-里程碑2B真实业务泳道与完整验收设计.md`。

回滚以服务为单位：A 未切流前保留现有旧链路；数据库变更只增加审计数据；关闭新 Worker 不删除 Outbox 和任务事实；不在回滚时删除 `/data/result`。

## 历史待确认问题及当前结论

- Kafka 客户端已经选择 `aiokafka` 0.14.x，并取得真实 Broker 证据。
- PPT 已冻结为共享路径、原子 manifest、容量续约和一次终态通知；回调进入既有
  `orchestrator-service`，不增加独立回调服务。
- 长视觉任务使用课程级 Kafka command/event；单帧和自适应轮次留在
  `vision-orchestrator-service` 内，不长时间占用 Kafka 消息处理边界。
- 八个算子镜像分别安装版本化 registry client wheel，已经取得隔离镜像导入证据。
