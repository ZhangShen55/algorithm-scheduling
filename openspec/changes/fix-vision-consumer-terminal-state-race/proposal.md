## Why

视觉命令 Consumer 在分析过程中可能遇到并发事务先将节点推进到终态的情况。当前迟到的进度更新会被包装成致命基础设施错误，Kafka offset 无法提交并导致 Consumer 退出，进而造成视觉命令积压和服务就绪状态下降。

## What Changes

- 将视觉进度更新与节点完成阶段的 `RepositoryStateConflictError` 单独处理。
- 重新读取节点状态；当节点已进入 `COMPLETED`、`FAILED` 或 `CANCELLED` 时，将迟到写入视为幂等结束，不覆盖终态且允许 Consumer 提交 offset。
- 当节点仍处于可执行的非终态时，继续抛出状态冲突，避免吞掉真正的并发错误。
- 为视觉命令 Consumer 增加终态竞态、非终态冲突、完成阶段竞态和后续消息继续处理的回归测试。
- 在 Harness 中记录复现步骤、修复边界和验证命令。

## Capabilities

### New Capabilities

- `vision-consumer-terminal-race`: 视觉命令 Consumer 对终态迟到进度和重复完成的幂等处理。

### Modified Capabilities

- `root-level-platform-services`: 明确视觉 Consumer 的终态状态冲突处理与 offset 提交语义。

## Impact

- 影响 `vision_orchestrator_service/app/application/events.py` 及其运行时回归测试。
- 不改变对外 HTTP/WebSocket 接口、节点状态值、Kafka topic 或算子调用协议。
- 不改变数据库 Repository 的状态保护；只在上层区分已确认终态与真正的状态冲突。
