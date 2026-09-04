# 视觉编排持续供给与抽帧流水线验证（2026-09-04）

## 变更范围

- OpenSpec：`optimize-vision-orchestration-pipeline`
- 受影响服务：`vision_orchestrator_service`，以及负责发布视觉命令后节点原因的 `orchestrator_service`
- 保持不变：A 服务 HTTP 契约、VBas HTTP 契约、整数节点状态码、Control 离线容量租约和四服务边界
- 目标：视觉命令持续补位、跨课程公平抽帧、首批帧就绪后立即推理、真实阶段状态和完整失败清理

## 修复前基线：`max_poll_records=2`

- Run ID：`offline20-20260904T024034Z`
- 远端原始证据：
  `/root/workspace/.algorithm-scheduling-restricted-reports/stabilize-capacity-wait-and-load-recovery/offline20-20260904T024034Z/`
- 运行配置：`worker.concurrency=16`、`kafka.max_poll_records=2`、
  `media.max_concurrent_processes=6`、`scan.batch_size=8`。
- `VisualCommandConsumerLoop.run_once()` 一次最多取得两条消息，并等待本次 poll 的全部消息完成后才再次
  poll；因此配置中的 16 个课程槽位不能持续填满。两条消息中一条先完成时也不补位。
- 约 30 分钟时仅 6/20 课程达到全量终态；PPT 与 ASR 已完成，学生视觉完成 7 个、教师视觉完成
  6 个。另有 26 个视觉节点显示“视觉节点已领取，正在准备本地视频”，但并未同时进入 VBas。
- 运行采样显示 VBas 有 batch 时三个实例可以同时处理，空闲主要发生在 Vision 消费和媒体准备阶段，
  不能归因于 VBas 实例选择失败。
- 本轮为未完成基线。2026-09-04 受控停止后保留全部原始报告，不得重标为通过。

## 临时配置对照：`max_poll_records=16`

- Run ID：`maxpoll16-20260904T033113Z`
- 远端原始证据：
  `/root/workspace/.algorithm-scheduling-restricted-reports/vision-max-poll-records-comparison/maxpoll16-20260904T033113Z/`
- 唯一临时调整：`kafka.max_poll_records` 从 2 改为 16；其他 Vision、VBas、媒体和任务参数保持不变。
- 第一条视觉命令错峰到达时，Consumer 只取得一条并等待该命令完成；其间新消息在 Kafka 积压。
  第一条完成后 Consumer 可以一次预取更多命令，证明该参数能够改善已有 backlog 的领取数量。
- 预取后的多个命令共享同一个 ffmpeg Semaphore。单条长视频提前为数百个时间点创建等待协程，后续
  命令的 ffprobe 和首批抽帧被排在其后；`_infer()` 又要等待整轮帧全部抽完才调用 VBas。
- 受控运行到 1005 秒时，PPT Slice、PPT OCR 和 ASR 均为 20/20 完成，但只有 1/20 课程达到全量
  终态；学生视觉完成 2 个、教师视觉完成 1 个，另有 37 个视觉节点为运行态。
- 停止时 7 个学生节点处于抽帧、9 个教师节点处于粗粒度扫描，其余运行节点仍显示准备本地视频；
  三张 GPU 利用率均为 0%。多个课程目录已经各产生约 290 张帧，但没有形成持续 VBas 供给。
- 判定：`max_poll_records=16` 改善命令预取，但没有解决端到端吞吐；该轮是诊断性受控停止，不是
  正式压力验收，也不得标记为通过。

## 受控停止与清理事实

- 停止两轮诊断驱动后，先停止 Orchestrator 与 Vision，避免数据库删除期间继续产生视觉事件。
- 将 `vision-orchestrator` 的 `algorithm.visual.commands` 和
  `algorithm-orchestrator-visual-events` 的 `algorithm.visual.events` 消费水位推进到停止后的末尾，
  防止已删除测试节点被旧消息重新触发。
- 数据库只删除两个 Run ID 对应的 `outbox_events`、`course_task_types`、级联节点/结果和
  `course_jobs`；清理后对应 `course_jobs` 数量为 0。
- 文件只删除 `/data/course/{run_id}-course-*` 和 `/data/result/{run_id}-course-*`，受限报告目录
  保留；没有删除其他课程、持久结果或基础设施数据。
- 临时远端配置已经恢复为 `max_poll_records=2`。清理后 Orchestrator 与 Vision 均为 `healthy`。

## 修复前代码证据

- `vision_orchestrator_service/app/infrastructure/runtime.py`：一次 `run_once()` 内创建任务并等待本批
  pending 集合归零，完成集合和提交水位不能跨 poll 存活。
- `vision_orchestrator_service/app/infrastructure/media.py`：`extract()` 对全部时间点调用
  `asyncio.gather()`，Semaphore 只限制运行进程，不限制或公平调度等待作业。
- `vision_orchestrator_service/app/application/analyzer.py`：`_infer()` 先等待 `extract()` 返回全部帧，
  再调用 VBas；学生全画面、前排、后排通过三次 `_infer()` 串行推进。
- `algorithm-scheduling-platform/packages/platform_common/repository.py`：Orchestrator 发布命令时提前将
  reason 写成“视觉节点已领取，正在准备本地视频”，不能区分 Kafka 等待和 Vision 实际消费。

## 实现阶段验证要求

1. 失败先行测试必须覆盖错峰命令持续补位、同 poll 快慢命令、跨 poll 连续 offset、停止重放、
   跨任务媒体公平、首批即推理、尾批、帧复用和失败隔离。
2. 修复后必须记录精确测试命令、通过数量、compile/import、指标与状态阶段证据。
3. 服务器只执行缓存构建、版本/架构/导入检查、容器替换、health/readiness 和 Kafka Consumer 门禁；
   按用户要求不代替用户执行最终业务压力测试。
4. 任何未运行的业务压力测试必须明确标记“待用户执行”，不得依据单元测试或健康检查写成通过。

## 实现结果

- `VisualCommandConsumerLoop` 持久保存跨 poll 的 pending、in-flight 和 partition 连续完成水位；
  错峰命令与同 poll 快命令释放槽位后立即补位。课程槽位满时通过暂停 assignment 的 keepalive
  poll 维持组成员资格，不额外预取消息。
- Kafka 适配器安装标准 rebalance listener；partition 被撤销时只取消该 partition 的 in-flight，
  移除对应 pending 和本地水位，不提交未完成 offset，重启后可重放。
- `FrameBatchPlan` 对去重、排序后的时间点按批次生成稳定身份。抽帧器不再为全部时间点创建协程，
  每次调用最多创建 `media.max_concurrent_processes` 个 worker，并使用可取消 asyncio 子进程。
- 每课程通过容量为 `scan.batch_prefetch=2` 的队列连接抽帧生产者和 VBas 推理消费者；首批就绪即
  推理，尾批不丢。学生全图、前排、后排复用同一帧路径，只更换区域身份和坐标。
- 新增命令 pending/in-flight、课程槽位利用率、媒体 pending/running、首批等待、首个 VBas 请求
  延迟和 prepared/inferred batch 指标。日志和指标均不包含图片、完整响应或模型结果。
- Orchestrator 发布成功后使用运行态进度更新将 reason 写为“视觉命令已发布，等待 Vision 消费”；
  Vision 随后细化为校验、探测、抽帧、容量等待、推理、聚合和持久化阶段，整数状态码不变。
- 部署前复审进一步发现命令发布确认与 Vision 首个进度事件存在并发覆盖窗口。共享 Repository
  增加按 `RUNNING + 旧 reason` 原子比较更新；若 Vision 已进入视频校验或后续阶段，Orchestrator
  不再把原因覆盖回“等待 Vision 消费”。首次发布和恢复发布共用同一保护逻辑。

## 失败先行与修复证据

- Consumer 新增错峰补位、同 poll 快慢任务、积压有界、跨 poll 乱序完成、取消重放、keepalive 和
  rebalance 测试。
- 媒体新增长短课程公平、多课程等待量上限、确定性尾批、探测不饥饿和子进程取消回收测试。
- 分析器新增首批推理与下一批抽帧重叠、学生帧复用、尾批和 image_id 重放稳定、单命令失败取消
  媒体生产者测试。
- 首次真实 PostgreSQL+Kafka 视觉恢复测试发现发布后使用 `transition_node(RUNNING)` 会形成非法
  `50 -> 50`；改为 `update_node_progress()` 后同一真实集成用例通过。该缺陷未带入服务器部署。

## 本地验证记录

```bash
cd vision_orchestrator_service
PYTHONPATH=../algorithm-scheduling-platform \
  ../algorithm-scheduling-platform/.venv/bin/python -m pytest -q tests
# 96 passed

cd ../orchestrator_service
PYTHONPATH=../algorithm-scheduling-platform \
  ../algorithm-scheduling-platform/.venv/bin/python -m pytest -q tests
# 105 passed, 1 warning

cd ../algorithm-scheduling-platform
.venv/bin/python -m pytest -q \
  tests/integration/test_visual_generation_runtime.py \
  tests/contract/test_service_entrypoints.py \
  tests/test_kafka_adapters.py
# 10 passed

.venv/bin/python -m pytest -q tests/test_control_api_submission.py
# 15 passed

cd ../control_service
PYTHONPATH=../algorithm-scheduling-platform \
  ../algorithm-scheduling-platform/.venv/bin/python -m pytest -q \
  tests/test_runtime.py -k 'route or openapi or operations or readiness'
# 6 passed, 5 deselected, 1 warning

cd ..
PYTHONPATH=algorithm-scheduling-platform \
  algorithm-scheduling-platform/.venv/bin/python -m compileall -q \
  vision_orchestrator_service/app orchestrator_service/app \
  algorithm-scheduling-platform/packages

cd vision_orchestrator_service
PYTHONPATH=../algorithm-scheduling-platform \
  ../algorithm-scheduling-platform/.venv/bin/python -c 'from app.main import app; print(app.title)'
# vision-orchestrator-service

cd ../orchestrator_service
PYTHONPATH=../algorithm-scheduling-platform \
  ../algorithm-scheduling-platform/.venv/bin/python -c 'from app.main import app; print(app.title)'
# orchestrator-service
```

部署前状态并发复审补充执行：

```bash
cd orchestrator_service
../algorithm-scheduling-platform/.venv/bin/python -m pytest -q \
  tests/test_visual_runtime.py
# 20 passed

cd ../algorithm-scheduling-platform
.venv/bin/python -m pytest -q \
  tests/integration/test_course_repository.py::test_visual_publish_reason_compare_and_set_preserves_newer_stage \
  tests/integration/test_visual_generation_runtime.py \
  tests/test_kafka_adapters.py
# 7 passed
```

单元测试在 Kafka `send_and_wait()` 返回前模拟 Vision 已写入“正在校验本地视频”；真实 PostgreSQL
测试验证同一事务锁下旧原因比较失败时既不覆盖新原因，也不覆盖新进度。

补充执行了平台非集成全量测试，结果为 `3226 passed, 40 failed, 3 skipped`。40 个失败分别属于
工作区其他并行变更中的迁移版本断言、VBas 部署配置旧字段、运维预检桩、注册 API 旧容量合同、
Pipeline `run_id` 桩、Dockerfile 旧阶段合同、旧 `tias` 身份断言和旧离线租约请求体断言；本变更
涉及的 Vision、Orchestrator、Kafka 适配器、真实视觉集成、A 服务查询契约均在上方独立命令通过。
本变更不修改或回滚这些并行开发文件，不能将该次仓库全量结果表述为全绿。

## 服务器缓存构建与容器替换

### 发布前资产与回滚边界

- 目标主机：`192.168.29.11`，`x86_64`；Docker Server `26.1.4/amd64`；发布前根分区
  可用约 `205 GiB`。
- 旧 Orchestrator 容器：
  `875c0ac2898469a06c219a4bd308206d7638d395e06bfe85406e89f64b99b140`，镜像
  `sha256:f730c4e6946e1048203578a5fe4829c274faa38a120f97ee9b82042678db27e5`，revision
  `f3329c61b775235421d93dae7dc1d44518e5b180`，发布前为 healthy。
- 旧 Vision 容器：
  `fa0c4a4579b0d3d863d2f981f719d3dfce1b3a87dffb5eb6cd26fbfb61cebc07`，镜像
  `sha256:f6513cc53933c2e61eec667562732cde81163a95c175cb3699a694d4f573e55a`，revision
  `88f83bf067ca93687700ec23b39a094463d70142`，发布前为 healthy。
- Orchestrator 配置 SHA-256 为
  `90748f310e456ebd16470b1bb084b35e788e7a2a1abdce82a2a72b4607b2c57c`；有效值保持
  `worker.node_concurrency=16`、`kafka.max_poll_records=10`。
- Vision 配置 SHA-256 为
  `770dfb1ce2d29ac3cc3cb685fecd482f537e52dd8089712ac3049a8f335f0b0c`；有效值保持
  `worker.concurrency=16`、`kafka.max_poll_records=2`、`scan.batch_size=8`、
  `scan.batch_prefetch=2`、`media.max_concurrent_processes=6`。
- 替换前曾将两个完整旧镜像 ID 分别标记为
  `algorithm-scheduling/orchestrator-service:rollback-pre-1ff4e20` 和
  `algorithm-scheduling/vision-orchestrator-service:rollback-pre-1ff4e20`。若门禁失败，精确回滚命令
  为将对应 rollback 标签重新标记为本轮运行标签，再用本轮 Compose 与配置 override 对单服务执行
  `docker compose up -d --no-deps --no-build --wait --wait-timeout 300 SERVICE`。本轮门禁通过后按任务
  要求已删除这些临时回滚标签和旧镜像，不能再把该发布前命令当作当前可执行回滚入口。

### 构建与镜像门禁

- GitHub Deploy Key 在服务器上不可用，直接 clone 返回 `Permission denied (publickey)`；改用本机
  对已推送分支生成完整 Git bundle 后通过 SCP 传输。服务器 release checkout 位于
  `/root/workspace/algorithm-scheduling-release-1ff4e20`，校验 HEAD 为
  `1ff4e2029b4664dd4ae7fe6f95bff6286d960f0b` 且工作树干净。
- 使用默认 BuildKit 缓存分别构建，未使用 `--no-cache`，未执行 prune：
  `algorithm-scheduling/orchestrator-service:v1.0_260904_1ff4e20` 与
  `algorithm-scheduling/vision-orchestrator-service:v1.0_260904_1ff4e20`。
- 新 Orchestrator 镜像 ID 为
  `sha256:ed38492f8a168502686a7bd584e3ad3640eb8ebff6f41929ac1b0fc34adb65c0`；新 Vision
  镜像 ID 为 `sha256:1b2043654845c43210be53989b2f192bbf6ee828d2128490ab2cd684e1291891`。
- 两镜像均为 `amd64`，OCI revision 均为目标完整 SHA，内置源码 manifest 校验通过；分别通过
  `app.main:app` 导入和实际挂载配置解析。最初的 Orchestrator 配置探针误用了 Vision 字段
  `worker.concurrency` 而失败，改为正确字段 `worker.node_concurrency` 后通过；该探针错误未启动或
  替换业务容器。

### 逐服务替换与运行门禁

- 先替换 Orchestrator，确认全部后台循环、PostgreSQL、Kafka Consumer 与 Control 检查 ready，
  再替换 Vision；未重启 PostgreSQL、Kafka、Control、算子或其他容器。
- 新 Orchestrator 容器 ID 为
  `36109732256f768ea71fe08e9e998e45fc68105e08f4d1fa0a3705f948df0f15`；新 Vision 容器 ID
  为 `030508d558a78c9239995dc18c2819b6961abfd9de69f9695ab1964b4fdee94d`。
- 两容器均从同一 release checkout 启动，均为 running/healthy；`/config/config.toml` 指向
  `/root/workspace/runtime-config/1ff4e20/` 下各自只读配置，`/data/course` 与 `/data/result`
  挂载保持不变。
- Orchestrator `/ops/readiness` 的 Outbox、课程 Consumer、节点执行器、视觉发布器、视觉事件
  Consumer、PPT 对账、PostgreSQL、Kafka 和 Control 全部 ready。Vision `/ready` 的视觉命令
  Consumer、PostgreSQL、Kafka 和 Control 全部 ready。
- Vision `/metrics` 已暴露命令 pending/in-flight、槽位利用率、媒体 pending/running 指标；空载值
  均为 0。新容器启动后的日志未发现 ERROR、CRITICAL、Traceback 或 Exception。

### 精确清理与验收结论

- Compose 原位替换已经删除两个旧容器；通过完整旧容器 ID 验证其不再存在。
- 新容器全部通过门禁后，仅删除上述两个旧镜像及其原标签、临时 rollback 标签；验证完整旧镜像
  ID 不再存在。没有删除其他容器、镜像、卷、课程结果或 BuildKit 缓存，也没有执行全局 prune。
- OpenSpec 的缓存构建、版本、架构、源码、配置挂载、health/readiness 和 Kafka Consumer 发布门禁
  已通过。最终业务压力测试仍为“待用户执行”；本次没有代替用户发起 20 路离线任务，也不将空载
  健康检查表述为吞吐验收通过。

## 后续抽帧与推理路径优化（`59c1761`）

### 实现内容

- 等间隔时间点改为一次 `ffmpeg` 连续解码和批量输出，避免每个时间点重复启动进程及从视频起点
  解码；不同课程和批次仍可并行，并统一受 `media.max_concurrent_processes` 限制。
- 批量抽帧异常时自动回退到受控的并行单帧抽取，保留已有任务的兼容和失败语义。
- 教师行为加密扫描按 `10s -> 5s 重新收敛 -> 2s 重新收敛` 推进，后一级只扫描上一级收敛后的
  边界范围，不再对全部 10 秒命中窗口重复扩散扫描。
- 学生行为优先复用 VBas 全画面结果中的 `ObjectPostList` 人员坐标，一次推理计算总人数及前后排
  人数；兼容不返回完整坐标的 VBas 实例，并在该情况下回退到原区域推理。
- 视觉节点进度统一更新为抽帧数、推理数和容量等待阶段，避免数据库继续展示已经结束的
  “等待 VBas”状态。
- 新增 `media.batch_extraction_enabled` 开关，默认开启，便于部署时受控回退。

### 本地验证

- Vision 服务测试：`98 passed`。
- 跨服务视觉相关测试：`14 passed`。
- `compileall`、`app.main:app` 导入、Ruff 和 `git diff --check` 均通过。
- 平台既有 `tests/test_vision_kafka_boundary.py` 仍按旧租约请求体断言，不接受当前已经存在的
  `capacity_pool="offline"`；该测试属于工作区其他并行变更，本轮未修改，也未将跨服务全量测试
  表述为全绿。

### 服务器构建与替换

- 目标主机：`192.168.29.11`，使用默认 BuildKit 缓存构建，未使用 `--no-cache`，未执行 prune。
- 发布提交：`59c176159f7e52881f2f1304bf2b085b6f27a6ad`；服务器 release checkout：
  `/root/workspace/algorithm-scheduling-release-59c1761`。
- 新镜像：`algorithm-scheduling/vision-orchestrator-service:v1.0_260904_59c1761`，镜像 ID
  `sha256:c014ee093ad60a327e537eb3e93cad78d0cc44dd514747b8c44194e7393448c5`，架构 `amd64`，
  OCI revision 与发布提交一致，源码 manifest、`app.main:app` 导入及 `ffmpeg` 可用性检查通过。
- 运行配置 SHA-256：`85bd32cca94b63ef3e293261c9c642f9436bb88bf082406e97f04bcf553d8590`；
  有效值为 `worker.concurrency=16`、`media.max_concurrent_processes=6`、
  `media.batch_extraction_enabled=true`。
- 新容器 ID：`96415983237e61a82dd4b36ec4e0d6f0f8b5a0f8c9b56da29de02cfe9fe7e2a4`；
  `/health` 返回 `ok`，`/ready` 中视觉命令 Consumer、PostgreSQL、Kafka 和 Control 均 ready，
  Kafka Consumer 已加入 `vision-orchestrator` 消费组并取得视觉命令分区。
- Compose 原位替换已删除旧容器；健康门禁通过后删除旧镜像
  `sha256:1b2043654845c43210be53989b2f192bbf6ee828d2128490ab2cd684e1291891`。未重启其他平台服务、
  算子或基础设施，未删除 BuildKit 缓存、课程数据或结果数据。
- 本轮按照用户要求不执行业务压力测试。代码、镜像、配置和服务健康门禁已通过；教师/学生行为
  端到端吞吐仍为“待用户验证”，需要依据用户后续测试结果判断是否继续调节 ffmpeg 并发数。
