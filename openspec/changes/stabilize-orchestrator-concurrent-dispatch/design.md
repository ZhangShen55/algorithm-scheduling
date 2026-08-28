## 背景

`orchestrator-service` 当前通过 `NodeExecutor.run_once()` 生成 `worker.node_concurrency` 个槽位。能力列表只有 `asr_offline` 时，16 个槽位会同时进入 `LeaseAwareDispatcher.reserve_next("asr_offline")`。每个槽位在领取单个节点之前都会执行以下全量操作：

```text
申请一个算子容量租约
→ resume_capability_nodes(capability)
→ aggregate_capability_task_types(capability)
→ claim_ready_node(capability, worker_id)
```

当算子总容量 12 小于节点槽位 16 时，部分槽位还会同时执行 `defer_capability_nodes()` 和全量聚合。`resume/defer` 批量更新相同 `task_nodes`，聚合又按任务类型锁定 `course_task_types` 和所属节点；并发事务的锁顺序不一致，最终触发 PostgreSQL `40P01`。

2026-08-28 的真实 ASR 验证使用 `tast_asr_1`～`tast_asr_100`、HTTP 提交并发 16、三个 `asr_offline` 实例且每实例 `declared_capacity=4`。100 次提交在 0.322 秒内全部受理，运行中实测 12 路并行下载；随后 PostgreSQL 在 15:04:03 连续报告死锁，`node_executor` 退出并设置全局 `stop_event`。最终只有 21 个成功、1 个因租约续租 `ReadError` 失败、78 个停在状态 10；容器保持进程存活但 `/ops/readiness=503`，Docker 不会仅因 unhealthy 执行 `restart: unless-stopped`。

通用节点执行器同时处理 `ASR_TRANSCRIPTION`、`PPT_SLICE` 和 `PPT_OCR`。教师/学生视觉节点由独立视觉协调循环处理，不直接执行上述能力级批量恢复，但会在全局 `stop_event` 后失去命令发布和结果消费。Online Gateway 不访问课程 PostgreSQL，因此没有同型死锁；但 Orchestrator、Vision Orchestrator 和 Online Gateway 的普通租约 keeper 均会因单次续租网络异常终止当前工作。

## 目标 / 非目标

**目标：**

- 单一能力积压可以安全使用全部 `worker.node_concurrency` 槽位，不产生并发批量状态恢复死锁。
- ASR、PPT Slice 和 PPT OCR 在容量耗尽、容量恢复和多能力混合场景下保持可恢复的节点状态、优先级和公平性。
- PostgreSQL 明确可恢复事务错误不会变成业务失败，也不会永久停止后台调度。
- 单槽位异常不取消同轮其他槽位；不可恢复运行时错误不再形成僵尸容器。
- 普通离线节点在进程异常退出后可以按租约和领取时效安全恢复。
- 三个调用服务和 PPT 异步协调器对容量租约瞬时续租错误使用一致的 TTL 安全窗口和有限重试语义。
- 用真实 PostgreSQL、真实服务生命周期和 `192.168.29.11` 三 GPU 拓扑证明修复，而不是只依赖 Fake Repository。

**非目标：**

- 不改变 A 服务接口、任务字段、整数状态、任务类型或查询结果结构。
- 不改变七算子的 HTTP/WebSocket 请求响应合同和端口。
- 不在本变更把媒体下载移动到 ASR/PPT 容量租约之前；下载并发与租约边界优化另行评审。
- 不改变 ASR、OCR、PPT Slice、VBas 模型算法或输出质量。
- 不引入 Kubernetes、分布式工作流引擎、新消息中间件或新的外部数据库。
- 不把数据库认证失败、迁移缺失、状态机非法或数据不变量损坏伪装成可重试瞬时错误。

## 技术决策

### 1. 按唯一 capability 规划槽位，禁止每槽位全量协调

`NodeExecutor` 每轮先取得唯一 capability 列表，再按现有轮转游标将 `node_concurrency` 个槽位分配给这些能力。同一 capability 的槽位由一个能力级批次协调器管理，不再让每个槽位独立执行全量恢复、等待和聚合。

```text
唯一 capability 列表
        │
        ▼
按轮转游标分配 N 个槽位
        │
        ├── asr_offline: reserve_many(12)
        ├── ppt_slice:   reserve_many(2)
        └── ocr:         claim_many(2)
```

单一 capability 仍可使用全部 16 个槽位；多 capability 继续轮转，不能退回“每种能力每轮只能领取一个节点”的旧实现。

备选方案“只给 `resume_capability_nodes` 增加 `asyncio.Lock`”仅对单进程有效，未来增加第二个 Orchestrator 实例仍会竞争；“只增加 PostgreSQL 死锁重试”会保留 16 次重复全量更新并可能形成高锁竞争或活锁。因此二者只能作为防御，不作为主要修复。

### 2. 容量恢复后直接从状态 10/30 原子领取

普通节点获得容量租约后，Repository 使用一个短事务从状态 `10` 或 `30` 中选择一个候选：

```sql
SELECT node.id
FROM task_nodes AS node
JOIN course_task_types AS task_type
  ON task_type.id = node.course_task_type_id
WHERE node.status IN (10, 30)
  AND node.required_capability = :capability
  AND task_type.status IN (10, 20, 30, 40, 50)
ORDER BY priority, ready_at, node.id
FOR UPDATE OF node SKIP LOCKED
LIMIT 1
```

同一事务把节点写为 `40` 并返回节点事实。这样容量恢复时无需先把该能力的全部状态 30 节点批量改为 10。取得租约但没有节点时立即幂等释放租约。

`ocr` 继续是工作项能力：外层 `PPT_OCR` 节点不占 OCR 实例租约，但同样通过状态 `10/30` 的原子领取进入执行；每张 `ppt_image_id` 仍单独申请、续租和释放 OCR 租约。

### 3. 容量不足只执行一次能力级等待协调

同一轮部分或全部槽位返回 `CapacityUnavailableError` 时，能力级协调器最多执行一次 `10 -> 30` 等待更新。该事务与成功领取阶段分离，使用稳定锁顺序；如保留批量更新，则使用 PostgreSQL capability 级事务 advisory lock 防止多个 Orchestrator 进程同时协调同一能力。

等待更新返回受影响的 `course_task_type_id`，事务提交后再按 ID 升序逐个聚合。禁止在持有批量节点更新锁时进入任务类型聚合，也禁止每个失败槽位扫描并聚合该能力的全部任务。

OCR 单图临时无容量不得让整个 `PPT_OCR` 节点进入状态 70。未完成的稳定 `ppt_image_id` 工作项保留等待事实，在有界退避后重新申请租约；已经完成的单图结果继续复用。

### 4. 数据库重试只覆盖明确瞬时 SQLSTATE

公共 Repository 为幂等、事务完整的领取/等待协调/聚合操作提供有限重试器。默认建议：

```toml
[postgres_retry]
max_attempts = 5
base_delay_seconds = 0.05
max_delay_seconds = 1.0
```

仅 `40P01` 和 `40001` 自动重试；每次重试必须开启全新事务，并使用指数退避和随机抖动。`08000` 类连接问题由运行时基础设施重连策略处理，不与事务死锁重试无限嵌套。

重试耗尽抛出带 `operation`、`sqlstate` 和 `attempts` 的 `TransientInfrastructureError`。该错误不得进入 `NodeExecutor` 的业务失败分支，不得把节点写成 70。日志和指标保留受控上下文，不记录媒体 URL、ASR/OCR 文本或请求体。

备选方案“捕获全部 `OperationalError`”会掩盖认证、迁移和 SQL 编程错误，因此不采用。

### 5. 单槽位隔离与后台循环监督分层

能力批次中的每个槽位独立收敛：领取前异常时释放已取得租约并返回可重试结果；领取后业务执行异常仍按现有节点状态机落入相应终态或等待状态。一个槽位的瞬时基础设施异常不得取消同轮已经开始的其他槽位。

运行时错误分三类：

| 分类 | 行为 |
| --- | --- |
| 单任务业务错误 | 只更新该节点，循环继续 |
| 可恢复基础设施错误 | 有界重试；持续失败时 readiness 降级并退避，循环保持可恢复 |
| 不变量/协议/迁移错误 | 记录 fatal 原因，执行受控关闭并让容器主进程退出，由 Docker 重启 |

`_record_loop_exit` 不再把异常转换为“主进程继续存活、所有循环停止”的永久状态。关键循环意外退出后，要么由 supervisor 重启该循环，要么使容器进程退出；选择由错误分类决定。`/health` 继续表示进程存活，`/ops/readiness` 必须暴露每个循环、最后瞬时错误、重试次数和 fatal 原因。

### 6. 普通节点使用领取纪元和过期时间恢复

普通 ASR/OCR 节点需要补充启动恢复能力。恢复器只处理满足全部条件的状态 `40/50` 节点：

- `claimed_at` 或最近执行心跳超过配置化恢复阈值；
- 原 `claimed_by` 对应执行器已经不存在；
- Control 中不存在该节点归属的有效容量租约，或租约已经明确过期；
- 节点不是由 PPT 异步协调器持有的 `PPT_SLICE`。

符合条件的普通节点进入状态 30 并保留 `attempt` 和诊断原因，之后由正常领取路径重试。PPT Slice 继续使用确定性 `operator_task_id`、持久 progress、manifest、终态回调和对账恢复；不得按普通节点规则重复提交切片任务。

现有 78 个状态 10 的 `tast_asr_*` 可以在新版本启动后直接继续。状态 70 的 `tast_asr_16` 保留历史失败事实，不由启动恢复器篡改；验证使用新任务 ID 或经未来明确的运维补跑动作处理。

### 7. 租约续租在 TTL 安全窗口内有限重试

Orchestrator、Vision Orchestrator、Online Gateway 和 PPT 异步 keeper 使用同一错误分类：

- `httpx.NetworkError`、`RemoteProtocolError` 和可恢复超时属于续租结果不确定，继续对相同 `lease_id` 有限重试；
- 404/明确租约不存在属于确定丢失，不继续使用该租约；
- 认证、协议结构和 lease_id 不一致属于不可恢复错误；
- 重试必须在最近一次已确认 `expires_at` 的安全余量之前结束，不能在确认过期后继续把实例视为独占。

建议配置化总尝试次数、基础退避和安全余量，默认单次 `ReadError` 不取消 ASR、VBas 批次或 ASR WebSocket。重试恢复时刷新新的 `expires_at` 并继续原工作；确认丢失或安全窗口耗尽时取消当前调用、幂等释放，并按工作类型处理：普通离线幂等节点回到状态 30，OCR 保留已完成单图并重排未完成项，在线请求/会话返回可诊断错误但不得影响其他请求，PPT 异步任务进入既有终态对账而不重复提交。

释放接口保持幂等：404 表示租约已经不存在，可视为释放完成；瞬时释放失败记录指标并由 TTL 最终回收，不得逆转已经持久化的业务终态。

### 8. 媒体下载与算子租约边界不在本变更调整

当前 ASR 顺序仍是先申请算子租约，再下载视频、提取 WAV、调用 ASR。本变更修复死锁后，三个实例合计容量 12 时仍最多形成 12 条完整 ASR 流水线，而不是 16 路独立媒体准备。

将下载/FFmpeg 移到算子租约之前，需要新增 `max_concurrent_downloads`、`max_concurrent_audio_extractions` 和中间产物恢复策略，属于独立性能设计。本变更的验收报告必须明确这一边界，不能把 12 条流水线误写为 12 条同时 GPU 推理。

### 9. 重建复用现有缓存并冻结当前并发基线

`192.168.29.11` 当前默认 BuildKit builder 正常运行；Docker 视图存在 849 条、约 85.73 GiB 本地 Build Cache，buildx 视图存在约 195.2 GiB 可复用记录。聚焦修复构建必须保留并复用这些缓存，不使用 `--no-cache`，不执行 `docker builder prune`、`docker buildx prune` 或宽泛镜像清理；构建空间确实不足时先报告精确占用和待删除目标，再按已批准范围处理。

四平台和七算子配置均由宿主机 Git 工作树中的 `config.toml` 只读挂载到容器。重新 build/run 时必须保持以下运行基线，不能为了绕开死锁擅自降低并发：

| 组件 | 当前并发基线 |
| --- | --- |
| Control Service | `service.workers=1`、`postgres.pool_size=10`、`redis.max_connections=50` |
| Orchestrator Service | `service.workers=1`、`worker.node_concurrency=16`、`outbox.batch_size=20`、`ppt.ocr_batch_size=8`、`ppt.ocr_max_concurrency=2` |
| Vision Orchestrator Service | `service.workers=1`、`worker.concurrency=16`、`scan.batch_size=8`、`media.max_concurrent_processes=6`、`vbas.max_batch_size=8`、`vbas.max_concurrency=3` |
| Online Gateway Service | `service.workers=1`、`http.max_connections=2048`、`http.max_keepalive_connections=512` |
| ASR Offline 单实例 | `UVICORN_WORKERS=1`、`concurrency=5`、`platform.max_concurrent_requests=4` |
| ASR Online 单实例 | `UVICORN_WORKERS=1`、`platform.max_concurrent_requests=10` |
| FaceRec 单实例 | `UVICORN_WORKERS=1`、`platform.max_concurrent_requests=128`、`threading.max_workers=2` |
| OCR 单实例 | `UVICORN_WORKERS=1`、`platform.max_concurrent_requests=256`、`ocr.recognition_batch_size=1`、`ocr.max_concurrency=1` |
| PPT Slice 单实例 | `UVICORN_WORKERS=1`、`platform.max_concurrent_requests=10`、`task.max_queue_size=25` |
| ScreenDet 单实例 | `UVICORN_WORKERS=1`、`platform.max_concurrent_requests=128`、`screen_detection.max_batch_size=16` |
| VBas 单实例 | `WORKERS_PER_INSTANCE=1`、`platform.max_concurrent_requests=1`、`TIAS.MaxConcurrentBatches=1`、`TIAS.MaxQueueSize=0` |

本变更允许新增 PostgreSQL 重试、循环退避、领取恢复和租约续租韧性字段，但这些字段不得覆盖或隐式改变上表并发值。部署前后必须同时比较宿主机配置摘要、Compose 展开结果、容器挂载源/目标和容器内实际解析值。

镜像和容器采用“构建复用缓存、验证期间保留回滚、通过后精确清理”的生命周期：

1. 替换前记录本次范围内旧容器完整 ID、旧镜像完整 ID/digest、Compose project/service 身份和 revision。
2. 使用现有 BuildKit 缓存构建新镜像；新容器启动后刷新当前容器/镜像账本，不覆盖旧账本。
3. 新版本完成健康、并发配置、注册、租约、真实业务和回滚门禁前，旧镜像必须保留；旧容器如已被 Compose 替换，可保持 stopped 作为精确账本目标，不得提前宽泛清理。
4. 全部门禁通过后，只按账本完整 ID 删除本次被替代的旧容器和旧镜像；删除前再次验证目标不是当前容器/镜像、不是其他运行容器依赖的镜像。
5. 清理后重新验证当前容器、镜像 revision、BuildKit 缓存、21/21 注册、GPU/CPU 实例、volume 和结果目录。

清理不得执行宽泛 `docker system prune`、`docker image prune -a`、`docker container prune` 或 builder/buildx prune；不得删除 CUDA/Python 等基础镜像、当前发布镜像、未变更算子镜像、BuildKit 缓存、PostgreSQL/Redis/Kafka/MongoDB volume、模型、`/data/result`、Git 或历史报告。验证失败时不删除旧回滚镜像，而是停止新负载、保存证据并按旧完整 ID 回滚。

## 风险与权衡

- **风险：直接领取状态 30 会改变运维观察到的短暂状态序列。** → 保留整数状态和最终语义；测试只允许 `30 -> 40 -> 50` 的合法推进，不改变北向字典结构。
- **风险：能力级批量协调降低每槽位完全独立性。** → 协调只覆盖短事务，真实节点执行仍完全并行；换取稳定锁顺序和显著更低的数据库扫描量。
- **风险：事务重试放大数据库压力。** → 先消除重复全量更新，重试仅作为低频防御，并记录 SQLSTATE 和次数门禁。
- **风险：普通节点恢复可能与仍在运行的算子重复执行。** → 同时核对领取时效和 Control 有效租约；ASR/OCR 只按幂等边界重试，PPT Slice 排除在普通恢复之外。
- **风险：fatal 时让进程退出造成短暂全服务重启。** → 比永久 unhealthy 僵尸状态可恢复；Kafka 至少一次交付、Outbox、节点幂等和启动恢复负责重新推进。
- **风险：租约续租重试过久导致容量超卖。** → 以最近确认 `expires_at` 和安全余量为硬边界，过界立即停止使用租约。
- **风险：本变更与 `balance-operator-routing-by-live-load` 同时修改租约调用。** → 先基于当前已实现的公共最少负载选择完成本变更；不得回退 Redis 路由算法，合并前运行公共注册表与跨服务租约回归。

## 迁移计划

1. 冻结本次 ASR 100 任务的 PostgreSQL、Orchestrator readiness、deadlock 日志、租约快照和成功/失败/停滞统计，写入中文 Harness；不得把 21/100 改写为通过。
2. 先以真实 PostgreSQL 失败测试复现单能力 16 槽位的死锁，再实现能力级批次领取、状态 10/30 原子 claim 和单次等待协调。
3. 增加 SQLSTATE 重试、单槽位隔离、循环 supervisor、普通节点恢复和三服务租约续租故障注入测试。
4. 完成静态、单元、真实 PostgreSQL、Redis、Kafka、服务 lifespan 和租约契约验证；同步配置中文注释、README、数据库字段注释、部署手册和 Harness。
5. 形成一个完整 Git SHA；聚焦修复验证阶段使用现有 BuildKit 缓存重建四个平台镜像并保持同一 revision，不使用 `--no-cache` 或执行缓存 prune。七算子协议未变化时不为聚焦验证无条件重建算子镜像。部署前记录旧平台镜像和容器完整 ID以及上表并发配置摘要；进入 canonical 里程碑 2B Campaign 前仍须按既有合同让四平台和七算子全部绑定同一最终 SHA。
6. 在 `192.168.29.11` 替换平台服务并验证 29 个既有容器拓扑、21/21 注册、readiness 和三 GPU 基线。失败时按旧镜像精确回滚，不删除数据库任务、`/data/result`、模型、历史证据或回滚镜像。
7. 使用全新前缀执行 ASR 16 并发 100 次，再执行 PPT Slice/PPT OCR 单泳道、ASR/PPT/OCR 混合任务、教师/学生视觉连带回归和在线长租约故障测试。
8. 全部门禁通过后，按完整账本 ID 删除本次被替代的旧容器和旧镜像，保留构建缓存并重验当前拓扑；随后恢复 `balance-operator-routing-by-live-load` 剩余验证，并用新 seed/Campaign ID/write-once attempt 从规定阶段重启 `run-milestone-2b-extreme-load-campaign`。

## 回滚计划

- 代码或运行门禁失败时停止提交新负载，保留新版本失败证据和数据库任务事实。
- 使用替换前记录的完整镜像 ID 恢复四平台旧容器；不得执行 `down -v`、删除 PostgreSQL/Redis/Kafka volume、删除 `/data/result` 或清理历史 release。
- 已进入新状态的普通任务通过旧版本可识别的整数状态保留；如旧版本不能安全推进状态 30/40/50 节点，则保持服务停止并通过只读 SQL 报告阻断，不执行未记录的人工状态篡改。

## 待确认问题

无阻断性待确认问题。媒体准备与算子租约边界、生产环境 `node_concurrency` 最终值和跨多 Orchestrator 实例扩容不在本变更中定型，但实现不得阻止未来通过 capability advisory lock 和原子领取扩展到多实例。
