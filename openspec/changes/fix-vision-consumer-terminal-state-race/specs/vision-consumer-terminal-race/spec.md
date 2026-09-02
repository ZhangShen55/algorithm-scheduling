## ADDED Requirements

### Requirement: 视觉 Consumer 必须幂等处理终态迟到进度

视觉命令处理器 SHALL 在进度写入因 `RepositoryStateConflictError` 失败后重新读取节点；当节点状态为 `COMPLETED`、`FAILED` 或 `CANCELLED` 时，必须结束当前命令而不覆盖节点终态，并允许 Consumer 提交 Kafka offset。

#### Scenario: 进度更新与完成事务发生竞态

- **WHEN** 处理器初次读取到 `RUNNING`，随后另一事务将节点改为 `COMPLETED`，而当前处理器提交进度
- **THEN** 处理器不抛出致命错误、不更新终态、不发布覆盖性进度事件，Consumer 提交该消息 offset

#### Scenario: 失败或取消后的迟到进度

- **WHEN** 节点已经是 `FAILED` 或 `CANCELLED`，仍收到属于该节点的进度
- **THEN** 处理器幂等结束当前命令，保留原状态和原因，Consumer 可以继续处理后续消息

### Requirement: 非终态状态冲突必须保持可见

视觉命令处理器 SHALL 仅对二次读取明确确认的终态冲突做幂等处理；节点仍为 `QUEUED`、`RUNNING` 或其他非终态时，必须继续报告状态冲突，不得提交 Kafka offset。

#### Scenario: 运行中节点的意外状态冲突

- **WHEN** Repository 报告状态冲突且二次读取节点仍为 `RUNNING`
- **THEN** 处理器返回基础设施错误，Consumer 不提交该消息 offset

#### Scenario: 节点不存在或数据库读取失败

- **WHEN** 进度冲突后的二次读取找不到节点或数据库不可用
- **THEN** 处理器保留原错误语义，Consumer 不提交该消息 offset

### Requirement: 完成阶段的终态竞态必须幂等且不得覆盖结果

视觉命令处理器 SHALL 在 `complete_node()` 发生状态冲突时重新读取节点；若节点已进入任一终态，必须保留已存在的终态和结果并安全结束，非终态冲突仍必须失败。

#### Scenario: 其他事务已经完成节点

- **WHEN** 当前分析得到结果后，另一事务先完成同一节点，当前处理器写完成结果发生冲突
- **THEN** 当前处理器不覆盖已有结果、不抛出致命 Consumer 错误，消息可提交

#### Scenario: 完成写入遇到非终态冲突

- **WHEN** `complete_node()` 冲突后的节点仍为可执行非终态
- **THEN** 处理器继续抛出状态错误，消息不提交

### Requirement: Consumer 在幂等冲突后必须继续运行

视觉命令 Consumer SHALL 在终态竞态消息成功提交后继续消费同一批次中的后续消息，不能因为迟到进度或重复完成而退出后台循环。

#### Scenario: 竞态消息后跟随正常完成消息

- **WHEN** 同一轮 Kafka poll 中第一条消息发生终态竞态，第二条消息可正常完成
- **THEN** 两条消息均按分区连续提交，Consumer 仍保持运行
