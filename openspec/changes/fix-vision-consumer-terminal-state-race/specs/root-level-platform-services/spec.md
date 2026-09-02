## MODIFIED Requirements

### Requirement: 服务部署组合保持可选边界

部署编排 SHALL 要求 `control_service` 与 `orchestrator_service` 支撑离线处理，并 SHALL 允许在不需要视觉离线分析或在线能力时分别省略 `vision_orchestrator_service` 和 `online_gateway_service`。当部署 `vision_orchestrator_service` 时，其视觉命令 Consumer 必须在终态状态冲突后保持运行，并遵守视觉节点状态机的幂等语义。

#### Scenario: 只部署核心离线能力

- **WHEN** 交付环境只启用不含教师和学生行为的离线任务
- **THEN** `control_service` 与 `orchestrator_service` 可在不启动两个可选服务的情况下部署

#### Scenario: 视觉服务遇到终态迟到消息

- **WHEN** `vision_orchestrator_service` 收到进度或完成命令，而其他事务已经将对应节点推进到 `COMPLETED`、`FAILED` 或 `CANCELLED`
- **THEN** 视觉 Consumer 将消息按幂等成功处理并提交 offset，不覆盖终态，也不因该冲突退出
