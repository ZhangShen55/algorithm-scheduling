# 容量等待与混合负载恢复验证（2026-09-03）

## 1. 目标与当前状态

本 Harness 对应 OpenSpec `stabilize-capacity-wait-and-load-recovery`。目标是修复 Vision 在 Control
瞬时不可达时错误终止视觉节点，以及 Online Gateway 在正常容量竞争或下游瞬时故障时过早返回 A
服务的问题，并在固定运行版本上完成故障注入、在线分档、离线基线、两轮混合压力、GPU 恢复和
媒体生命周期验收。

当前状态：本地实现与本地门禁已完成；远端发布和正式压力结果尚未写入。未完成全部门禁前，本文件
不得给出变更通过结论。

## 2. 不可覆盖的失败基线

- 历史 Run ID：`cleanup-mixed-20full-200x10000-20260903-174427`。
- 历史证据：`harness/scenarios/mixed-load-media-cleanup-validation-20260903.md`。
- 在线人数识别：HTTP `10000/10000`，业务成功 `9884`，业务失败 `116`，该轮未通过。
- Vision：两个 `TEACHER_BEHAVIOR_ANALYSIS` 节点在 Control 热替换窗口发生租约连接失败。
- 旧 Control 容器：`52bca0eb13a389cd9b02ac319f3d8db6b8312bce13f39a81de0cbcbe87ad6683`。
- 新 Control 容器：`e0f355d65302675718b0c26cc8f31828bca99067bed0c043bfa2aa82448674d2`。
- 历史运行中服务版本发生变化，因此该轮既是有效缺陷证据，也是环境失效的正式验收 attempt；禁止
  覆盖、删除或改写其原始报告。

## 3. 实现边界与错误矩阵

本变更不修改接口路径、请求字段、成功响应、四服务边界、VBas 模型或推理逻辑。三个 VBas 的权威
边界为每实例 offline `1`、online `24`、内部 online queue `24`；Control 每实例最多发放 24 个
online pool 租约，内部队列不计入平台注册容量。

| 阶段 | 条件 | 是否恢复 | 最终结果 |
| --- | --- | --- | --- |
| 租约申请 | 容量暂不可用 | 在累计预算内退避 | 在线 `50301`；Vision 中文失败终态 |
| 租约申请 | 建连/连接超时、HTTP 502/503/504 | 在累计预算内退避 | 在线 `50302`；Vision 中文失败终态 |
| 租约申请 | HTTP 4xx 或非法响应 | 快速失败 | 在线 `50000`；Vision 中文失败终态 |
| VBas 调用 | 建连/复位、429/502/503/504、读取超时、协议错误 | 最多三次并重选实例 | `50201` 或 `50401` |
| VBas 调用 | 图片/坐标错误、HTTP 400/422 | 不重试 | `40001` |
| VBas 调用 | 其他确定性/未分类错误 | 不重试 | `50000` |
| 租约释放 | 404 | 幂等成功 | 保留原业务结果 |
| 租约释放 | 瞬时失败耗尽 | TTL 回收并告警 | 不覆盖原业务/分析根因 |
| 上游取消 | 容量等待或 VBas 调用中取消 | 传播取消 | 释放已取得租约，无后台调用残留 |

## 4. 本地失败先行与回归证据

- 旧 Vision 聚焦测试：`10 failed, 6 passed`；证明连接失败、可恢复 5xx 等路径未实现。
- 旧 Gateway 测试收集失败；证明类型化租约异常尚不存在。
- Vision 容量聚焦回归 `52 passed`；Vision 全量回归 `79 passed`。
- Online Gateway 容量与路由聚焦回归 `55 passed`；纳入真实
  `frame_000068.jpg` Smoke 和镜像源码 manifest 合同后，Gateway 全量回归为 `93 passed`。
- 平台共享租约、指标、日志、Gateway 合同聚焦回归 `47 passed`；Redis/Control 单实例 online=24
  上限 `1 passed`；Vision VBas 与三调用方跨服务租约 `2 passed`。
- 变更 Python 文件 Ruff 通过；`mypy --strict --follow-imports=silent` 检查 8 个变更源文件通过；
  compileall 与两个 `app.main:app` 导入通过；OpenSpec strict 与 `git diff --check` 通过。
- 两个服务均以真实 Uvicorn 进程启动。Gateway `/health`、`/ready` 为 200；Vision 使用 Mac 可写的
  `/tmp` 存储覆盖后连接本机 PostgreSQL/Kafka，`/health`、`/ready` 为 200，并完成优雅停止。
- 真实图片 Smoke 使用 `vbas/tests/teacher_person_count/frame_000068.jpg`，验证图片解码、原请求转发、
  VBas 原始成功响应和租约边界；远端发布后仍须调用真实 VBas，不能以本地 Mock 代替远端门禁。
- 平台全量非集成回归完整执行结果为 `3228 passed, 39 failed, 3 skipped, 172 deselected`。39 项属于
  当前分支既有非本变更基线，集中在迁移 0008/0009 后的旧断言、旧 VBas 配置名、并行中的部署/
  运维脚本测试、旧 Control HTTP 状态断言、Pipeline `run_id` 测试桩和旧 Dockerfile 合同；本变更
  不修改这些文件，不将该组失败包装为通过。全导入严格 Mypy 还会报告既有 `repository.py` 和
  Vision `events.py` 类型债务，限定本 change 的 8 个源文件检查已通过。
- `test_unified_capacity_cross_service.py` 中旧 OCR 容量用例仍假设容量立即失败，并把 default pool
  租约当作 online pool；该历史断言与本变更 VBas 有界等待语义无关，已单列为非本变更项，未改写。
- 日志只允许记录 trace、capability、pool、stage、exception type、attempt、已耗时、剩余预算和
  outcome；Base64、完整请求/响应、识别文本与 embedding 均为禁止字段。

## 5. 远端发布门禁

目标服务器为 `192.168.29.11`。每次发布前将完整 Git SHA、受影响服务旧容器/镜像完整 ID、OCI
revision、实际配置 SHA-256、健康状态和磁盘空间写入独立 release 目录。使用干净 checkout 与现有
BuildKit 缓存构建，不使用 `--no-cache`，不执行宽泛 container/image/system/buildx prune。

全部新镜像完成 amd64、revision、source manifest、compile 和 import 校验后逐个替换。新容器通过
health、readiness、注册和真实 Smoke 后，精确删除被替换的旧容器及无引用旧镜像；无关镜像、构建
缓存、数据卷、历史证据和 `/data/result` 保留。

## 6. 正式测试矩阵

| 顺序 | 场景 | 规模 | 通过条件 |
| ---: | --- | --- | --- |
| 1 | Control 短暂不可用 | 5、15、30 秒各一轮 | Vision 全成功、attempt=1、batch 不重复、全部收敛 |
| 2 | 三条 VBas 在线路由恢复 | 人数/教师/学生真实图片 | 响应兼容、重选有效、租约归零 |
| 3 | 在线分档 | 72x5000、144x10000 | HTTP/业务成功 100%，错误分类为 0 |
| 4 | 在线重复压力 | 200x10000 连续三轮 | 每轮完整终态和显存恢复 |
| 5 | 离线基线 | 20 路四任务 | 80 类任务成功、节点 attempt=1、媒体清理 |
| 6 | 最终混合 | 20 路四任务 + 200x10000，连续两轮 | 在线 100%，80 类任务成功，三卡均有真实工作 |

## 7. 运行事实、GPU 与存储证据

每个 attempt 使用唯一 Run ID 和 write-once 目录。开始及结束均保存容器完整 ID、镜像 revision、
配置摘要和媒体输入；测试期间周期复核，任何变化均将 attempt 标记为环境失效。

每组 VBas 正式压力前重新创建三个实例，ready 后空闲 5 分钟，以最后 60 秒显存中位数为基线。
负载期间每 2 秒保存三卡显存、利用率、功耗、PID 到容器完整 ID、在线/离线租约及 VBas
running/queued；终态后继续观察 5 分钟。恢复值超过基线 512 MiB、跨轮单调增长、OOM、GPU Xid、
容器重启或剩余显存低于 2 GiB 时该轮失败并停止扩大负载。

存储采集使用 `df` 和本轮 `/data/course/{task_id}` 增量，不高频递归扫描整个 `/data/result`。
终态必须确认 `slides.mp4`、`teacher.wav`、`teacher.mp4`、`student.mp4` 和视觉临时目录按消费者终态
删除，课程临时目录消失；`/data/result/{task_id}`、数据库结构化结果和失败事实保留。

## 8. 收敛与结论规则

每轮必须等待全部在线响应、课程/节点终态、活动租约、reported inflight、VBas running/queued、
三个 Kafka Consumer lag、未发布 Outbox 和本轮课程缓存目录归零。驱动中断、监控缺失、运行事实
变化或任何未收敛项只能记录为失败、未完成或环境失效。只有全部必需轮次及 GPU 恢复门禁通过后，
才能在本文件追加最终“通过”结论。

## 9. 执行结果

### 9.1 发布前快照与干净源码

- Run ID：`stabilize-capacity-20260903T130714Z`。
- 发布前原始证据：
  `/root/workspace/.algorithm-scheduling-restricted-reports/stabilize-capacity-wait-and-load-recovery/stabilize-capacity-20260903T130714Z/release-preflight/`。
- 该目录已保存四个平台容器与镜像完整 inspect、OCI revision、实际 `config.toml` SHA-256、健康与
  readiness、三卡 `nvidia-smi`、磁盘和 Docker 空间、Git 状态及媒体源响应；初次误探测 Control 和
  Orchestrator 的 `/ready` 得到 404，已原样保留，并补采正确的 `/ops/readiness` 成功事实。
- 发布前四平台、三 VBas 和全部中间件均为 healthy；三张 GPU 可见，`/data/course`、
  `/data/result` 可写，媒体源 `192.168.29.12:5555` 可达，根文件系统剩余约 209 GiB。
- 服务器没有可用的 GitHub SSH 凭据，直接 fetch 失败。未修改现有运行目录，改由本机已验证仓库
  生成 Git bundle，服务器完成 bundle 校验后建立独立 clean checkout
  `/root/workspace/algorithm-scheduling-release-6722e28`，冻结 SHA 为
  `6722e285d0287ac7349083ccd093d8596ae2eb5e`。
- 发布前复核发现 Online Gateway Dockerfile 缺少源码 manifest 门禁；已补充 `/app/app` 与
  `/app/packages` manifest 生成和测试。后续构建必须使用包含该修复的新最终 SHA，不能继续使用
  `6722e28`。

### 9.2 缓存构建、替换与发布门禁

- 最终发布 SHA：`88f83bf067ca93687700ec23b39a094463d70142`。实际受恢复逻辑影响的镜像为
  Vision Orchestrator 和 Online Gateway；两者均从该 SHA 的 clean checkout 构建，未使用
  `--no-cache`，未执行 image/buildx prune。
- 新 Vision 镜像完整 ID：
  `sha256:f6513cc53933c2e61eec667562732cde81163a95c175cb3699a694d4f573e55a`；新 Gateway 镜像完整
  ID：`sha256:fea1b461a6cdb28cdd8407f2692474f591b102068fc6e959c7d4445ef3ad48e4`。两者均为
  `linux/amd64`，OCI revision 与最终 SHA 一致，容器内 compile、`app.main:app` 导入和实际
  `/app/app`、`/app/packages` manifest 校验全部通过。
- 新 Vision 容器完整 ID：
  `fa0c4a4579b0d3d863d2f981f719d3dfce1b3a87dffb5eb6cd26fbfb61cebc07`；新 Gateway 容器完整
  ID：`ea71ea2f2e59f2c42dee4bdad48ecfe8ea665af2fbb37b02d3d21fc1d4c0f449`。两者 health/readiness
  均通过，Vision 继续使用 `worker.concurrency=16` 和 ffmpeg 并发 `6`；Gateway 使用 600 秒总预算。
- 真实图片 `frame_000068.jpg` 经 `/online/vbas/person-count` 返回 HTTP 200、VBas
  `ErrCode=0`。短视频 `0912-360-410-S.mp4` 的 `STUDENT_BEHAVIOR` Smoke 在约 10 秒内按
  `10 -> 50 -> 60` 达到成功终态；结束时 Outbox、平台队列和三实例活动租约均为 0。
- 三个 VBas 均保持 `ONLINE`、`model_ready=true`、offline 容量 1、online 容量 24。发布日志未
  检出 Base64/Data URI。新版本门禁通过后，已按完整 ID 删除旧 Vision 镜像
  `sha256:8e8700...`；旧 Gateway 镜像 `sha256:10f61c...` 因有两个标签，先核对无容器引用，再
  精确解除两个标签并删除。无关镜像、数据卷、历史证据和 BuildKit 缓存均保留。
- 原始构建、镜像 inspect、manifest、部署、Smoke、租约与清理证据位于同一 Run ID 下的
  `build/` 和 `deploy/` 目录；首次 Smoke 因 clean checkout 不包含未跟踪图片而在发请求前失败，
  已保留事实，并将本机同一 fixture 传至受限测试目录后成功重跑。

后续每个正式 attempt 按 Run ID 继续追加原始报告路径、请求规模、完整错误分类、任务终态、实例
分布、GPU 基线/峰值/恢复值、存储清理和最终判定；不得覆盖前一轮记录。

### 9.3 Control 短暂不可用故障注入

- 原始证据根目录：
  `/root/workspace/.algorithm-scheduling-restricted-reports/stabilize-capacity-wait-and-load-recovery/`。
- 首次 5 秒 attempt `control-fault-5s-20260903T135756Z` 的业务任务实际成功，数据库节点为状态 60、
  `attempt=1`；但验收脚本误读北向接口未暴露的节点 `attempt` 字段而退出。该 attempt 原样保留为
  “验收脚本失败”，未覆盖或改写，并使用全新 task_id 执行正式 5 秒重跑。
- 正式 5 秒 attempt `control-fault-5s-retry-20260903T140203Z`：任务
  `capwait-fault-5-retry-140203` 成功；观察 50 个唯一逻辑 `work_id` 和 50 个唯一租约，不存在同一
  `work_id` 关联多个租约或实例；Vision 记录 21 条 `control_transient_failure` 恢复事件。
- 正式 15 秒 attempt `control-fault-15s-20260903T140531Z`：任务 `capwait-fault-15-140531`
  成功；观察 47 个唯一逻辑 `work_id` 和 47 个唯一租约，无重复逻辑工作；Vision 记录 31 条
  `control_transient_failure` 恢复事件。
- 正式 30 秒 attempt `control-fault-30s-20260903T140758Z`：任务 `capwait-fault-30-140758`
  成功；观察 44 个唯一逻辑 `work_id` 和 44 个唯一租约，无重复逻辑工作；Vision 记录 52 条
  `control_transient_failure` 恢复事件，单个逻辑工作的最高恢复尝试序号为 19。
- 三档任务的 PostgreSQL 节点事实均为 `STUDENT_BEHAVIOR_ANALYSIS / 60 / attempt=1 / 视觉分析完成`。
  每档最终 Kafka lag、活动租约、reported inflight、Outbox 与平台队列均为 0，
  `/data/course/{task_id}` 均已删除；测试前后容器 ID 与配置摘要保持一致。
- 正确解析结果保存在每个正式 attempt 的 `task-lease-summary-v2.json`，对应的脱敏恢复原始事件保存
  在 `vision-recovery-events-v2.jsonl`。旧的错误派生文件 `task-lease-summary.json` 保留作为脚本缺陷
  证据，不作为验收依据。
- 判定：5、15、30 秒三档 Control 暂时不可用均通过。该结论只覆盖 Vision 租约申请恢复，不替代后续
  三条在线路由、在线分档、离线全量、两轮混合负载和 GPU 恢复验收。

### 9.4 在线实例中断失败先行与修复

- `online-person-count-fault-20260903T141924Z`、`online-person-count-fault-20260903T142025Z` 和
  `online-person-count-fault-20260903T142130Z` 分别在请求前停止一个 VBas。由于停止完成后心跳已
  过期，Control 正确排除了该实例，全部请求成功但没有产生算子调用重选事件；三轮均保留为“未命中
  故障”的无效恢复 attempt，不作为通过证据。
- `online-person-count-fault-20260903T142257Z` 在观察到目标实例存在活动在线租约后立即停止
  `vbas-gpu0`，真实命中已获租约后的算子中断。200 个请求中 193 个成功、7 个返回业务码 `50201`，
  Gateway 记录 88 条重选事件；其中 7 个请求的三次调用全部再次选择 `vbas-gpu0` 后耗尽。
- 根因不是 VBas 在线容量不足，而是失败实例停止上报后 `reported_inflight=0`，在心跳 TTL 到期前
  反而持续成为最低负载候选；旧 Gateway 没有把同一请求已失败的实例传入后续租约选择边界。
- 修复方式：Gateway 为单个请求维护失败实例集合；租约客户端再次取得集合内实例时立即释放租约并
  在原 600 秒总预算内继续等待其他实例，跳过动作不消耗最多三次的真实算子调用次数。该行为不修改
  北向请求/响应或 Control 内部租约合同。
- 本地新增失败实例排除与租约释放测试，Gateway 全量回归为 `95 passed`，严格 Mypy、Ruff、
  compileall 和 `app.main:app` 导入通过。远端须重建 Gateway 后从人数故障档重新执行，再继续教师与
  学生路由；本节明确记录当前失败，不给出在线恢复通过结论。
