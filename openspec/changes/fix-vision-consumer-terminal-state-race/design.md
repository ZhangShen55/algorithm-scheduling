## Context

`vision_orchestrator_service` 的 `VisualCommandProcessor` 会在分析期间多次更新节点进度，并在分析结束时写入完成结果。另一个事务可能在这两个操作之间先将节点推进到 `COMPLETED`、`FAILED` 或 `CANCELLED`。Repository 正确拒绝对终态写入，但当前处理器把所有异常都包装成基础设施故障，Consumer 因未提交 offset 而退出。

## Goals / Non-Goals

**Goals:**

- 只把已确认终态造成的迟到进度或重复完成视为幂等成功。
- 不覆盖终态结果、不发布误导性的覆盖事件，并让 Kafka Consumer 提交当前消息 offset。
- 保留节点不存在、数据库故障、Kafka 发布失败、身份错误和非终态冲突的失败语义。
- 通过内存竞态测试证明 Consumer 处理完冲突消息后仍能处理下一条消息。

**Non-Goals:**

- 不修改 `platform_common.repository` 的状态机约束。
- 不改变 Kafka topic、消息格式、HTTP 接口或 VBas 调用协议。
- 不把所有 Repository 异常转换为成功，也不新增自动重试策略。

## Decisions

### 1. 在应用层识别终态竞态

引入对 `RepositoryStateConflictError` 的专门分支。进度更新发生冲突后重新读取节点；只有状态属于 `COMPLETED`、`FAILED` 或 `CANCELLED` 才返回幂等成功。其他状态继续包装为 `_ProgressDeliveryError`。这样保持 Repository 的并发保护，同时避免迟到进度拖垮 Consumer。

### 2. 完成阶段采用同样的终态幂等规则

`complete_node()` 发生状态冲突时重新读取节点。若节点已经是 `COMPLETED`，视为其他事务已完成；若是 `FAILED` 或 `CANCELLED`，视为终态已由其他流程决定，结束当前命令且不重复写结果。非终态冲突继续抛出，避免掩盖真正的状态机错误。

### 3. 不为迟到进度补发覆盖性事件

迟到进度不会发布新的 `PROGRESS` 事件，因为终态事件或终态查询已经是权威事实。对于原本已经完成的重复命令，沿用现有的终态事件重发布逻辑；对竞态中由其他事务完成的命令不重复发送结果事件。

### 4. 测试在 Consumer 边界验证 offset

测试使用可控 Repository 在更新/完成调用时切换节点状态，验证：终态消息被消费并提交 offset、终态不被覆盖、后续消息继续处理；同时验证运行中冲突、数据库错误和 Producer 错误仍不提交 offset。

## Risks / Trade-offs

- [终态事件尚未发布时发生竞态] -> 当前命令不补发覆盖性事件；由完成事务的调用方负责发布终态事件，重复命令再次投递时使用现有幂等终态重发布路径。
- [状态在二次读取后再次变化] -> 只依据二次读取到的明确终态决定幂等；读取失败或仍为非终态继续失败，不吞掉异常。
- [多个 Consumer 同时处理同一节点] -> 数据库状态机仍是最终仲裁，应用层只处理已经落到终态的迟到写入。

## Migration Plan

1. 部署包含修复的 `vision_orchestrator_service` 代码并重启 Consumer。
2. 观察 `/ready`、Kafka 视觉命令积压和 Consumer 活动成员，确认迟到进度不再使循环退出。
3. 若发现异常行为，回滚到上一镜像；数据库状态机和消息格式无需迁移。

## Open Questions

无。终态集合与 Repository 状态枚举保持一致。
