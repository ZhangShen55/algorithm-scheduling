## 1. 基线与失败证据

- [x] 1.1 记录本地分支、dirty/untracked 边界、当前 Git SHA 和 `192.168.29.11` 四平台/七算子镜像及容器完整 ID，禁止覆盖用户文件和既有 Campaign 证据。
- [x] 1.2 记录默认 BuildKit builder、构建缓存条目/容量、Docker 磁盘摘要和四平台现有镜像 ID；确认后续构建不使用 `--no-cache`，不执行 builder/buildx cache prune。
- [x] 1.3 从宿主机 TOML、Compose 展开、容器只读挂载和容器内解析四个层面发布设计文档所列平台/七算子并发参数基线，后续替换不得擅自降低或放大。
- [x] 1.4 将 2026-08-28 `tast_asr_1`～`tast_asr_100` 的提交耗时 0.322 秒、12 路实际下载、21 成功、1 失败、78 停滞、635.297 秒观察窗口、PostgreSQL `40P01`、Orchestrator readiness 503 和后台循环退出写入新的中文 Harness 场景，并标记为失败事实。
- [x] 1.5 保存受控 PostgreSQL 状态/原因汇总、Orchestrator `/ops/readiness`、三个 ASR 实例租约快照和 deadlock 日志摘要；证据不得包含完整媒体 URL、ASR 文本、请求正文或凭据。
- [x] 1.6 为现有调度器增加失败先行测试，使用真实 PostgreSQL 和 16 个同 capability 槽位稳定复现并发 `resume/defer/aggregate` 的锁竞争或证明当前实现会让一个 Repository 瞬时错误结束关键循环。
- [x] 1.7 参数化失败测试覆盖 `asr_offline`、`ppt_slice` 和工作项型 `ocr`，并补充 ASR/PPT/OCR 混合 capability 的槽位轮转基线。

## 2. 无死锁能力级并发领取

- [x] 2.1 重构 `NodeExecutor` 的槽位规划，使每轮先读取唯一 capability，再按轮转游标把 `worker.node_concurrency` 个槽位分配给能力；单一能力可以使用全部槽位，多能力不得饥饿。
- [x] 2.2 为普通租约型能力实现能力级 `reserve_many` 协调，隔离各槽位的租约申请、节点领取、上下文绑定和释放，禁止每槽位调用全量状态恢复与能力级聚合。
- [x] 2.3 修改 Repository 的原子领取 SQL，使节点可从状态 10 或 30 按 URGENT/NORMAL、`ready_at`、`id` 顺序通过 `FOR UPDATE SKIP LOCKED` 进入状态 40，并保持 claim token、attempt 和时间字段正确。
- [x] 2.4 对取得租约但没有节点、上下文绑定失败、节点领取异常和协程取消补充精确释放路径，验证同一租约只产生一个有效归属且最终收敛。
- [x] 2.5 将容量不足处理收敛为同一 capability 每轮最多一次 `10 -> 30` 协调；事务提交后只按返回的受影响任务 ID 稳定聚合，禁止在批量节点锁内扫描全部任务类型。
- [x] 2.6 为能力级协调增加跨进程安全边界；如保留批量等待更新，使用 capability 级 PostgreSQL advisory transaction lock 或等价原子方案，不能只依赖进程内 `asyncio.Lock`。
- [x] 2.7 保持 `ocr` 外层节点不占实例租约，每张 `ppt_image_id` 继续独立租约；OCR 暂时无容量时保留已完成单图并有界重排未完成工作项，不得把整个 PPT 任务立即写成 70。
- [x] 2.8 运行 ASR、PPT Slice、PPT OCR 的调度单元测试，验证同节点不重复领取、容量不超卖、优先级保持、单能力 16 槽位、多能力轮转和单槽位异常隔离。
- [x] 2.9 将 `NodeExecutor` 改为受 `worker.node_concurrency` 限制的在途任务池，任一槽位释放后立即补位；最后一个在途节点完成后禁止额外申请整轮空租约，取消时等待全部在途租约精确释放。

## 3. PostgreSQL 瞬时事务重试

- [x] 3.1 在公共 Repository 层实现仅匹配 PostgreSQL SQLSTATE `40P01` 和 `40001` 的有限事务重试器，每次尝试使用新事务并采用配置化指数退避、随机抖动和最大延迟。
- [x] 3.2 为领取、能力等待协调和任务聚合接入事务重试；重试耗尽抛出包含 operation、sqlstate、attempts 的类型化 `TransientInfrastructureError`，不得写入节点业务失败。
- [x] 3.3 明确排除认证失败、迁移缺失、SQL 编程错误、非法状态迁移和数据不变量错误，验证它们不会被无限重试或伪装成瞬时错误。
- [x] 3.4 增加真实 PostgreSQL 故障注入测试，覆盖首次 `40P01` 后成功、连续死锁耗尽、`40001`、非重试 SQLSTATE，以及重试过程中租约和节点状态精确收敛。
- [x] 3.5 增加脱敏结构化日志和 `operation/sqlstate/outcome` 维度的事务重试指标，不记录 SQL 参数中的媒体地址、文本结果或请求正文。

## 4. 后台循环监督与普通节点恢复

- [x] 4.1 重构 Orchestrator runtime 错误分类：单任务业务错误只影响节点；可恢复基础设施错误退避后继续；不可恢复不变量错误进入 fatal 处置。
- [x] 4.2 修改单槽位和能力批次的异常收敛，确保一个瞬时错误不取消同轮其他已领取节点，并确保已取得租约在异常/取消路径精确释放。
- [x] 4.3 修改关键循环监督，禁止再次出现仅设置全局 `stop_event` 后主进程继续存活的僵尸状态；fatal 时完成受控关闭并让容器主进程退出，以触发现有 `restart: unless-stopped`。
- [x] 4.4 扩展 `/ops/readiness`，按关键循环暴露 running/degraded/fatal、最近瞬时错误类型、重试次数和恢复时间；`/health` 继续只表示进程存活。
- [x] 4.5 设计并实现普通 ASR/OCR 节点的过期领取恢复，只有领取者失效、领取超时且节点归属租约不存在或明确过期时才把状态 40/50 恢复为 30，并保留 attempt 和中文原因。
- [x] 4.6 将 `PPT_SLICE` 排除在普通恢复之外，验证其继续按确定性 `operator_task_id`、持久 progress、manifest、回调和对账恢复，不重复创建后台切片任务。
- [x] 4.7 增加服务 lifespan 测试，覆盖瞬时数据库错误后循环继续、fatal 后进程退出意图、一个循环异常不形成永久存活停摆、重启后状态 40/50 普通节点恢复和 PPT 异步节点恢复。

## 5. 容量租约续租韧性

- [x] 5.1 抽取或统一 Orchestrator、Vision Orchestrator、Online Gateway 和 PPT 异步 keeper 的租约续租错误分类，区分结果不确定的瞬时网络错误、确认租约丢失、协议/身份错误和取消。
- [x] 5.2 在最近一次确认 `expires_at` 的安全窗口内，对同一 `lease_id` 实现配置化有限续租重试；禁止越过安全余量继续使用租约或同时申请第二个实例租约。
- [x] 5.3 修复普通 ASR 节点的单次 `ReadError` 终态行为：首次瞬时异常恢复时继续原调用；最终无法续租时取消调用、幂等释放并把可幂等节点放回状态 30，而不是直接状态 70。
- [x] 5.4 修复 OCR 工作项续租失败收敛，保留已完成 `ppt_image_id`，仅用稳定标识重排未完成项。
- [x] 5.5 保持 PPT 异步任务在续租最终失败后进入 manifest/终态对账，禁止重复提交同一 `operator_task_id`。
- [x] 5.6 修复 Vision VBas 批次和 Online Gateway 长请求/实时 ASR 会话的单次续租异常；一个租约最终失败只能影响对应批次、请求或会话，不得停止服务或其他工作。
- [x] 5.7 让租约释放 404 成为幂等成功；瞬时释放失败只记录指标并依赖有限重试或 TTL 回收，不得逆转已经持久化的业务终态。
- [x] 5.8 增加跨服务租约测试，覆盖首次 `ReadError` 后恢复、响应丢失后的同 lease_id 重试、明确 404、TTL 安全余量耗尽、释放响应丢失、协程取消和日志脱敏。

## 6. 配置、数据库与文档

- [x] 6.1 在受影响服务 `config.toml` 中增加带中文注释的 PostgreSQL 重试、后台退避、普通节点恢复超时、租约续租尝试/退避/安全余量配置，并提供安全默认值和交叉字段校验。
- [x] 6.2 更新各服务配置模型、环境变量覆盖、配置测试和 README，明确 `worker.node_concurrency` 是节点槽位上限，不是独立下载并发或真实 GPU 推理并发。
- [x] 6.3 如实现需要新增领取纪元、执行心跳或索引，创建可回滚 PostgreSQL migration，并为每个表/字段/索引补充中文 COMMENT；不得通过远端人工 DDL 代替迁移。（本次复用既有领取字段，无需新增 migration。）
- [x] 6.4 更新 `algorithm-scheduling-platform/AGENTS.md` 仅当长期运行边界、配置、必需验证或恢复合同发生变化；普通实施记录写入 OpenSpec、Harness、README 或设计文档，不把 AGENTS.md 当变更日志。
- [x] 6.5 更新部署手册，补充 Orchestrator unhealthy、关键循环退出、PostgreSQL deadlock、租约续租失败、普通节点恢复和精确回滚的查询与处置步骤。
- [x] 6.6 明确本变更不调整媒体下载与算子租约先后顺序；如发现需要 `max_concurrent_downloads` 或 FFmpeg 独立并发，记录为后续独立变更而不偷偷扩大本次实现。

## 7. 本地与集成验证

- [x] 7.1 对受影响公共包和三个平台服务运行 Ruff、strict Mypy、`compileall`、应用导入和配置解析，保持四服务独立 `app.main:app` 启动合同。
- [x] 7.2 运行 Orchestrator 全量测试以及公共 Repository、状态机、PPT 回调/对账、OCR 工作项、视觉 Kafka 边界和在线网关租约回归。
- [x] 7.3 使用真实 PostgreSQL 执行至少 100 个同 capability 节点、16 槽位、容量满载/恢复循环，断言无调度 SQL deadlock、无重复 claim、无错误终态和无遗留租约。
- [x] 7.4 使用真实 Redis 验证公共最少负载路由、共享容量、续租、释放和 TTL 回收未回退；首次等负载租约继续覆盖不同健康实例。
- [x] 7.5 使用真实 Kafka 和服务 lifespan 验证 fatal 重启/恢复、Outbox 至少一次交付、课程命令重放、视觉事件消费和 PPT 终态对账。
- [x] 7.6 运行 OpenSpec strict 校验、变更 diff 检查和 Harness 验证命令，确保全部新增规格均有可追踪测试证据。

## 8. 远端发布与真实重跑

- [x] 8.1 形成并推送一个完整候选 Git SHA；记录 `192.168.29.11` 替换前的平台容器/镜像完整 ID、revision、readiness、注册、租约、Kafka lag、PostgreSQL 状态和磁盘基线。
- [x] 8.2 聚焦修复验证阶段使用现有 BuildKit 缓存重建并替换四个平台镜像，确保四个平台绑定同一候选 SHA；不得使用 `--no-cache` 或执行 builder/buildx cache prune。七算子协议和代码未变化时不为该聚焦验证无条件重建七算子镜像；记录旧/新容器和镜像完整 ID/digest 并在门禁完成前保留可精确回滚的旧平台镜像。
- [x] 8.3 重新创建容器后逐项比较并发基线，确认 `node_concurrency=16`、Vision `16/8/6/8/3`、Online HTTP `2048/512`、三个 ASR Offline 实例各容量 4、VBas 单实例 `1/1/0` 及其余算子值均未改变。
- [x] 8.4 验证平台和基础设施容器健康、21/21 算子注册、18 个 GPU 进程、3 个 CPU PPT 实例、Control/Orchestrator readiness、共享目录和 A 服务北向端口。
- [x] 8.5 使用全新任务前缀执行真实 ASR 16 并发、100 次处理，记录提交耗时、实际流水线并发、下载/FFmpeg/ASR 分段耗时、成功率、P50/P95、总耗时、三实例租约分布、GPU 时序和 PostgreSQL deadlock 计数；100 个任务必须全部合法终态。
- [x] 8.6 执行 PPT Slice/PPT OCR 单泳道积压和 ASR/PPT/OCR 混合并发，验证三种通用节点能力均无同型死锁且一种能力等待不阻断其他能力。
- [x] 8.7 执行教师/学生视觉连带回归，验证通用节点瞬时错误不会永久停止视觉命令发布、结果消费和任务聚合。
- [x] 8.8 对 Vision VBas 和 Online Gateway 实时 ASR/长请求执行租约续租故障注入，证明首次瞬时异常恢复且最终失败只影响单批次/单会话。
- [x] 8.9 验证全部测试结束后节点、Outbox、Kafka lag、活跃租约、算子 inflight、临时 `/data/course` 和结果 `/data/result` 按合同收敛；不得删除结果、数据库 volume 或历史失败证据。
- [x] 8.10 全部门禁通过后，按账本完整 ID 删除本次被替代的旧容器和旧镜像；删除前验证目标不属于当前发布或其他运行容器，清理后重验当前 revision、健康、注册、GPU/CPU 实例、volume、`/data/result` 和 BuildKit 缓存仍完整。
- [x] 8.11 任一门禁失败时停止新负载、保存中文 Harness 和 OpenSpec 任务状态，保留旧回滚镜像并按完整旧镜像 ID 精确回滚平台服务；无论成功失败都不得用重启掩盖 deadlock、把部分成功写成通过或执行宽泛 prune。（本候选全部业务门禁通过，回滚分支未触发；既有失败候选证据和回滚镜像按合同保留。）

## 9. 变更联动与完成门禁

- [x] 9.1 在 `balance-operator-routing-by-live-load` 的剩余任务与 Harness 中引用本变更验证结果，确认公共 Redis 最少负载路由未回退后才能继续其远端均衡用例。（联动证据已写入本变更 Harness；目标变更的 dirty `tasks.md` 保持不覆盖，后续由其自身提交引用。）
- [x] 9.2 在 `run-milestone-2b-extreme-load-campaign` 中保留旧 ASR 压测失败 attempt；进入 canonical Campaign 前按既有合同构建并核验同一最终 SHA 的四平台和七算子镜像，再使用新 seed、Campaign ID 和 write-once attempt 从规定阶段重新执行，不得从失败用例之后续写为通过。（旧 attempt 未改写，后续重跑约束已写入本变更 Harness，不表示 Campaign 已完成。）
- [x] 9.3 汇总 ASR、PPT、OCR、视觉和在线租约验证层级、通过/失败/未验证项，更新中文 Harness、部署手册和相关设计文档。（Harness 已汇总六层证据；部署手册与设计文档中既有并发调度、死锁、续租、恢复及精确回滚边界继续有效，本轮未改变部署合同。）
- [ ] 9.4 完成 `openspec validate stabilize-orchestrator-concurrent-dispatch --strict`、代码审查、Git diff 范围审计和用户确认后，方可将本变更标记完成并归档。
