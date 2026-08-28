# Orchestrator 并发调度稳定化验证记录（2026-08-28）

## 变更与保护边界

- OpenSpec：`stabilize-orchestrator-concurrent-dispatch`。
- 本地分支：`codex/milestone-2b-three-gpu-deployment`。
- 失败基线 Git SHA：`d19e5e46b9cb0c78d775727e1cf33a75a4321df8`；目标机 Git SHA 与本地一致。
- 目标机：`192.168.29.11`。本记录不包含登录凭据、完整媒体 URL、请求正文、ASR/OCR 文本或 embedding。
- apply 前工作区已有用户改动：`docs/A服务对接指南.md`、
  `openspec/changes/balance-operator-routing-by-live-load/tasks.md`、`text_analysis/README.md`，以及多个未跟踪设计文档、
  Docker README、既有路由 Harness 和 `5{n++}`。本变更不得覆盖、清理或顺带提交这些文件。
- `text_analysis/` 不属于调度平台，本变更不修改、构建、注册、路由或部署它。

## 失败事实（写后不改）

2026-08-28 使用 16 路 HTTP 提交并发创建 `tast_asr_1`～`tast_asr_100`：

| 项目 | 失败基线 |
| --- | ---: |
| 100 次提交耗时 | 0.322 秒 |
| 实际完整 ASR 流水线并行数 | 12 |
| 成功终态 | 21 |
| 失败终态 | 1（`tast_asr_16`） |
| 停滞 | 78 |
| 首次提交至最后成功观察窗口 | 635.297 秒 |
| Orchestrator readiness | HTTP 503 |
| PostgreSQL 错误 | `40P01 deadlock_detected` |

受控 PostgreSQL 查询在 apply 开始时仍返回任务类型与节点相同分布：状态 10 为 78、状态 60
为 21、状态 70 为 1。`tast_asr_16` 的状态 70 是历史失败证据，不由恢复器重写；修复验证必须
使用全新任务 ID。

`/ops/readiness` 显示 `node_executor` 在并发执行 `resume_capability_nodes(asr_offline)` 时发生
死锁，其余 `outbox_publisher`、`course_consumer`、`visual_dispatcher`、
`visual_event_consumer`、`ppt_reconcile` 均已停止。进程和 HTTP `/health` 仍存活，容器为
`running/unhealthy`，证明旧实现形成了 Docker 不会自动重启的僵尸状态。

同一观察窗 PostgreSQL 日志出现 3 条 `deadlock detected`。三个 ASR Offline 实例的受控租约
快照均为 `active_lease_count=0`、`reported_inflight=0`、`attribution_difference=0`；失败节点日志
确认单次 `httpx.ReadError` 被旧实现转换为 `LeaseRenewalError` 和节点终态失败。

## 替换前平台容器与镜像账本

| 服务 | 完整容器 ID | 完整镜像 ID | 状态 |
| --- | --- | --- | --- |
| control-service | `733e49d9b5cccc8f2867c209512daf7d682f6bb1e5f900153dc0dcccbea11674` | `sha256:786c22f01532059d0654e5fc35931da4d4d9cd99077b2ab3a449481e442a282f` | healthy |
| orchestrator-service | `c41ded19c7571c306b03f3e324e0e22da4cf80652c1e21b9a116b0a1b8fd45cc` | `sha256:05be35ab8e45237a284f1cf02c456237d730bc2e3170b1f6d02c6a8ccfbf2c4f` | unhealthy |
| vision-orchestrator-service | `1c52d67b9c625c541eefce3491153ea863d5d989326e32d1ddf246026c0b0d65` | `sha256:21335aadd71aa156955374801d2b967c3c8f45e4dbf42764cacef26bb6aa780d` | healthy |
| online-gateway-service | `d313c79fb58942183936d3090a2ae25b0ef7bf1344d105833dd75e3af60e52d1` | `sha256:fc874d8c3ba6127a1f61eca6eb0c353423d89f1e9390c42d12e14283fa80a436` | healthy |

七类算子当前 21 个实例的完整容器 ID 如下；聚焦修复不改变七算子代码时不得无条件重建它们：

| 算子实例 | 完整容器 ID |
| --- | --- |
| asr-offline-gpu0 | `1ab28f91a3441d47f93ae2a132ce993d7488101166cb0ad82bf14766c7666cf2` |
| asr-offline-gpu1 | `27a10a25572176fd62117457de06e4a757ecbe6c94190e68cc4708ec3e402bed` |
| asr-offline-gpu2 | `9e3cb7de521b46a4c11ad98e9509087a9b86681d4b152eec4f2ed9c353151870` |
| asr-online-gpu0 | `7f59b03b409831c553b76e17b10ae15ec33be584d053f16c0506c7ca5545e3e6` |
| asr-online-gpu1 | `93494bfbd104cd57292d86c72df4a95454d04e652951a281b7212f08f56b4962` |
| asr-online-gpu2 | `301d52d6c70bfa305cf6c10cfcb860412f894294df1c68be15ce81846d81bbfd` |
| ocr-gpu0 | `9049b707d6a5db54f4c81f408db7b15d491d9052159d16b1a12bd3b6bd6dbebb` |
| ocr-gpu1 | `5f093ef557df7d58f9c221821578a24ebe12d3ef6b4ddbdf12b4e782819e39ee` |
| ocr-gpu2 | `ad9dcca9e3e0a8414890dcd14891955e99af3886430a53b5316b178bbba0ca64` |
| facerec-gpu0 | `2734933e68dc882950d2fb7d564d36979ca3f60902c417f0391607e56b18b542` |
| facerec-gpu1 | `e8e6d46479b3095d48a947cd8f2ac204368f4be6e5c7df8a29f5fe578dfd89eb` |
| facerec-gpu2 | `7bff9f9ccd427ff62b54e883597706d7bed7f2c45911c35d95db64f2948b616e` |
| screen-det-gpu0 | `fc32a4464b0eaac8ed080c07a753668aa81fb634696acfa2684db05b7583b67e` |
| screen-det-gpu1 | `f4c26ebd2ac24676b332a9536b140562a6475206c0e245a4364eb0da920752f8` |
| screen-det-gpu2 | `749287bc9f910f6f09e22ab40aa8b24e5e700a47db12e920d4844a723f69bd91` |
| vbas-gpu0 | `260da96f2a13843090edfb2e1de7429e80b3dd46ae3ba1ceaa2f5a2e4dc992bb` |
| vbas-gpu1 | `5d24f6cd45aae8d56fd795db2591ed38ea3252ac4a642e9d7b3437262c4a4ba8` |
| vbas-gpu2 | `b8e6e0528abba8adde38f10b7fe98259ab6abc65f3b2098335fd9ed998bd9a89` |
| ppt-slice-cpu0 | `e45c5d1f53df7f0c0b50888747f140f001952f87f7d26f57e092727163dddbe3` |
| ppt-slice-cpu1 | `c044ab0194834e8f94d3511871bdb94479e6666c130b17993c1bd655c6f77998` |
| ppt-slice-cpu2 | `4fc4ad228bbf334ebc649d727c793986b7c0bb2a174d9632eb35600d7477caf6` |

七类算子当前镜像完整 ID：ASR Offline
`sha256:a287f19b1072c784241a5dfc608a059f13bff2a54ce8659c62f6fd3156736bc1`、ASR Online
`sha256:f416db726429a52d9d7c22978a16d49bafa199ea5999d38326762367b0973098`、OCR
`sha256:e2264ac186f2ba077264507bd21e6fa57e96e2f44aa3a901ce10e24d2e5672ba`、FaceRec
`sha256:49352c58c2a3cc39b3c37a52d7d6dbb8951de3db45c5661835a9a2dbc689d5cd`、ScreenDet
`sha256:268c849235a2d101c967b968e41c394915ef8dcece68da6929e728943b65ac10`、VBas
`sha256:69fd3ebaf62bc7db73d06b6107eb1eccbb7e8c8e0f03ad672e881955f3a2abae`、PPT Slice
`sha256:fb5403906f9fb681915820738278c88413f87e2b26b38f6de520fd9aada72a86`。

## 构建缓存与磁盘基线

- Docker：233 个镜像、33 个活动镜像、镜像总占用约 230.9 GiB。
- Build Cache：849 条、约 85.73 GiB；buildx 视图总计约 195.2 GiB 可复用记录。
- 本变更构建必须复用缓存，禁止 `--no-cache`，禁止 `docker builder prune`、
  `docker buildx prune` 和任何宽泛 container/image/system prune。
- 新版本门禁通过前保留旧平台镜像；通过后只用替换前再次采集的完整 ID 精确删除被替代对象。

## 冻结并发配置

| 组件 | 发布前必须保持的值 |
| --- | --- |
| Orchestrator | `node_concurrency=16` |
| Vision | `worker.concurrency=16`、`scan.batch_size=8`、`media.max_concurrent_processes=6`、`vbas.max_batch_size=8`、`vbas.max_concurrency=3` |
| Online Gateway | `http.max_connections=2048`、`http.max_keepalive_connections=512` |
| ASR Offline | 每实例 worker 1、`platform.max_concurrent_requests=4` |
| ASR Online | 每实例 worker 1、`platform.max_concurrent_requests=10` |
| OCR | 每实例 worker 1、容量 256、OCR 并发 1 |
| PPT Slice | 每实例 worker 1、容量 10、队列 25 |
| FaceRec / ScreenDet | 每实例容量 128 |
| VBas | 每实例 worker 1、`max_concurrent_requests=1`、`MaxConcurrentBatches=1`、`MaxQueueSize=0` |

四个平台 `config.toml` 均从目标机 Git 工作树只读挂载到 `/config/config.toml`；课程和结果目录
按服务边界挂载 `/data/course`、`/data/result`。远端替换门禁必须依次保存宿主机 TOML 摘要、
Compose 展开值、Docker Mounts 和容器内配置解析结果，任何一层不一致均停止发布。

## 当前实施与验证层级

- 已新增能力级槽位规划、状态 10/30 原子领取、单轮一次等待协调和 capability advisory
  transaction lock；ASR/PPT/OCR 单元回归达到层级 2。
- 已新增 `40P01/40001` 新事务有限重试及非重试 SQLSTATE 单元故障注入，达到层级 2。
- 已新增 Orchestrator、Vision、Online 和 PPT keeper 的同 lease_id 续租安全窗口与首次
  `ReadError` 恢复单元测试，达到层级 2。
- 已使用目标机 PostgreSQL 创建隔离 `_test` 数据库：ASR/PPT/OCR 各 100 个状态 10/30 节点
  在 16 线程下完成互斥领取，结果 `4 passed`；另以 100 个 ASR 节点并发执行 16 路 capability
  等待协调、稳定聚合和 16 路领取，结果 `1 passed`，无重复 claim 或 `40P01`。该证据达到
  层级 3，但不接触业务 `algorithm` 库中的课程数据。
- 真实 Kafka、完整服务 lifespan 和目标机真实算子重跑尚未完成，不得把本记录标记为发布通过。

## 首次候选发布门禁失败与修复

候选 SHA `73c2bf5ae1597ac682a2bb9925354fc15ad1f36d` 的四个平台镜像均使用既有
BuildKit 缓存构建成功，架构均为 `amd64` 且镜像 revision 一致。首次只替换四个平台容器后，
Control、Vision 和 Online Gateway 健康，Orchestrator 在历史 ASR 节点 `21231` 上进入 fatal，
readiness 返回“只有处理中节点可以合并进度”。本次失败未提交新业务负载，七算子和中间件均未
重建或停止，旧平台镜像已经按完整 ID 增加 `rollback-pre-73c2bf5` 标签并保留。

根因有两项：

1. 新领取 SQL 把节点从状态 10/30 原子写为状态 40，Dispatcher 随后绑定租约并写入租约
   progress，但 Repository 的合并接口仍只接受状态 50；Fake Repository 单元测试没有暴露
   这一真实 PostgreSQL 状态约束。
2. fatal 退出只在 `service.environment=production` 时发送 `SIGTERM`，而 Compose 没有覆盖
   仓库默认的 `development`，所以只记录退出意图，容器仍保持运行/不健康。

修复增加真实 PostgreSQL 回归，证明状态 40 可合并租约上下文且仍保持状态 40；状态 40/50
之外继续拒绝。Compose 显式注入 `ORCHESTRATOR_SERVICE__ENVIRONMENT=production`，并新增
进程退出请求测试。首次候选失败保持为失败事实，后续必须形成新 SHA、只重建 Orchestrator
镜像并重新执行全部远端门禁，不能把首次 attempt 改写为通过。

## 第二候选的批次尾部低利用率与修复

候选 SHA `6350595e1a185c4c7c94c96049924ef95de90fd5` 在目标机运行后，四个平台均健康，
Orchestrator 六个关键循环均为 running，21/21 算子注册、18 个 GPU 算子进程、3 个 CPU PPT
实例、三个 Kafka 消费组 lag 0、Outbox 全部 published，且 PostgreSQL deadlock 与
Orchestrator fatal 计数均为 0。真实 Kafka、Outbox、课程命令重放、服务重启组合测试
`test_real_milestone_2a_runtime_closes_and_recovers` 在目标机得到 `1 passed in 13.96s`。

恢复历史 `tast_asr_*` 队列后，受控状态快照为状态 30：67、状态 50：5、状态 60：27、状态
70：1。首批曾领取 12 个节点，但其中 7 个完成后，执行器仍等待余下 5 个长任务，67 个等待
节点没有利用已经释放的 7 个槽位。根因是旧 `run_once()` 对整个 reservation 批次执行一次
`gather`，形成批次屏障；该候选解决了死锁与 fatal，但尚未兑现“任一槽位释放后继续领取”。

后续修复把节点执行器改为有界在途协程池，使用 `FIRST_COMPLETED` 在单槽位结束时立即补位；
最后一个在途节点结束后返回 runtime，避免额外空租约探测。新增回归证明一个慢节点仍运行时，
短节点释放的槽位可以启动第三个节点；取消执行器会取消在途调用并释放其租约。修改后的
Orchestrator 定向测试为 `20 passed`、全量测试为 `95 passed`，Ruff、strict Mypy、
`compileall`、应用导入和 `git diff --check` 均通过。该实现仍须形成新候选 SHA 并在目标机
重新执行远端门禁。

## 提交前实现复审

- `NodeExecutor` 先对去重后的 capability 规划 `node_concurrency` 槽位，单一能力可使用全部
  16 个槽位，多能力按轮转游标分配。
- 普通节点从状态 10/30 通过 `FOR UPDATE SKIP LOCKED` 原子领取；同 capability 每轮最多
  执行一次等待协调，并使用 PostgreSQL advisory transaction lock 隔离多 Orchestrator 进程。
- PostgreSQL 仅对 `40P01`/`40001` 重建事务并有界退避；耗尽后抛出
  `TransientInfrastructureError`，不将基础设施故障写成节点状态 70。
- 关键后台循环由 supervisor 分类恢复或请求主进程退出；`/ops/readiness` 暴露
  `running/degraded/fatal`、瞬时错误、重试数、恢复数和最近恢复时间。
- 普通状态 40/50 节点仅在超时、不属于当前运行纪元且原租约不存在时恢复到 30；
  `PPT_SLICE` 排除在普通恢复外。这一边界避免无外层租约的 OCR 节点在当前进程内被误恢复。
- Orchestrator、Vision、Online 和 PPT keeper 使用同一租约续租安全窗口；对同一
  `lease_id` 重试，404 确认丢失，释放 404 视为幂等成功。
- 本次未新增数据库字段、索引或表，复用现有 `claimed_by`、`claim_token`、`attempt`、
  `claimed_at` 和 progress，因此不需要 migration，也未在远程手工执行 DDL。

## 配置四层证据

2026-08-28 已对目标机完成以下四层只读核对：

1. 宿主机各项目 `config.toml`；
2. Docker Compose 展开后的配置和变量；
3. 容器 Mounts 中的宿主源路径、容器目标和只读标记；
4. 容器内 Python 对 `/config/config.toml` 的实际解析值。

四层均与上述冻结基线一致。特别说明：目标机 VBas 宿主配置及容器实际值为
`platform.max_concurrent_requests=1`、`TIAS.MaxConcurrentBatches=1`、`TIAS.MaxQueueSize=0`；
本地 Git 中的历史默认值不是本次远程发布权威，后续 Compose 替换必须保留目标机这组运行值。

## 本地与集成验证结果

| 验证项 | 结果 | 层级 |
| --- | --- | --- |
| 本次 Python 文件 Ruff | 通过 | 1 |
| strict Mypy | 通过 | 1 |
| `compileall` 及四服务 `app.main:app` 导入 | 通过 | 1 |
| Control 全量测试 | `25 passed` | 2 |
| Orchestrator 全量测试 | `95 passed` | 2 |
| Vision Orchestrator 全量测试 | `47 passed` | 2 |
| Online Gateway 全量测试 | `51 passed` | 2 |
| 真实 PostgreSQL ASR/PPT/OCR 各100节点、16线程领取 | 通过，无重复 claim、无 `40P01` | 3 |
| 真实 PostgreSQL SQLSTATE 故障注入 | `40P01` 恢复/耗尽、`40001` 恢复、非重试错误均通过 | 3 |
| 本次 Repository 及真实 PostgreSQL 定向回归 | `18 passed` | 2/3 |
| 真实 Redis 公共路由/共享容量/续租/释放/TTL | `41 passed` | 3 |
| 真实 Kafka 发布/手动提交/未提交重投 | 目标机原生执行 `1 passed` | 4（局部） |

平台非集成全量测试的一次完整扫描结果为 `3226 passed, 6 failed, 3 skipped, 165 deselected`。
其中与本变更相关的 3 项已修复并定向通过；剩余 3 项是已有的非本次范围问题：

- 两项 registry wheel 临时源码根中 `logging.py` 遮蔽标准库 `logging`；
- 一项 VBas identity 部署契约测试仍读取历史 TOML 节名 `[TIAS]`。

上述 3 项不用于伪造本变更通过，也不在本次顺手修复。真实 Kafka 的基础发布/重投已通过，
但候选版四服务 lifespan、fatal 重启、Outbox/课程/视觉/PPT 组合门禁仍待发布后验证。远程候选
发布和真实业务回归仍是未完成门禁。

提交前 Harness consistency 为 `5 passed`，`openspec validate
stabilize-orchestrator-concurrent-dispatch --strict` 返回 valid，`git diff --check` 零输出。

## 第三候选发布与基础门禁

2026-08-28 已形成并推送第三候选 SHA
`c9c703bb482a781b6fd851499506854fb7f78fb7`。目标机因无法直接访问私有 Git remote，使用本机
`git archive` 生成只读发布目录 `/root/workspace/releases/stabilize-c9c703b`，并仅复制目标机
现行四平台配置；主工作树中的用户运行配置未被覆盖。

四个平台镜像使用既有 BuildKit 缓存构建，未使用 `--no-cache`，未执行 builder/buildx prune，
也未重建七算子。替换前运行版本均为 `6350595e1a185c4c7c94c96049924ef95de90fd5`；替换后
四个平台镜像及容器均绑定 `c9c703bb482a781b6fd851499506854fb7f78fb7`：

| 服务 | 旧镜像 ID | 新镜像 ID | 新容器 ID |
| --- | --- | --- | --- |
| Control | `sha256:7f7f14b01a1d...` | `sha256:eabffb5e0dc4...` | `8cd860f97d40...` |
| Orchestrator | `sha256:b01bbc4bcdf9...` | `sha256:cac8e6b6373f...` | `1cf67873ab4d...` |
| Vision Orchestrator | `sha256:1c97117330b9...` | `sha256:ddd10dd12c61...` | `dec084a415b8...` |
| Online Gateway | `sha256:2ab7f8446c7d...` | `sha256:9219e657c710...` | `32aa27681ebd...` |

完整 ID 账本保存在目标机发布目录的 `runtime-evidence/pre-replace-platform-ledger.txt` 与
`runtime-evidence/post-replace-platform-ledger.txt`。新镜像均已增加 `candidate-c9c703b` 标签；
`candidate-6350595`、`candidate-73c2bf5`、`failed-first-73c2bf5` 和
`rollback-pre-73c2bf5` 继续保留，真实业务门禁完成前不清理。

发布后基础门禁结果：

- `preflight runtime --git-sha c9c703b...` 通过镜像 revision、Control/Orchestrator
  readiness、数据库中文注释/索引和 Kafka Topic 检查；
- 四个平台全部 healthy；Orchestrator 的 Outbox、课程 Consumer、节点执行、视觉发布、
  视觉结果 Consumer、PPT 对账六个循环全部为 `running`；
- 七类算子共 21/21 实例 `ONLINE` 且模型 ready，`nvidia-smi` 可见 18 个 GPU 算子进程，
  Compose 可见 3 个 CPU PPT Slice 实例；
- 三个 Kafka Consumer Group lag 均为 0，Outbox 13104 条全部已有 `published_at`；
- 替换后 PostgreSQL `deadlock detected=0`，Orchestrator fatal 计数为 0；
- 配置四层仍为 Orchestrator `node_concurrency=16`、Vision `16/8/6/8/3`、Online
  `2048/512`、ASR Offline 单实例容量 4、VBas 单实例 `1/1/0`。

真实 Kafka/lifespan 门禁通过 SSH 端口转发连接目标机 PostgreSQL、Redis 和 Kafka，并把
Docker 证据读取指向目标机 daemon。`test_real_milestone_2a_runtime_closes_and_recovers` 与
`test_milestone_2b_infrastructure_case_runners.py` 共 `6 passed in 51.01s`，覆盖课程命令重放、
Outbox 至少一次发布与恢复、未提交消息重投、真实服务 lifespan 和隔离资源清理。首次未指定
远端 Docker daemon 的执行结果为 5 失败、1 通过，失败原因均是本机 Docker Desktop 未运行，
不作为业务实现失败，也未修改断言绕过。

第三候选启动后，历史 `tast_asr_*` 状态从第二候选的 `30=67、50=5、60=27、70=1`
持续推进；18:21:45 快照为 `30=4、50=21、60=74、70=1`，并保持三个实例合计 12 条容量
租约。该事实证明任一槽位完成后会即时补领，不再等待整批长任务结束。历史失败状态 70 仍
原样保留；待历史非终态收敛后，才使用全新任务前缀执行正式 ASR 100 次门禁。

## 第三候选真实 ASR 100 次门禁

历史 `tast_asr_*` 最终收敛为状态 60：99、状态 70：1，原失败事实未被重写；活跃 ASR 租约
归零后，使用全新前缀 `verify_asr_c9c703b_r3_` 和同一教师视频执行正式门禁。测试按 16 路
HTTP 并发提交 100 个仅含 `ASR` 的任务，运行配置保持 Orchestrator 节点槽位 16、三个
ASR Offline 实例各容量 4。媒体地址只在运行期传入，证据中仅保留 `lan_http/url_redacted`。

| 指标 | 结果 |
| --- | --- |
| 北向提交 | 100/100 受理，0.535 秒 |
| 单次提交延迟 | P50 0.047 秒，P95 0.135 秒，最大 0.139 秒 |
| 最终状态 | 状态 60：100，状态 70：0 |
| 总墙钟时间 | 1885.363 秒 |
| 端到端耗时 | P50 1053.849 秒，P95 1800.791 秒，最大 1883.198 秒 |
| 节点运行耗时 | P50 190.851 秒，P95 301.312 秒 |
| 排队耗时 | P50 870.439 秒，P95 1607.710 秒 |
| 下载阶段（文件 mtime 推导） | P50 7.655 秒，P95 84.614 秒 |
| FFmpeg 阶段（文件 mtime 推导） | P50 1.950 秒，P95 69.350 秒 |
| ASR 阶段（文件 mtime 推导） | P50 176.063 秒，P95 202.099 秒 |
| 算子 `gpu_time_ms` | P50 154883.100，P95 178573.057 |
| 实例任务分布 | GPU0 33、GPU1 34、GPU2 33；各峰值活跃租约 4 |
| PostgreSQL deadlock | 统计增量 0，日志匹配 0 |
| Orchestrator readiness | 356 个 GPU 采样周期内 readiness 失败采样 0 |

GPU 采样中三卡利用率峰值分别为 75%、71%、90%，显存总占用峰值分别为 16859、16087、
16517 MiB；该显存包含同卡部署的六类 GPU 算子，不能解释为 ASR 单算子独占。任务结束后
全部算子活跃租约与 reported inflight 均为 0，测试前缀临时 `/data/course` 目录为 0，Outbox
pending 为 0，Kafka 最大 lag 为 0。

分段耗时是由节点 `started_at`、下载视频/WAV 最后 mtime 与节点 `updated_at` 推导的近似值；
`gpu_time_ms` 和 `load_audio_time_ms` 来自 ASR 精简指标。测试器明确丢弃 `text`、`segments`、
完整响应和媒体 URL。0600 原始统计位于目标机
`/root/workspace/releases/stabilize-c9c703b/runtime-evidence/asr-100-result-r3.json`。

## PPT/OCR 积压与三能力混合门禁

PPT 单泳道使用全新前缀 `verify_ppt_c9c703b_` 以 16 路提交并发创建 36 个任务，超过三个
PPT Slice 实例合计 30 的声明容量。36/36 在 0.204 秒内受理；运行中明确观测到
`PPT_SLICE: 50=30、30=6`，三个 CPU 实例均达到 10 个活跃租约和 reported inflight 10。
首批完成后等待节点在下一采样全部补位，OCR 同时从依赖状态 20 进入执行，不形成能力串行阻塞。

最终 36 个 `PPT_SLICE` 和 36 个 `PPT_OCR` 全部为状态 60，墙钟时间 1200.784 秒；首个
切片和首个 OCR 分别在 690.319 秒、712.901 秒完成。PPT Slice 三实例处理的唯一课程任务数
为 10/16/10，峰值租约均为 10。每个 PPT 的多张 OCR 图片按工作项分散到三个 OCR 实例；
三个实例分别接触到 36/36/36 个课程任务，峰值活跃单图租约为 9/7/8。该计数表示一个课程的
不同 `ppt_image_id` 可跨实例处理，不表示外层 PPT 节点被重复执行。

随后使用前缀 `verify_mixed_c9c703b_` 以 12 路并发提交 12 个同时包含 `PPT` 和 `ASR` 的任务。
启动快照同时出现 ASR 11 执行、PPT Slice 10 执行，之后达到 ASR 12 与 PPT Slice 12 同时
执行，证明多 capability 轮转没有饥饿。最终结果：

- 12 个 `ASR_TRANSCRIPTION`、12 个 `PPT_SLICE`、12 个 `PPT_OCR` 全部状态 60；
- ASR/PPT 两类任务均为 12/12 状态 60，总耗时 466.865 秒；
- 首个 ASR、PPT Slice、PPT OCR 分别在 207.748、291.051、308.611 秒完成；
- ASR 三实例唯一任务分布为 4/4/4，峰值租约均为 4；PPT Slice 也为 4/4/4；
- 两轮 readiness 失败采样均为 0，PostgreSQL deadlock 统计与日志匹配均为 0；
- 结束后活跃租约、reported inflight、Outbox pending、Kafka 最大 lag 和测试临时目录均为 0。

PPT 与混合轮次的 0600 原始统计分别位于目标机
`runtime-evidence/ppt-36-result.json` 和 `runtime-evidence/mixed-12-result.json`；文件只含受控标识、
状态、耗时、容量分布和门禁统计，不含 OCR 文本、媒体 URL 或完整响应。

## 教师/学生视觉连带回归

使用前缀 `verify_visual_c9c703b_` 提交 3 节同时包含 `TEACHER_BEHAVIOR` 和
`STUDENT_BEHAVIOR` 的课程，共 6 条视觉分支。学生请求使用 1920×1080 的前后区域和
`student_count=70`；教师/学生媒体 URL 只在运行期使用，证据中已脱敏。

三次北向请求均受理，耗时分别为 0.022、0.037、0.036 秒。启动阶段同时出现三个学生节点
执行和三个教师节点领取/执行；视觉 Consumer 使用手动提交，运行中 Kafka 最大 lag 为 13。
最终教师、学生任务类型各 3/3 状态 60，对应两个视觉节点也各 3/3 状态 60，总耗时
572.723 秒；首个教师、学生结果分别在 127.235 秒和 219.838 秒到达。

三个 VBas 实例均处理过三节课程的不同帧批次，峰值活跃租约均为 1，符合目标机冻结的
VBas 单实例 `1/1/0` 并发边界。结束后正式 Kafka 三组 lag 全部归零，Outbox pending、全部
算子活跃租约/reported inflight、测试临时课程目录均为 0；readiness 失败、PostgreSQL
deadlock 统计增量和日志匹配均为 0。该结果证明通用节点运行期间视觉命令发布、Vision
Consumer、VBas 批次、视觉事件回传和任务聚合没有永久停止。

0600 原始统计位于目标机
`/root/workspace/releases/stabilize-c9c703b/runtime-evidence/visual-3x2-result.json`，不含行为结果、
证据图片、媒体 URL 或完整请求/响应。

## Vision 与 Online 租约续租故障注入

代码级定向门禁结果为 Orchestrator/PPT `28 passed`、Vision `3 passed`、Online `2 passed`、
PPT keeper `2 passed`。此外，在目标机当前 `c9c703b` 的 Vision 和 Online 运行镜像中直接加载
生产 `CapacityLeaseHttpClient` / `OnlineCapacityLeaseClient`，使用 `httpx.MockTransport`
隔离注入受控续租故障；该测试不修改 Control、Redis、运行容器网络或真实业务租约。

Vision 使用 `student_behavior` 模拟 VBas 批次，Online 使用 `asr_online` 模拟实时 ASR 长会话。
两者均通过以下门禁：

1. 第一次续租抛出 `ReadError`，同一 lease_id 在 TTL 安全窗口内继续重试；工作最终完成，
   观测到 5 次续租调用；
2. 释放返回 404 被视为幂等成功；
3. 持续 `ReadError` 时按配置完成 2 次尝试后，只取消对应批次/会话并转换为服务定义错误；
4. 与失败工作并行的健康工作仍完成并成功续租 7 次，证明失败不传播到兄弟工作。

注入后 Vision/Online readiness 继续为 ready，全部活跃租约和 reported inflight 为 0。0600
证据位于目标机 `runtime-evidence/vision-lease-fault.json` 与
`runtime-evidence/online-asr-lease-fault.json`，不包含凭据、请求正文或媒体数据。

## 最终收敛审计

第三候选真实门禁结束后的本次范围事实如下：

- 任务类型：ASR 100、混合 ASR 12、混合 PPT 12、PPT 36、教师行为 3、学生行为 3，
  共 166 条，全部状态 60；
- 节点：`ASR_TRANSCRIPTION=112`、`PPT_SLICE=48`、`PPT_OCR=48`、教师/学生视觉各 3，
  共 214 个，全部状态 60；
- Outbox 共 13270 条，全部已有 `published_at`；正式 Kafka 三个 Consumer Group lag 全为 0；
- 21 个算子实例的活跃租约和 reported inflight 均为 0；所有本次测试前缀的
  `/data/course` 临时目录均为 0；
- `/data/result` 保留 PPT 36 个目录约 163.8 MB、混合任务 12 个目录约 54.6 MB、视觉 3 个
  目录约 15.5 MB，未删除持久结果；目标文件系统仍有约 233 GB 可用；
- 候选发布后 PostgreSQL deadlock 日志匹配为 0，Orchestrator fatal 状态匹配为 0；四平台
  和 PostgreSQL/Kafka/Redis/MongoDB 全部 healthy。

全局数据库仍有 8 个历史状态 20 节点，分别属于 2026-08-24～26 的旧 Campaign、独立 PPT
任务和退役 Text Analysis 历史事实；它们不属于任何 `verify_*_c9c703b_` 前缀。本变更不把
历史未完成事实伪装为本轮失败，也不越权改写或删除它们。

## 精确清理与保留资产

全部真实门禁通过后，按替换前账本逐个核验本轮被替代的四个 `6350595` 镜像完整 ID。四个
镜像均只有各自 `candidate-6350595` 标签，且没有任何运行或停止容器引用；Compose 替换时旧
容器已被删除，因此没有额外旧容器可清理。随后按完整 ID 删除：

- Control `sha256:7f7f14b01a1d...`；
- Orchestrator `sha256:b01bbc4bcdf9...`；
- Vision Orchestrator `sha256:1c97117330b9...`；
- Online Gateway `sha256:2ab7f8446c7d...`。

清理未使用 `container/image/system/builder prune`。清理后四个 `candidate-c9c703b` 标签及当前
容器 revision 均保持不变；更早的 `failed-first-73c2bf5` 和 `rollback-pre-73c2bf5` 继续作为
历史失败/回滚证据保留。Docker Build Cache 为 926 条、87.04 GB；PostgreSQL、Kafka、Redis、
MongoDB 四个数据卷仍存在，平台和基础设施全部 healthy，三类本次持久结果目录仍为
36/12/3。数据库 volume、模型、Git、`/data/result` 和七算子镜像均未删除。

本候选没有触发业务门禁失败或回滚分支。此前两个候选的失败事实仍保留在本场景中；本轮没有
通过重启掩盖 deadlock、没有把部分成功写成通过，也没有提前删除回滚资产。

## 与实时负载路由变更的联动结论

本变更没有修改或回退 `balance-operator-routing-by-live-load` 已实现的公共 Redis 最少负载
选择器。真实业务门禁提供了以下交叉证据：

- 正式 ASR 100 次在三个实例上的任务分布为 33/34/33，各实例峰值租约均为 4；
- ASR/PPT/OCR 混合轮次中，ASR 与 PPT Slice 三实例均为 4/4/4；
- 三个 VBas 实例都处理了本轮三节课程的真实帧批次，峰值租约均为 1；
- 所有轮次结束后 21 个实例的活跃租约与 reported inflight 均归零。

这些事实证明并发节点调度、续租和即时补位修复没有让实例选择退回“固定首实例长期独占”。
它们只作为 `balance-operator-routing-by-live-load` 后续远端均衡用例的前置引用，不能替代该
变更仍未完成的 20 任务首批租约时序、A 服务兼容、在线千路和混合负载门禁。该变更的
`tasks.md` 在本次 apply 前已有独立未提交修改，因此本变更不覆盖或顺带提交该文件；其后续
提交应引用本场景和候选 SHA `c9c703b`。

## 与极限 Campaign 的联动边界

`run-milestone-2b-extreme-load-campaign` 的历史 ASR 失败 attempt 保持只读，本轮没有从失败
用例之后续写为通过。本次只构建并核验了同一候选 SHA 的四个平台镜像；七算子代码和协议未
变化，所以聚焦修复验证复用了既有七算子镜像。这个选择满足本变更的发布门禁，但不满足
canonical Campaign 的“同一最终 SHA 四平台与七算子镜像”要求。

进入下一次 canonical Campaign 前必须：

1. 形成新的最终 Git SHA，并让四平台和七算子镜像全部绑定该 SHA；
2. 使用新的 seed、Campaign ID 和 write-once attempt；
3. 从规定阶段重新执行，而不是复用或改写旧 ASR 失败 attempt；
4. 保留并重新执行尚未完成的 217 条反例、26 条压力/恢复、6 项 B 级人工复核、完整在线与
   混合负载、故障恢复和 4～8 小时长稳门禁。

因此，本场景只能解除“并发调度稳定性”这一前置阻断，不能发布“里程碑 2B 极限 Campaign
全部完成”的结论。

## 验证层级、结论与未覆盖项

| 验证层级 | 本变更证据 | 结论 |
| --- | --- | --- |
| 1 静态验证 | Ruff、strict Mypy、`compileall`、四服务导入和配置解析 | 通过 |
| 2 单元验证 | 四平台相关全量/定向测试、状态机、调度器、租约 keeper 和 PPT/OCR 回归 | 通过；平台全量扫描另有 3 项既有非本变更失败，已单独记录 |
| 3 PostgreSQL/Redis 集成 | 真实 PostgreSQL 并发领取、SQLSTATE 注入、真实 Redis 路由/容量/续租/释放/TTL | 通过 |
| 4 Kafka 集成 | Outbox 至少一次发布、手动提交、未提交重投、课程命令重放 | 通过 |
| 5 服务运行验证 | 四平台 healthy、六个关键循环 running、lifespan、readiness 和恢复 | 通过 |
| 6 算子契约验证 | ASR 100、PPT/OCR 36、混合 12、视觉 3×2、Vision/Online 生产客户端故障注入 | 本变更范围通过 |

本变更最终结论为：并发节点领取死锁、批次屏障低利用率、关键循环僵尸状态和单次租约续租
异常已按设计修复，并在 `192.168.29.11` 的三 GPU/21 实例拓扑通过真实业务门禁。部署手册
既有的 Orchestrator readiness 503、PostgreSQL `40P01`、租约续租、普通节点恢复和精确回滚
处置章节仍适用；本轮未改变北向接口、目录、端口、迁移或部署入口，无需复制第二套操作步骤。

未覆盖或明确留待后续的项目包括：完整极限 Campaign、217+26+6 最终门禁、在线千路与离线
混合极限同时压测、4～8 小时长稳、七算子同最终 SHA 重建，以及将媒体下载/FFmpeg 从算子租约
中拆出独立并发控制。本变更仍保持“先取得租约，再下载与抽取”的流水线顺序，ASR 总容量 12
表示最多 12 条完整流水线，不表示 12 条同时 GPU 推理。

## 归档前技术审查

2026-08-28 对失败基线 `d19e5e4` 至运行候选 `c9c703b` 的实现范围完成最终复审。审查覆盖
Repository 原子领取和 SQLSTATE 重试、NodeExecutor 有界在途池、运行时监督与恢复、三调用
服务和 PPT keeper 的租约韧性、配置/Compose、部署手册和相关测试；未发现新的本变更阻断项。
运行候选仍为 `c9c703b`，随后产生的 Harness/OpenSpec 记录提交只补充验收事实，不要求重新
构建未变化的运行镜像。

归档前技术门禁结果：

- `openspec validate stabilize-orchestrator-concurrent-dispatch --strict`：valid；
- `tests/test_harness_consistency.py`：`5 passed`；
- `git diff --check`：零输出；
- `d19e5e4..c9c703b` 实现差异检查：零空白错误，代码、测试、配置和文档范围与变更设计一致；
- 工作区范围审计：本次只允许暂存本场景与本变更 `tasks.md`，A 服务文档、实时负载路由任务、
  `text_analysis/README.md`、Campaign registry 和全部未跟踪文件继续保持未暂存、未覆盖。

OpenSpec 9.4 的技术检查已经完成；“用户确认后标记完成并归档”仍是唯一未完成步骤。
