## ADDED Requirements

### Requirement: 算子租约必须选择实时负载最低的可用实例
Control Service SHALL 对 capability 匹配、心跳有效、生命周期为 `ONLINE`、`model_ready=true` 且未达到声明容量的实例计算实时负载，并 MUST 选择归一化负载最低的实例，而不是选择排序第一的未满实例。实例有效负载 MUST 为 Redis 活跃租约数与最近上报 `reported_inflight` 的较大值，二者不得相加。

#### Scenario: 空闲实例优先于已有任务实例
- **WHEN** 三个等容量 VBas 实例的有效负载分别为 `1/1024`、`0/1024`、`0/1024`
- **THEN** 下一租约 MUST 选择后两个空闲实例之一，不得继续选择已有任务的第一个实例

#### Scenario: 不同容量按负载率比较
- **WHEN** 两个健康实例的有效负载分别为 `2/4` 和 `10/100`
- **THEN** 下一租约 MUST 选择负载率为 `10/100` 的实例，而不是只比较绝对任务数或实例 ID

#### Scenario: 上报负载防止租约低估
- **WHEN** 某实例活跃租约数为 `0` 但最近 `reported_inflight` 为 `3`，另一等容量实例两者均为 `0`
- **THEN** 下一租约 MUST 选择真正空闲的实例，并将差异保留为运维可观测事实

### Requirement: 选择与租约创建必须原子且同负载实例必须轮询
Control Service MUST 在同一个 Redis 原子操作中完成过期租约清理、候选评分、同负载选择和新租约创建。同一最低负载集合 SHALL 使用持久轮询游标，禁止并发调用同时读取旧负载后全部选择同一实例。

#### Scenario: 首批三个并发租约分散到三实例
- **WHEN** 三个 VBas 实例均为 `0/1024` 且三个租约请求并发到达
- **THEN** 三个原子租约 MUST 分别归属三个不同实例，创建完成后的活跃租约数均为 `1`

#### Scenario: 相同负载持续轮询
- **WHEN** 三个等容量实例在每次选择前具有相同有效负载且持续存在工作积压
- **THEN** 轮询游标 MUST 使三个实例依次获得租约，不得由固定实例 ID 长期独占

#### Scenario: 高并发不超卖容量
- **WHEN** 多个调用方并发申请的租约总数超过所有候选实例的剩余声明容量
- **THEN** 成功租约数 MUST 不超过剩余总容量，其余请求 SHALL 获得明确的容量不足结果

### Requirement: 实例全部能力和调用方必须共享容量
同一算子实例的全部 capability、在线调用和离线调用 SHALL 共享该实例的活跃租约集合、有效负载和 `declared_capacity`。平台 MUST 不为 `student_behavior`、`teacher_behavior`、Online Gateway 和 Vision Orchestrator 重复计算同一 VBas 实例容量。

#### Scenario: 在线和离线 VBas 共同参与评分
- **WHEN** `vbas-gpu0` 已持有 Online Gateway 的在线租约而 `vbas-gpu1`、`vbas-gpu2` 空闲，Vision Orchestrator 申请离线 `student_behavior` 租约
- **THEN** 离线租约 MUST 优先选择空闲实例，且在线租约继续占用 `vbas-gpu0` 的共享容量

#### Scenario: 教师和学生能力共享实例上限
- **WHEN** 同一 VBas 实例已有教师和学生能力租约且有效负载达到 `declared_capacity`
- **THEN** 该实例 MUST 同时停止接收两类新租约，直到租约释放或过期

### Requirement: 生命周期和释放行为必须保持安全
新选择策略 MUST 保留现有生命周期、TTL、续租、释放和工作归属合同。`OFFLINE`、`DRAINING`、心跳过期或模型未就绪实例 MUST 不参与评分；响应、异常和取消后租约 MUST 精确释放，过期租约 MUST 不再占用负载。

#### Scenario: 排空实例不参与最低负载选择
- **WHEN** 当前负载最低的实例处于 `DRAINING`
- **THEN** Control Service MUST 跳过该实例并从其余符合条件实例中选择

#### Scenario: 过期租约恢复容量
- **WHEN** 实例租约超过 TTL 且调用方没有续租
- **THEN** 下一次原子选择 MUST 清理该租约并按恢复后的负载重新评分
