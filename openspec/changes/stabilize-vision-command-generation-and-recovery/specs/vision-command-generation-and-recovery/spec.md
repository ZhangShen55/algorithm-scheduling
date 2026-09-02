## ADDED Requirements

### Requirement: 普通恢复器不得回收视觉节点
系统 MUST 在普通过期节点查询和条件恢复更新两层排除 `TEACHER_BEHAVIOR_ANALYSIS` 与 `STUDENT_BEHAVIOR_ANALYSIS`，视觉节点 SHALL 仅由视觉专用恢复流程管理。

#### Scenario: 视觉节点超过普通恢复超时
- **WHEN** 视觉节点保持 `RUNNING(50)` 的时间超过普通节点恢复阈值
- **THEN** 普通过期恢复器不返回或修改该节点，节点的状态、`attempt`、`claim_token` 和 `claimed_by` 保持不变

#### Scenario: 普通算子节点真正失效
- **WHEN** 非视觉、非 PPT 节点超过恢复阈值且原执行器和容量租约均已失效
- **THEN** 普通过期恢复器继续按现有规则安全恢复该节点

### Requirement: 视觉命令必须绑定节点执行代次
视觉命令发布端 MUST 从当前 `NodeRecord` 复制必填的 `dispatch_attempt` 与 `claim_token`，并 MUST 使用 `submission_id`、`node_id`、`dispatch_attempt` 和 `claim_token` 稳定生成 `command_id`。

#### Scenario: 同一执行代次恢复重发
- **WHEN** 视觉协调器为同一 `attempt` 和 `claim_token` 的运行节点重复发布命令
- **THEN** 每次命令具有相同的 `command_id`、`dispatch_attempt` 和 `claim_token`

#### Scenario: 节点重新领取
- **WHEN** 视觉节点合法进入新的领取代次
- **THEN** 新命令的 `dispatch_attempt` 增加、`claim_token` 和 `command_id` 与上一代不同

#### Scenario: 领取身份不完整
- **WHEN** 视觉协调器尝试为缺少 `claim_token` 或有效 `attempt` 的运行节点生成命令
- **THEN** 系统拒绝猜测身份，不发布命令，并将该节点纳入显式恢复报告

### Requirement: Consumer 必须在推理前校验命令身份
Vision Consumer MUST 在下载媒体、抽帧或调用 VBas 前，根据数据库权威状态校验节点、任务、提交和执行代次；只有当前 `RUNNING(50)` 且身份完全匹配的命令 SHALL 获准执行。

#### Scenario: 当前命令获准执行
- **WHEN** 节点为 `RUNNING(50)`，并且 `task_id`、`submission_id`、`dispatch_attempt` 和 `claim_token` 全部匹配
- **THEN** Consumer 执行视觉分析并使用同一执行代次写入后续状态

#### Scenario: 状态已经回到可调度状态
- **WHEN** 旧命令到达时节点状态为 `PENDING(10)`、`WAITING_OPERATOR(30)` 或 `QUEUED(40)`
- **THEN** Consumer 不调用 VBas、不修改节点，将命令记录为陈旧并允许提交 offset

#### Scenario: 执行代次不匹配
- **WHEN** 命令的 `dispatch_attempt` 或 `claim_token` 与数据库当前节点不一致
- **THEN** Consumer 不调用 VBas、不覆盖当前代次状态，并允许提交该命令的 offset

#### Scenario: 终态重复命令
- **WHEN** 命令对应节点已经为 `COMPLETED(60)`、`FAILED(70)` 或 `CANCELLED(80)`
- **THEN** Consumer 保留终态和已有结果，按终态幂等语义结束并允许提交 offset

#### Scenario: 身份无法判定
- **WHEN** 节点不存在、数据库不可用或身份记录损坏
- **THEN** Consumer 报告基础设施错误且不提交该命令的 offset

### Requirement: 视觉状态写入必须使用执行代次 CAS
视觉进度、完成和失败写入 MUST 在单个数据库事务中同时验证节点为 `RUNNING(50)`、`attempt` 等于 `dispatch_attempt` 且 `claim_token` 匹配；完成写入 SHALL 原子保存结果、推进状态、聚合任务类型并释放依赖。

#### Scenario: 当前代次写入进度
- **WHEN** 当前命令报告视觉扫描进度且执行身份仍匹配
- **THEN** Repository 更新进度和原因，并保持节点属于同一执行代次

#### Scenario: 当前代次完成节点
- **WHEN** 当前命令生成结果且完成 CAS 成功
- **THEN** Repository 在同一事务中保存结果、将节点推进到 `COMPLETED(60)`、聚合任务类型并释放后继节点

#### Scenario: 当前代次发生业务失败
- **WHEN** VBas、媒体内容或参数处理产生可终结业务错误且失败 CAS 成功
- **THEN** Repository 将当前节点推进到 `FAILED(70)` 并聚合任务类型，Consumer 可以确认消息

#### Scenario: 分析期间节点被重新领取
- **WHEN** 旧命令写入进度、完成或失败时，数据库的 `attempt` 或 `claim_token` 已变化
- **THEN** CAS 不修改节点或结果，处理器将其归类为陈旧命令并允许提交 offset

#### Scenario: 分析期间节点进入终态
- **WHEN** 当前命令写入时，其他事务已经将节点推进到任一终态
- **THEN** CAS 不覆盖终态或已有结果，处理器按终态幂等语义结束

### Requirement: 严格状态机不得被陈旧命令绕过
系统 MUST 保留既有节点状态转换约束，不得为处理视觉陈旧命令增加 `PENDING(10) -> FAILED(70)`、`WAITING_OPERATOR(30) -> FAILED(70)` 等绕过当前执行代次的转换。

#### Scenario: 旧命令尝试失败落库
- **WHEN** 旧视觉命令执行失败但节点已不属于该执行代次
- **THEN** 系统通过身份 CAS 拒绝写入，而不是放宽状态机或改写当前节点

### Requirement: Consumer 必须隔离可确认消息与基础设施故障
Vision Consumer SHALL 将陈旧命令、终态重复和已成功落库的单任务业务失败视为可确认消息，并 MUST 继续处理同批后续消息；PostgreSQL、Kafka 等基础设施故障 MUST 保持未提交和 fail-closed。

#### Scenario: 陈旧命令后跟随正常命令
- **WHEN** 同一轮 poll 中第一条命令已经陈旧，后一条命令身份有效
- **THEN** Consumer 提交陈旧命令并继续处理后一条命令，必需后台循环保持运行

#### Scenario: 单任务业务失败后仍有正常命令
- **WHEN** 第一条命令已原子写入 `FAILED(70)`，后一条命令可以正常执行
- **THEN** 两条消息按分区连续水位提交，后一条不因前一条业务失败被取消

#### Scenario: 数据库故障发生
- **WHEN** Consumer 无法读取权威身份或提交 CAS 事务
- **THEN** 失败消息及其后不能越过的 offset 保持未提交，Consumer readiness 报告失败原因

### Requirement: 视觉节点必须具备专用恢复语义
Kafka SHALL 负责已发布视觉命令在未提交 offset 时的重投；视觉协调器 SHALL 在启动时为具有完整当前 claim 身份的 `RUNNING(50)` 视觉节点重发同一代命令，不得通过固定处理时长周期性增加 `attempt`。

#### Scenario: Vision Consumer 处理中重启
- **WHEN** Consumer 在提交 offset 前重启
- **THEN** Kafka 重投原命令，Consumer 按相同执行代次继续或幂等结束

#### Scenario: Orchestrator 在发布边界重启
- **WHEN** 视觉节点已经为 `RUNNING(50)`，但 Orchestrator 在命令发布确认前重启
- **THEN** 启动恢复使用现有 `attempt` 和 `claim_token` 重发同一代命令

#### Scenario: 长视频持续处理
- **WHEN** 合法视觉分析运行时间超过普通节点恢复阈值
- **THEN** 节点 `attempt` 不因运行时长周期性增长，Consumer 可继续写入当前代次

### Requirement: 内部命令升级必须安全处理旧消息
新 Vision Consumer MUST 将缺少 `dispatch_attempt` 或 `claim_token` 的旧格式命令识别为不可执行的 `LEGACY_STALE`，不得为其推断身份或调用 VBas；同步部署后的视觉协调器 SHALL 为仍可恢复的运行节点发布新格式命令。

#### Scenario: 部署后消费旧格式命令
- **WHEN** 新 Consumer 收到部署前遗留且没有执行代次字段的视觉命令
- **THEN** Consumer 不执行推理，记录旧格式命令并按分区连续规则提交 offset

#### Scenario: 旧命令对应节点仍在运行
- **WHEN** 数据库中存在具有完整 claim 身份的 `RUNNING(50)` 视觉节点
- **THEN** 新视觉协调器为该节点发布带当前执行代次的新命令，使任务能够继续处理

### Requirement: 远端验收必须按状态机门禁顺序执行
系统 MUST 在 `192.168.29.11` 先完成新镜像部署和原 Vision Consumer 状态机故障定向复现；只有定向复现全部通过，才 SHALL 执行 16 路全量任务与在线人数识别 300 并发、30000 总请求的混合压力验收。

#### Scenario: 定向复现仍出现状态冲突
- **WHEN** 定向验证再次出现 `10 -> 70`、视觉节点周期性增加 `attempt`、Consumer unhealthy 或 Kafka lag 无法收敛
- **THEN** 本变更验收失败，系统停止后续混合压力测试并保留数据库、日志、offset 和 readiness 现场证据

#### Scenario: 定向复现通过后执行混合压力
- **WHEN** 超时运行、陈旧代次、终态迟到和 Consumer 重投场景均未复现原状态机故障
- **THEN** 验收程序同时执行 16 路全量任务和在线人数识别 300 并发、30000 总请求

#### Scenario: 混合压力完成
- **WHEN** 两类负载同时运行直至全部请求与任务得到可判定结果
- **THEN** Vision Consumer 全程保持健康，视觉节点不发生周期性重领或陈旧结果覆盖，Kafka lag 最终收敛，报告包含分泳道耗时、在线吞吐与延迟、GPU/显存、节点代次和 readiness 证据

#### Scenario: 混合压力再次出现状态机问题
- **WHEN** 压测期间出现原状态冲突、Vision Consumer unhealthy、状态冲突导致的容器重启或视觉 lag 不收敛
- **THEN** 整体验收判定不通过，即使在线人数识别请求本身已完成
