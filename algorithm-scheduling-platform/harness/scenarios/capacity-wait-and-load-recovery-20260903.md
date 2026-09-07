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

### 9.5 Gateway 实例排除修复发布与三路恢复验证

- 修复提交：`8e51e55bcd199895b31471a41e9da7f9a972bdf4`。新 Gateway 镜像完整 ID 为
  `sha256:36b7ce1c1b339b3fd53de0bcff74cf888c9c9cb8951b3f969f45f5d883ccd9a3`，新容器完整 ID 为
  `c22d736f88f9349fd2f4baacdcdc71b55fff89c5b7686b607f19bdae6ef7311a`；amd64、revision、源码
  manifest、compile、导入、health、readiness 和真实人数 Smoke 全部通过。
- 构建与替换证据位于 `gateway-reroute-fix-20260903T142930Z/`。首次 Compose 解析因未注入注册管理
  token 在替换前 fail closed，旧容器始终健康；重试时只在远端进程内从现有 Control 容器读取并
  注入 token，未写入报告。新容器门禁通过后精确删除旧 Gateway 镜像，BuildKit 缓存保留。
- 人数路由 attempt `online-person-count-fault-20260903T143240Z`：200/200 成功，在已有活动租约后
  中断 `vbas-gpu1`，产生 62 条算子调用重选事件，实例恢复 healthy。
- 教师路由 attempt `online-teacher-fault-20260903T143325Z`：200/200 成功，在已有活动租约后中断
  `vbas-gpu2`，产生 137 条算子调用重选事件，原 VBas 响应保持兼容。
- 学生路由首次 attempt `online-student-fault-20260903T143427Z` 以 200 并发执行故障注入，将流量压到
  剩余两台学生模型，触发明确 CUDA `OutOfMemoryError`：24/200 成功、176 个业务码 `50000`。该轮
  已按门禁停止、保存 GPU/进程/容器/OOM/租约现场，并重启三台 VBas 重置显存，原始失败不得覆盖。
- 学生路由恢复语义重跑 `online-student-fault-20260903T143817Z` 使用 3 个请求：3/3 成功，已有活动
  租约后中断 `vbas-gpu2`，产生 1 条重选事件并恢复 healthy。正式高并发验收只针对纯人数路由，
  不把学生行为模型的 200 并发 OOM 包装为平台通过。
- 三路完成后，三个 VBas 均为 `ONLINE/model_ready=true`，每实例 offline=1、online=24，活动租约、
  reported inflight 和实例上报 online/offline inflight 全为 0。判定：任务 11.1 通过；后续在线人数
  分档仍须重新创建三实例并建立独立冷启动 GPU 基线。

### 9.6 在线人数识别第一档压力

- Run ID：`online-tier1-72x5000-20260903T144318Z`；原始证据位于
  `/root/workspace/.algorithm-scheduling-restricted-reports/stabilize-capacity-wait-and-load-recovery/online-tier1-72x5000-20260903T144318Z/`。
- 本轮先强制重新创建三个 VBas，全部 ready 后完成 5 分钟冷启动稳定观察；全程每 2 秒采集三卡
  显存、利用率、功耗、GPU 进程、目标容器完整 ID、注册实例、活动租约、平台队列和磁盘余量。开始、
  结束容器事实与配置摘要一致，未发生中途替换。
- 请求结果：并发 72、总请求 5000，HTTP 200 为 `5000/5000`，业务码 0 为 `5000/5000`，失败分类
  为空；总耗时 `88.9512` 秒，吞吐 `56.2106 req/s`，延迟 P50/P95/P99 分别为
  `1.1407/2.2264/2.9446` 秒。
- 按 VBas 日志中的唯一 TaskID 统计，`vbas-gpu0/gpu1/gpu2` 分别处理 `1624/1682/1694` 个请求，
  合计 5000；三实例采样活动租约峰值分别为 `14/15/15`，不存在固定实例独占。派生统计保存在本轮
  `instance-distribution.json`，不覆盖原始日志。
- 三卡冷启动基线显存中位数分别为 `11037/11023/10439 MiB`，负载峰值与 5 分钟恢复中位数均未高于
  各自基线，恢复差值均为 0；未发现 CUDA OOM、GPU Xid 或容器重启。
- 最终活动租约、VBas inflight、未发布 Outbox 和平台队列均为 0，Kafka Consumer lag 收敛；运行事实
  未变化。判定：在线第一档通过。该结论只覆盖 72 并发、5000 次，不能替代后续 144 并发、三轮
  200 并发、离线全量和两轮混合验收。

### 9.7 在线人数识别第二档压力

- Run ID：`online-tier2-144x10000-20260904T010705Z`；原始证据位于本变更受限报告根目录下的同名
  write-once 目录。本轮重新创建三个 VBas，完成 5 分钟冷启动基线、2 秒连续采样、负载、运行态
  收敛和 5 分钟恢复观察，开始与结束运行事实及配置摘要一致。
- 请求结果：并发 144、总请求 10000，HTTP 200 与业务码 0 均为 `10000/10000`，失败分类为空；
  总耗时 `207.9014` 秒，吞吐 `48.0997 req/s`，延迟 P50/P95/P99 分别为
  `2.5789/5.8612/7.8581` 秒，最大 `13.1259` 秒。
- 按 VBas 日志中的唯一 TaskID 统计，`vbas-gpu0/gpu1/gpu2` 分别处理 `3351/3378/3271` 个请求，
  合计 10000，三实例均接受真实工作且无固定实例独占；派生统计保存于本轮
  `instance-distribution.json`。
- 三卡冷启动基线、负载峰值和恢复中位数分别维持在 `11037/11023/10439 MiB`，恢复差值均为 0；
  未发现 CUDA OOM、GPU Xid 或容器重启。最终租约、VBas inflight、Outbox、平台队列和 Kafka lag
  收敛，判定第二档通过。

### 9.8 在线 200 并发首次 attempt 环境失效

- Run ID：`online-tier3-r1-200x10000-20260904T012208Z`。业务请求本身全部成功：HTTP 200 与业务码 0
  均为 `10000/10000`；耗时 `247.5836` 秒，吞吐 `40.3904 req/s`，P95/P99 为
  `11.0056/16.7650` 秒，最终运行态收敛且无 OOM、Xid 或容器重启。
- 本轮 GPU0 恢复中位数比采样基线高 `938 MiB`，超过 512 MiB 门禁，原始汇总据此正确判为失败；
  GPU1/GPU2 恢复差值为 0。进一步核对 VBas 进程显存时序，GPU0 的 VBas 在首次正式人数请求后从
  基线中位数 `1252 MiB` 增至 `2190 MiB` 并稳定，符合首次推理惰性 CUDA 分配特征。
- 基线窗口并非空闲：检测到外部 TaskID `zhang111` 在 GPU1 和 GPU2 各进入一次原始请求及一次区域
  子任务，使两卡在正式负载前已分别完成不同程度的惰性内存分配；GPU0 没有收到该请求。三卡基线
  因而不可比较，该 attempt 同时标记为“显存门禁失败”和“外部流量导致环境失效”，不得计入三轮
  200 并发通过记录。
- 测试执行器调整为：重新创建后先用 72 个本轮专属人数请求预热，并证明三个实例均有命中；预热
  租约收敛后才开始 5 分钟基线。基线窗口若出现任何非预热请求，立即写入环境失效事实并停止正式
  发压。后续须以新 Run ID 从 200 并发第一轮重新执行，不能沿用本轮部分结果。

### 9.9 预热基线门禁脚本误判

- Run ID：`online-tier3-r1-retry-200x10000-20260904T013954Z`。预热 72 个请求全部成功，三个 VBas
  分别处理 `25/20/27` 个请求，且预热租约收敛。
- 脚本完成 5 分钟基线后检测到 3 条 `Received TaskID` 并按设计在正式发压前退出，未产生 10000 次
  正式负载。核对 TaskID 后确认三条均为同一 Run ID 下延迟刷盘的预热请求，不是外部流量，属于
  验收脚本误判；该 attempt 记录为中断，不计入 200 并发正式轮次。
- 修正方式是在预热收敛后增加日志排空等待，并在基线外部请求计数中排除本轮专属
  `${run_id}-warmup` 前缀。真正不属于预热前缀的请求仍会触发环境失效门禁。

### 9.10 在线 200 并发有效轮次 1

- Run ID：`online-tier3-r1-retry2-200x10000-20260904T014628Z`。预热请求 `72/72` 成功并严格覆盖
  三个 VBas 各 24 次；预热租约收敛后完成 5 分钟无外部请求基线。
- 正式请求 HTTP 200 与业务码 0 均为 `10000/10000`，失败分类为空；耗时 `242.6369` 秒，吞吐
  `41.2138 req/s`，P50/P95/P99 为 `3.8771/10.5751/15.7669` 秒。
- 三实例按唯一 TaskID 处理 `3285/3279/3436` 个请求，合计 10000，均有真实工作且无固定实例独占。
- 三卡基线、峰值和 5 分钟恢复中位数均为 `11037/11023/10439 MiB`，恢复差值均为 0；运行事实
  未变化，最终租约、VBas inflight、平台队列、Outbox 与 Kafka lag 收敛，无 OOM、Xid 或重启。
  判定：200 并发第 1 个有效轮次通过。

### 9.11 在线 200 并发有效轮次 2

- Run ID：`online-tier3-r2-200x10000-20260904T020216Z`。预热、三实例覆盖、5 分钟无外部请求基线、
  2 秒连续采样和运行事实门禁均有效。
- 正式请求 HTTP 200 与业务码 0 均为 `10000/10000`，失败分类为空；耗时 `241.9490` 秒，吞吐
  `41.3310 req/s`，P50/P95/P99 为 `3.8657/10.5148/16.3442` 秒。
- 三实例处理 `3313/3371/3316` 个唯一 TaskID，合计 10000；三卡基线、峰值和恢复中位数均为
  `11037/11023/10439 MiB`，恢复差值均为 0。运行事实未变化，所有运行态收敛，无 OOM、Xid 或
  重启。判定：200 并发第 2 个有效轮次通过。

### 9.12 在线 200 并发有效轮次 3

- Run ID：`online-tier3-r3-200x10000-20260904T021750Z`。预热、三实例覆盖、空闲基线、采样和运行
  事实门禁均有效。
- 正式请求 HTTP 200 与业务码 0 均为 `10000/10000`，失败分类为空；耗时 `242.1612` 秒，吞吐
  `41.2948 req/s`，P50/P95/P99 为 `3.8836/10.5685/16.1406` 秒。
- 三实例处理 `3290/3363/3347` 个唯一 TaskID，合计 10000；三卡基线、峰值和恢复中位数均为
  `11037/11023/10439 MiB`，恢复差值均为 0。运行事实未变化，所有运行态收敛，无 OOM、Xid 或
  重启。判定：200 并发第 3 个有效轮次通过。
- 三个有效 200 并发轮次连续获得 100% 业务成功率，吞吐稳定在 `40.39~41.33 req/s`，三个 VBas
  每轮均接受真实工作且无固定实例独占。此前显存失败和脚本误判 attempt 均单独保留，不计入三轮
  通过结果。

### 9.13 在线人数压力工具纳管（2026-09-07）

- 工作区新增 `tests/online_person_count_load.py`，通过 Online Gateway
  `/online/vbas/person-count` 发送单图请求，可显式设置并发数、总请求量、超时、图片、任务前缀和
  汇总 JSON 输出路径。
- 工具只输出 HTTP 状态、业务码、错误分类、吞吐和延迟统计，不保存图片 Base64 或 VBas 响应正文。
- 使用平台 `.venv` 执行 `compileall` 和 `--help` 通过，默认真实图片
  `vbas/tests/teacher_person_count/frame_000068.jpg` 存在且非空。
- 本节只证明压力工具已纳入版本控制并可启动，不新增远端压力通过结论；9.6 至 9.12 的运行结果仍以
  各自 write-once Run ID 和受限证据目录为准。
