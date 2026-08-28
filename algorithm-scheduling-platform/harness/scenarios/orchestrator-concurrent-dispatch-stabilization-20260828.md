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
