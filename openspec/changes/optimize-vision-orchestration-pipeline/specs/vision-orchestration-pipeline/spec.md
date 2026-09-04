## ADDED Requirements

### Requirement: 视觉命令槽位必须持续补位
Vision Orchestrator SHALL 将 `[worker].concurrency` 作为全局课程级视觉命令在途上限，并 MUST 在任一命令完成或失败释放槽位后继续领取下一条可用命令，不得等待同一次 Kafka poll 中其他慢命令完成。

#### Scenario: 错峰到达的命令填满空闲槽位
- **WHEN** 第一条长命令已开始执行且后续视觉命令陆续到达 Kafka，当前在途数小于 `worker.concurrency`
- **THEN** Consumer 持续领取并启动后续命令，直至达到配置上限或 Kafka 暂无消息

#### Scenario: 同批快命令完成后立即补位
- **WHEN** 同一 poll 领取的多条命令中一条先完成而另一条仍在运行
- **THEN** 空出的槽位立即用于后续命令，不等待该 poll 的全部命令结束

#### Scenario: 在途和预取均有界
- **WHEN** Kafka 积压量远大于 `worker.concurrency` 和 `kafka.max_poll_records`
- **THEN** 执行中的命令不超过 `worker.concurrency`，内存 pending 数不超过单次预取上限，其余消息保留在 Kafka

### Requirement: Kafka offset 必须按 partition 连续提交
Vision Orchestrator MUST 跨多次 poll 维护每个 partition 的完成水位，较高 offset 提前完成时 MUST NOT 越过较低未完成 offset 提交；停止、崩溃或 rebalance 时未完成命令 MUST 保持可重放。

#### Scenario: 较高 offset 先完成
- **WHEN** 同一 partition 的 offset 101 已完成而 offset 100 仍在运行
- **THEN** Consumer 不提交越过 offset 100 的水位，并在 offset 100 完成后连续提交已完成区间

#### Scenario: 带在途命令停止服务
- **WHEN** Vision Orchestrator 在存在执行中或 pending 命令时收到停止信号
- **THEN** 服务停止领取新命令、在优雅停止预算内收敛任务，且不提交任何未完成命令的 offset

### Requirement: 多课程媒体处理必须公平且有界
Vision Orchestrator MUST 限制每条视觉命令预取的媒体 batch 数，并 SHALL 在活动命令之间公平分配 ffmpeg/ffprobe 执行机会；单个长视频不得把整节课的全部时间点预先排在其他命令的探测和首批抽帧之前。

#### Scenario: 长视频不能饿死另一课程首批抽帧
- **WHEN** 课程 A 有数百个采样点且课程 B 在 A 抽帧期间进入媒体调度
- **THEN** B 的视频探测和首批抽帧在有界轮转次数内开始，不等待 A 的全部采样点完成

#### Scenario: 媒体等待作业数量有上限
- **WHEN** `worker.concurrency` 个长视频同时开始视觉分析
- **THEN** 待执行媒体作业总数受活动命令数、每命令预取 batch 数和 batch 大小共同限制，不为全部时间点一次性创建无界协程

### Requirement: 抽帧与 VBas 推理必须形成有界流水线
Vision Orchestrator SHALL 按排序时间点生成确定性 batch 计划；一个 batch 的图片全部准备后 MUST 立即进入离线容量申请和 VBas 推理，不得等待同轮全部时间点抽取完成。媒体生产者与推理消费者之间 MUST 使用有界队列。

#### Scenario: 首批完成即请求 VBas
- **WHEN** 一轮扫描包含多个 batch 且第一个 batch 的图片已经全部抽取
- **THEN** Vision 开始为首批申请租约和调用 VBas，同时允许媒体层准备后续 batch

#### Scenario: 最后一批不足批次大小
- **WHEN** 剩余采样帧少于 `scan.batch_size`
- **THEN** 剩余帧仍作为最后一个确定性 batch 调用 VBas，且不会丢失对应时间点

#### Scenario: 异步完成不改变批次身份
- **WHEN** 相同命令因重启或重放再次执行且各帧实际完成顺序不同
- **THEN** batch 分组、帧顺序、`image_id` 和 `batch_id` 与首次逻辑执行一致

### Requirement: 学生区域分析必须复用帧资产
学生行为分析 MUST 对每个采样时间点只解码并保存一张帧图片；全画面、前排和后排推理 SHALL 引用该图片并使用各自稳定的区域身份，不得为不同区域重复执行视频解码。

#### Scenario: 同时提供前后排区域
- **WHEN** 学生任务同时包含 `front_points` 和 `back_point`
- **THEN** 每个时间点只发生一次有效抽帧，并产生相互可区分的全画面、前排和后排 VBas 逻辑批次

#### Scenario: 未提供区域
- **WHEN** 学生任务未提供前排或后排区域
- **THEN** Vision 不创建缺失区域的推理工作，并继续使用既有兜底聚合规则

### Requirement: 视觉节点原因必须反映真实执行阶段
系统 SHALL 保持现有整数节点状态码，并 MUST 使用中文 `reason` 区分视觉命令等待消费、视频校验、时长探测、抽帧、等待 VBas 容量、VBas 推理、聚合和结果持久化阶段。命令发布但尚未被 Vision 消费时 MUST NOT 表述为“已领取”或正在推理。

#### Scenario: 已发布但尚未消费
- **WHEN** Orchestrator 已发布视觉命令而 Vision 尚未处理该消息
- **THEN** 节点原因显示“视觉命令已发布，等待 Vision 消费”或等价中文，不误报为已领取

#### Scenario: 等待离线容量
- **WHEN** 帧 batch 已准备但 Control 暂无 VBas 离线租约
- **THEN** 节点保持运行状态并显示“正在等待 VBas 离线容量”，容量恢复后继续推理

#### Scenario: 批次进度可查询
- **WHEN** 视觉任务正在抽帧或推理多个 batch
- **THEN** 查询结果能看到当前阶段和有界更新的已完成 batch/总 batch 进度，不要求读取容器日志判断

### Requirement: 流水线失败必须隔离并完整清理
任一视觉命令的媒体、租约、VBas 或聚合步骤失败时，Vision Orchestrator MUST 取消该命令尚未开始的工作、终止其媒体子进程、释放租约并发布既有失败终态事件；该失败 MUST NOT 取消其他课程命令。

#### Scenario: 推理期间发生不可恢复错误
- **WHEN** 一个命令的某个 VBas batch 返回不可恢复错误
- **THEN** 该命令停止继续生产和推理 batch、已取得租约全部释放、节点进入失败终态，其他命令继续执行

#### Scenario: 服务重启后重放未提交命令
- **WHEN** 服务在命令尚未完成时重启并重新收到该命令
- **THEN** 系统使用稳定命令代次和批次身份恢复或幂等跳过已有事实，不产生重复节点结果或状态冲突

### Requirement: 发布必须可回滚且不代替用户压力验收
实现完成后 SHALL 在 `192.168.29.11` 使用现有 BuildKit 缓存构建实际受影响的服务镜像，完成版本、架构、health 和 readiness 门禁后替换容器；新容器正常后 MUST 精确删除被替换的旧容器和旧镜像。本变更 MUST NOT 把未执行的用户业务压力测试记录为通过。

#### Scenario: 新容器通过部署门禁
- **WHEN** 新镜像 revision 与目标提交一致且新容器通过 health、readiness 和 Kafka Consumer 检查
- **THEN** 部署保留新容器并精确删除对应旧容器和旧镜像，不执行全局镜像清理

#### Scenario: 新容器门禁失败
- **WHEN** 新容器未通过任一部署门禁
- **THEN** 部署恢复发布前记录的镜像和配置，保留失败证据且不删除可回滚旧资产
