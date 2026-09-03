## ADDED Requirements

### Requirement: 远端发布应使用缓存并精确替换资产
系统 SHALL 在 `192.168.29.11` 使用既有构建缓存构建受影响镜像，并 SHALL 在替换前记录 Git SHA、
配置摘要、旧容器完整 ID 和旧镜像完整 ID。旧资产只能在新容器完成健康、readiness 和真实请求门禁
后精确删除。

#### Scenario: 缓存构建受影响镜像
- **WHEN** 本地测试通过并开始服务器发布
- **THEN** 构建 SHALL 不使用 `--no-cache`，不得执行宽泛镜像或 BuildKit 缓存清理，并 SHALL 校验新镜像架构和 revision

#### Scenario: 新容器失败时保留回滚资产
- **WHEN** 新容器未通过健康、readiness、注册或最小真实请求门禁
- **THEN** 发布 SHALL 停止并使用记录的旧镜像和配置回滚，旧镜像不得提前删除

#### Scenario: 新版本通过后精确清理
- **WHEN** 所有受影响新容器均通过发布门禁
- **THEN** 系统 SHALL 只删除被替换且无容器引用的旧容器和旧镜像，并 SHALL 保留构建缓存和无关资产

### Requirement: 正式压测应锁定运行版本和配置
每个正式测试 attempt SHALL 在开始时冻结全部目标容器 ID、镜像 revision 和配置摘要，并 SHALL 在
执行期间持续检查。运行事实变化的 attempt MUST 标记为环境失效。

#### Scenario: 压测期间服务被替换
- **WHEN** 任一平台服务或算子容器 ID、revision 或关键配置在测试期间变化
- **THEN** 当前 attempt MUST 停止或继续收集诊断但标记为环境失效，不得用于通过结论

#### Scenario: 稳定版本完成测试
- **WHEN** 从预检到终态所有运行事实保持不变
- **THEN** 报告 SHALL 保存开始与结束快照，并允许进入业务验收判定

### Requirement: 容量恢复应接受 Control 故障注入验证
远端测试 SHALL 在视觉任务已产生真实 VBas 批次后分别使 Control 暂时不可用 5、15、30 秒，并
验证租约恢复、节点幂等和最终终态。

#### Scenario: 三档 Control 短暂不可用
- **WHEN** 每档故障注入后 Control 在对应时间内恢复
- **THEN** 视觉节点 SHALL 全部成功、节点 attempt 保持 1、逻辑 batch 不重复，最终租约和 Kafka lag SHALL 归零

### Requirement: 在线压力应分档并重复验证
远端 Campaign SHALL 至少执行 72 并发 5000 次、144 并发 10000 次、200 并发 10000 次在线人数
识别；200 并发 SHALL 连续完成至少三轮。

#### Scenario: 在线分档全部通过
- **WHEN** 每档正式请求全部完成且系统达到终态
- **THEN** 每轮 HTTP 和业务成功率 SHALL 均为 100%，任何 `50301`、`50302`、`50201`、`50401`、`50000` 或连接异常计数 SHALL 为 0

#### Scenario: 压测失败保留全量分类
- **WHEN** 任一在线请求失败
- **THEN** 报告 SHALL 保存全部失败的分类计数和脱敏关联字段，不得只凭有限样本推断总体分布

### Requirement: 离线和最终混合压力应完整验证
远端 Campaign SHALL 独立执行一轮 20 路全量课程基线，并 SHALL 连续执行至少两轮 20 路全量课程
加在线人数识别 200 并发、10000 次的混合压力。

#### Scenario: 20 路离线基线完成
- **WHEN** 20 个课程同时请求 `PPT`、`ASR`、`TEACHER_BEHAVIOR`、`STUDENT_BEHAVIOR`
- **THEN** 全部任务类型和节点 SHALL 成功终态，且不得出现重复领取、状态回退、批次身份冲突或空失败原因

#### Scenario: 两轮最终混合压力完成
- **WHEN** 每轮 20 路全量课程和 200 并发、10000 次在线请求同时开始
- **THEN** 两轮 SHALL 连续达到在线 100% 业务成功、80 个任务类型成功终态、节点 attempt 为 1、三台 VBas 均有在线和离线真实工作

### Requirement: 每轮只能在全部运行状态收敛后结束
测试驱动 SHALL 等待全部在线响应、课程任务、节点、租约、算子在途与队列、Kafka、Outbox 和文件
生命周期达到终态，才可生成最终结论。

#### Scenario: 仍有任务或队列未完成
- **WHEN** 超时时仍有非终态课程、活动租约、VBas running/queued、Kafka lag、未发布 Outbox 或本轮课程缓存目录
- **THEN** attempt MUST 标记为失败或未完成，并列出剩余项，不得输出通过

#### Scenario: 驱动中断或证据缺失
- **WHEN** 负载驱动异常退出、监控中断或必需原始报告缺失
- **THEN** attempt MUST 标记为中断并保留现有证据，不得根据部分请求结果形成总体结论

### Requirement: GPU 显存应按基线峰值和恢复值持续监控
每轮 VBas 压测前 SHALL 从新创建的三个 VBas 容器取得冷启动稳定基线，负载期间 SHALL 每 2 秒采集
三卡显存、利用率、功耗和 PID 到容器映射，终态后 SHALL 继续观察至少 5 分钟并计算恢复值。

#### Scenario: 显存负载后恢复
- **WHEN** 一轮负载全部收敛并完成 5 分钟恢复观察
- **THEN** 每卡最后 60 秒显存中位数与本轮基线差值 SHALL 不超过 512 MiB，且连续轮次不得单调累积

#### Scenario: 显存持续膨胀
- **WHEN** 恢复值超出基线 512 MiB、连续轮次恢复值上升、发生 OOM/GPU Xid/容器重启或剩余显存低于 2 GiB
- **THEN** Campaign MUST 暂停扩大负载并标记失败，完成接口、容量池、请求阶段、实例、队列和模型内存生命周期归因后才能重新测试

### Requirement: 文件生命周期应在混合压力下保持正确
测试 SHALL 验证 PPT、ASR、教师行为和学生行为共享媒体的引用终态清理，并 SHALL 始终保留
`/data/result/{task_id}` 持久结果。

#### Scenario: 成功课程按消费者终态清理
- **WHEN** PPT Slice、ASR、教师行为和学生行为分别达到终态
- **THEN** `slides.mp4`、`teacher.wav`、`teacher.mp4` 和 `student.mp4` SHALL 按实际消费者规则删除，最终课程缓存目录 SHALL 消失

#### Scenario: 失败课程仍清理临时媒体
- **WHEN** 任一请求任务类型在所有允许重试后进入失败终态
- **THEN** 不再有消费者的媒体和视觉临时目录 SHALL 删除，结果目录和失败事实 SHALL 保留

### Requirement: 压测监控不得显著干扰业务负载
存储监控 SHALL 优先采集 `df` 和本轮目标目录增量，不得高频递归扫描整个持久结果树；资源采集失败
SHALL 单独记录并不得伪装成业务失败。

#### Scenario: 持久结果目录规模较大
- **WHEN** `/data/result` 已包含大量历史文件
- **THEN** 监控 SHALL 避免每 10 秒执行全树 `du`，并 SHALL 继续准确跟踪本轮课程目录和磁盘剩余空间

### Requirement: 验收证据应不可覆盖且使用中文记录
每个 attempt SHALL 使用唯一 Run ID 保存 write-once 原始报告，并 SHALL 将环境、请求规模、终态、
错误分类、实例分布、GPU、存储和最终判定写入中文 Harness。

#### Scenario: 完整通过报告
- **WHEN** 所有必需轮次和终态门禁均通过
- **THEN** Harness SHALL 引用每轮原始证据位置和固定运行事实，并明确可以作为本变更验收证据

#### Scenario: 失败或中断报告
- **WHEN** 任一轮失败、中断或环境失效
- **THEN** Harness SHALL 保留失败事实、未完成项和后续动作，不得覆盖旧证据或宣称整个变更通过
