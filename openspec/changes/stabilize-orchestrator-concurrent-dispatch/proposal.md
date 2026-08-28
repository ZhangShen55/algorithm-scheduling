## 变更原因

2026-08-28 在 `192.168.29.11` 执行 16 并发、100 个 ASR 离线任务时，通用节点执行器对同一 `asr_offline` 能力并发执行全量状态恢复与任务聚合，触发 PostgreSQL `40P01 deadlock_detected`，继而使 `node_executor` 和 Orchestrator 其余后台循环全部停止；100 个已受理任务仅 21 个成功、1 个失败、78 个永久停滞。相同领取实现同时服务 `ppt_slice` 和 `ocr`，且各服务的容量租约续租均存在单次瞬时网络异常直接终止工作的风险，因此必须在继续里程碑 2B 极限 Campaign 前完成通用稳定性修复。

## 变更内容

- 重构 Orchestrator 的能力级并发领取：同一轮按唯一 capability 规划槽位，节点通过 PostgreSQL `FOR UPDATE SKIP LOCKED` 原子领取，禁止每个槽位并发执行全量 `resume_capability_nodes`、`defer_capability_nodes` 和能力级任务聚合。
- 让 `ASR_TRANSCRIPTION`、`PPT_SLICE` 和 `PPT_OCR` 在单能力积压、容量满载/恢复和多能力混合负载下兑现 `worker.node_concurrency`，同时保持任务优先级、整数状态、算子容量和 A 服务接口不变。
- 对 PostgreSQL `40P01`、`40001` 等明确可恢复事务错误实施有界重试、退避和指标；重试耗尽时保留节点可恢复状态，不得误写业务失败。
- 隔离单个领取槽位的瞬时异常；后台循环对可恢复基础设施异常持续重试，对不可恢复不变量错误执行可被 Docker 重启的非零进程退出，禁止再次形成 HTTP 仍存活但调度循环全部停止的僵尸容器。
- 为普通离线节点增加过期领取/运行状态恢复边界；PPT 异步节点继续使用既有 `operator_task_id`、manifest、回调和对账恢复，不重复启动切片任务。
- 统一增强 Orchestrator、Vision Orchestrator 和 Online Gateway 的容量租约续租：单次 `ReadError`、连接错误或远端协议瞬时错误在 TTL 安全窗口内有限重试；确认租约丢失或重试耗尽时使用可诊断且与业务幂等性匹配的受控终止/重排语义。
- 保留本次 `tast_asr_1`～`tast_asr_100` 的失败事实并写入 Harness；修复后使用全新任务 ID 在 `192.168.29.11` 重跑 ASR 16 并发 100 次，并补充 PPT、OCR、视觉、在线租约续租和混合任务回归。
- 复用 `192.168.29.11` 现有 BuildKit 缓存完成重建，不使用 `--no-cache` 或执行缓存 prune；重新 build/run 四平台容器时保持当前已运行的节点、课程、媒体、VBas、HTTP 连接和七算子并发参数原值，仅允许增加本变更明确设计的重试/恢复配置。新版本验证通过后按完整 ID 删除本次被替代的旧容器和旧镜像，但保留 BuildKit 缓存、基础镜像、当前镜像、未变更算子镜像、volume、模型和业务数据。
- 不改变 `POST /api/course-jobs`、查询接口、在线 HTTP/WebSocket 路径、请求/响应字段、七算子协议、默认端口或四服务边界。

## 能力范围

### 新增能力

- `concurrent-offline-node-dispatch`: 规定 ASR、PPT Slice、PPT OCR 的无死锁能力级并发领取、容量等待/恢复、优先级、公平性和单槽位异常隔离。
- `orchestrator-runtime-fault-recovery`: 规定 PostgreSQL 瞬时事务重试、后台循环监督、就绪/存活语义、进程重启以及普通节点崩溃恢复。
- `capacity-lease-renewal-resilience`: 规定离线、视觉和在线容量租约在瞬时续租异常、确认丢失、释放失败和 TTL 安全窗口下的一致行为。

### 修改能力

无。

## 影响范围

- 主要代码：`orchestrator_service/app/application/executor.py`、`dispatcher.py`、`infrastructure/runtime.py`、`control_client.py`，以及 `algorithm-scheduling-platform/packages/platform_common/repository.py`。
- 关联代码：`vision_orchestrator_service/app/infrastructure/capacity.py`、`online_gateway_service/app/infrastructure/capacity.py`、PPT 异步容量续租与普通节点恢复代码。
- 数据库：不改变 A 服务表面合同；如需索引、领取纪元或恢复字段，必须通过可回滚迁移并补充字段中文注释。不得用人工改库作为运行时修复。
- 配置：可能新增数据库事务重试、后台循环退避、普通节点恢复超时和租约续租重试配置；所有 TOML 字段必须带中文注释并提供安全默认值。
- 验证：新增真实 PostgreSQL 并发集成、运行时监督、重启恢复、租约故障注入、四离线任务类型混合回归，以及 `192.168.29.11` 的 ASR/PPT/OCR 真实发布验证。
- 现有变更：本变更是 `balance-operator-routing-by-live-load` 剩余远端验证和 `run-milestone-2b-extreme-load-campaign` 后续 Campaign 的前置阻断项；两者不得在本变更验证通过前标记完成。
