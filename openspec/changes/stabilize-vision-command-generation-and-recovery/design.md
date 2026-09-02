## 背景

视觉节点由 `orchestrator_service` 的 `VisualNodeCoordinator` 领取并置为 `RUNNING(50)`，准备本地视频后向 Kafka 发布视觉命令；`vision_orchestrator_service` 消费命令、反复调用 VBas、写入进度并最终完成或失败节点。数据库已经保存 `attempt`、`claim_token` 和 `claimed_by`，但当前视觉命令未携带这些执行代次，Consumer 的进度、完成和失败写入也只按 `node_id` 判断。

与此同时，普通 `StaleNodeRecovery` 会扫描除 `PPT_SLICE` 外所有 `QUEUED(40)` 与 `RUNNING(50)` 节点。普通执行器和视觉协调器使用不同的 Worker ID，且视觉分析期间通常没有节点级算子租约，因此正在处理的视觉节点会在固定超时后被误认为失效并恢复到 `WAITING_OPERATOR(30)`，随后重新进入 `PENDING(10)`。旧 Kafka 命令继续运行时便会出现 `10 -> 70` 等非法写入。现场多次增长的 `attempt` 和 Vision Consumer unhealthy 已验证这条故障链路。

上一变更 `fix-vision-consumer-terminal-state-race` 只处理节点已经进入 `COMPLETED(60)`、`FAILED(70)` 或 `CANCELLED(80)` 后的迟到写入。本设计补充非终态回退、旧执行代次、视觉恢复所有权和镜像来源一致性，不替代上一变更的终态幂等语义。

## 目标与非目标

**目标：**

- 普通节点恢复器不再回收视觉节点，视觉命令的正常重投由 Kafka 负责。
- 每条视觉命令唯一绑定一次节点领取，Consumer 在模型执行前能够识别当前命令、陈旧命令和终态重复命令。
- 视觉进度、完成和失败结果只能由当前执行代次写入，竞态结果由数据库原子判定。
- 陈旧命令和单任务业务失败可被确认且不拖垮同批消息；真实基础设施故障继续阻止 offset 越过失败消息。
- 服务镜像中的实际源码能够与声明 revision 对应，远端验收可以证明运行的是本次修复。

**非目标：**

- 不开放 `PENDING(10) -> FAILED(70)` 或其他新的节点状态转换。
- 不改变 A 服务 HTTP 接口、任务状态值、VBas 接口、Kafka topic 名称和已有任务结果结构。
- 不把视觉聚合逻辑迁回 VBas，也不调整 VBas 在线/离线容量策略。
- 不引入 Kubernetes、分布式锁服务或新的数据库中间件。
- 不把所有 Consumer 异常吞掉；PostgreSQL、Kafka 和不可判定身份的基础设施错误仍需 fail-closed。

## 技术决策

### 1. 普通恢复器按节点所有权排除视觉节点

`list_stale_claimed_nodes()` 和 `recover_stale_claimed_node()` 均明确排除 `TEACHER_BEHAVIOR_ANALYSIS` 与 `STUDENT_BEHAVIOR_ANALYSIS`，形成查询和更新两层保护。不能只在 Python 循环中过滤，否则其他调用方仍可能误用 Repository 方法。

视觉命令成功发布后，Consumer 崩溃或未提交 offset 由 Kafka 重投，不需要按 `claimed_at` 周期回收节点。`VisualNodeCoordinator.recover()` 保留启动恢复职责：对于仍为 `RUNNING` 且具有完整 claim 身份的视觉节点，重新构造并发布同一代命令。多次启动恢复允许重复发布，但同一 claim 生成相同 `command_id`，最终由 Consumer 准入与 CAS 保证幂等。

备选方案是继续使用统一 120 秒超时并定期刷新视觉心跳。该方案把长视频分析时长与 Worker 存活错误绑定，还需要额外心跳一致性，本次不采用。

### 2. 视觉命令携带不可猜测的执行代次

内部 `VisualAnalysisCommand` 增加必填字段：

```json
{
  "dispatch_attempt": 4,
  "claim_token": "8bb2fbe8-..."
}
```

命令继续携带 `task_id`、`submission_id`、`node_id` 等现有身份。生成命令时必须从刚领取或恢复的 `NodeRecord` 读取 `attempt` 与 `claim_token`，不得重新生成 claim。`command_id` 使用 `submission_id + node_id + dispatch_attempt + claim_token` 稳定派生：同一代恢复重发保持同一 ID，重新领取后产生新 ID。

不使用 `node_id` 单独作为幂等键，因为同一节点可以合法重试；也不使用 Kafka offset 作为业务代次，因为重发会产生不同 offset。

### 3. 执行前以数据库权威状态进行准入分类

Consumer 在下载/抽帧和调用 VBas 前调用 Repository 校验命令身份，分类如下：

| 分类 | 数据库条件 | 处理方式 |
| --- | --- | --- |
| 当前命令 | 状态为 `RUNNING(50)`，任务/提交身份、`attempt`、`claim_token` 全部匹配 | 允许执行视觉分析 |
| 陈旧命令 | 节点存在，但状态为 `10/30/40`，或 `attempt`、`claim_token`、任务/提交身份不匹配 | 不调用 VBas、不修改节点，记录指标并允许提交 offset |
| 终态重复 | 状态为 `60/70/80` | 保持上一变更的终态幂等语义并允许提交 offset |
| 不可判定 | 节点不存在、数据库不可用、记录字段损坏 | 抛出基础设施错误，不提交 offset |

校验结果使用明确枚举或数据类表达，避免依赖异常文本。准入读取本身不能替代写入 CAS，因为分析期间仍可能发生并发状态变化。

### 4. 视觉写入全部采用执行代次 CAS

Repository 增加视觉专用条件操作，至少覆盖进度、完成和失败：

- `update_visual_progress_if_current(...)`
- `complete_visual_node_if_current(...)`
- `fail_visual_node_if_current(...)`

每次数据库更新都必须同时约束：

```sql
WHERE id = :node_id
  AND status = 50
  AND attempt = :dispatch_attempt
  AND claim_token = :claim_token
```

涉及 `node_results` 与 `task_type_runs` 的完成操作必须在同一事务内先锁定并确认当前代次，再写结果、推进节点、聚合任务类型和释放依赖。Repository 返回 `APPLIED`、`STALE`、`TERMINAL` 等结构化结果；节点不存在和数据库错误继续抛出异常。

Consumer 对 CAS 结果的处理为：`APPLIED` 正常继续；`STALE` 停止当前分析并允许确认消息；`TERMINAL` 按幂等结束。不能先 `get_node()` 再无条件 `transition_node()`，因为两次操作之间仍有竞态窗口。

### 5. 业务失败与基础设施失败分层

VBas 明确业务错误、媒体内容错误和参数错误在当前代次仍有效时，通过 `fail_visual_node_if_current()` 落为 `FAILED(70)`，聚合任务状态后允许提交消息。CAS 判定命令已陈旧时，不覆盖当前节点并允许提交消息。

PostgreSQL 连接失败、Kafka 事件发布失败、无法读取节点身份等错误继续从处理器抛出，由 Consumer 保持未提交 offset 和 not ready。当前批次取消行为只保留给这种需要 fail-closed 的基础设施故障。陈旧命令和已经成功落库的单任务业务失败不得进入该异常路径。

### 6. 旧格式消息采用停机同步迁移

新命令字段属于内部破坏性变更。发布端和消费端不得滚动混跑：先暂停新的视觉任务领取并停止两个服务，再部署包含相同公共契约版本的 `orchestrator_service` 与 `vision_orchestrator_service`。

新 Consumer 将缺少代次的旧格式视觉命令识别为 `LEGACY_STALE`，不执行推理并按分区连续提交；新 `VisualNodeCoordinator.recover()` 随后为数据库中仍然 `RUNNING` 且 claim 完整的节点发布带当前代次的新命令。缺少 claim 身份的历史运行节点必须通过显式恢复命令重新进入待调度状态，不能推断或伪造 token。

### 7. 构建来源使用“固定 checkout + 源码 manifest”双重证明

构建继续使用完整 Git SHA 的 detached checkout。构建前由该 checkout 生成源码 manifest，至少覆盖本次跨服务关键文件、服务 `app/` 和公共平台包；镜像同时保存 manifest、revision 和 manifest 哈希。部署前置检查需要：

1. 确认运行容器绑定不可变 image ID；
2. 确认 OCI revision 等于目标 Git SHA；
3. 在目标 checkout 重新计算期望 manifest；
4. 校验镜像内实际文件哈希与期望 manifest 一致。

只修改 revision 标签而未包含对应源码的镜像必须拒绝部署。构建缓存可以保留，但缓存命中不能跳过 manifest 校验。

## 风险与权衡

- [发布端和消费端命令版本不一致] -> 采用停机同步部署，并在启动前校验两镜像的公共契约版本和源码 manifest。
- [旧命令被确认后没有新命令] -> 启动恢复先枚举数据库中的运行视觉节点并产生恢复报告；只有新命令发布成功后才允许服务整体 readiness 通过。
- [排除普通恢复后视觉节点永久卡住] -> Kafka 负责已发布命令重投，视觉协调器负责启动时重发；缺失 claim 的异常节点由显式恢复操作处理并记录审计证据。
- [准入读取后状态再次变化] -> 所有进度、完成和失败写入继续使用数据库 CAS，不依赖前置读取保证正确性。
- [重复命令造成重复模型计算] -> 准入能拦截已陈旧或已终态命令；同一当前代命令极端并发时，CAS 保证结果不覆盖，但可能产生一次额外推理，后续可增加执行中 command 去重指标评估。
- [基础设施故障阻塞同分区后续消息] -> 保持 Kafka 分区顺序和 fail-closed，这是正确性优先的权衡；通过 readiness、错误指标和告警缩短故障时间。
- [源码 manifest 增加构建复杂度] -> 复用现有固定 SHA checkout 与 preflight 脚本，只增加确定性哈希生成和校验，不引入外部签名服务。

## 迁移计划

1. 完成公共契约、Repository、Orchestrator、Vision Consumer 和部署门禁的自动化测试。
2. 在 `192.168.29.11` 停止新的视觉任务提交，保存 PostgreSQL、Kafka Consumer Group lag、运行视觉节点和当前镜像 ID/revision 证据。
3. 停止 `orchestrator_service` 与 `vision_orchestrator_service`，从明确 Git SHA 的干净 release checkout 构建新镜像；保留构建缓存。
4. 校验新镜像 revision、源码 manifest 和关键文件哈希后，同步启动两个服务。新 Consumer 确认旧格式命令，新协调器重发当前代命令。
5. 验证 `/ready`、视觉命令 Consumer Group、节点 `attempt`、claim 身份和任务结果，再删除旧容器与旧镜像；不要提前删除可回滚镜像。
6. 依次执行视觉单任务、16 路全量任务以及在线人数 300 并发/30000 请求混合回归，记录 Harness 和 OpenSpec 验收证据。
7. 若 readiness、manifest 或状态一致性任一门禁失败，停止新服务并恢复旧镜像；数据库字段未迁移，回滚不需要 DDL 回退。已产生的新格式内部消息只能由新 Consumer 处理，因此回滚前必须暂停并清理或完成该批视觉命令。

## 验收标准

验收必须在 `192.168.29.11` 按以下门禁顺序执行，前一阶段未通过不得进入下一阶段。

### 1. 镜像替换与旧版本清理

- 从同一个目标 Git SHA 和干净 release checkout 构建 `orchestrator_service` 与 `vision_orchestrator_service` 新镜像，允许保留 Docker 构建缓存。
- 新容器必须通过 image ID、revision、源码 manifest、health 和 readiness 校验；运行容器内的视觉命令结构必须包含 `dispatch_attempt` 与 `claim_token`。
- 新容器确认正常后删除被替换的旧 Orchestrator/Vision 容器和旧镜像，只保留本次目标版本；不得在新版本通过门禁前删除唯一可回滚版本。

### 2. 原状态机故障定向复现

- 使用受控视觉任务覆盖运行时间超过 `stale_node_recovery_seconds`、旧执行代次命令到达、终态迟到命令和 Consumer 重启重投场景。
- 超过普通恢复阈值后，当前视觉节点的 `status`、`attempt`、`claim_token` 与 `claimed_by` 不得被普通恢复器修改。
- 旧 `attempt` 或旧 `claim_token` 的命令必须在调用 VBas 前被识别并确认，不得改写当前节点，不得导致同批后续消息取消。
- 定向复现期间 `/ready` 必须持续为 ready，Kafka lag 必须最终收敛，日志中不得再次出现 `节点状态不允许从 10 转换到 70`、`10 -> 70` 或由该冲突导致的 Vision Consumer unhealthy。
- 以上任一条件失败即判定本变更未通过，停止后续混合压力验证并保留现场证据。

### 3. 混合压力验收

- 在定向复现全部通过后，同时启动 16 路全量课程任务和在线 VBas 人数识别 300 并发、30000 总请求。
- 16 路全量任务的 PPT、ASR、教师行为和学生行为节点必须分别记录提交、开始、完成和总耗时；教师与学生视觉节点不得发生周期性重领，当前测试中的正常节点 `attempt` 应保持单次领取值。
- 在线人数识别报告必须记录总请求数、完成数、业务成功数、失败数、吞吐量、总耗时以及延迟分位值；30000 个请求均必须得到可判定结果，不得因 Vision Consumer 状态冲突失败。
- 压测全程采集三个 VBas 实例的 GPU 利用率与显存、Vision readiness、容器重启次数、视觉节点状态/代次、Consumer Group lag 和状态冲突日志。
- 压测结束后 Vision Consumer 必须仍为 ready，容器不得因状态冲突重启，视觉 Kafka lag 必须回落到测试前基线或零，数据库和日志不得出现原状态机故障。

### 4. 验收报告

最终报告必须给出镜像 ID/revision/manifest、旧版本删除清单、定向复现步骤与证据、16 路全量分泳道结果、在线 300 并发/30000 请求指标、GPU/显存变化、节点状态与 `attempt` 变化、Kafka lag、readiness 时间线和明确的通过/不通过结论。Harness 保留原始命令、脱敏日志、数据库快照和机器可读统计文件，不得只给出口头结论。

## 待确认问题

无。执行阶段不得以放宽状态机转换或手工修改任务终态替代上述一致性修复。
