## 为什么

视觉节点正在由 `vision_orchestrator_service` 处理时，会被通用过期节点恢复器错误回收到可调度状态；已经发布的旧 Kafka 命令又缺少 `attempt` 与 `claim_token` 执行代次，因而可能继续推理并尝试将已回退到 `PENDING(10)` 的节点写成 `FAILED(70)`，最终触发状态冲突、阻止 offset 提交并使 Vision Consumer unhealthy。现场还发现视觉镜像声明的 Git revision 与容器内实际源码不一致，导致已修复代码无法被可信地部署和验证，因此必须同时收敛视觉恢复所有权、命令代次和发布来源一致性。

## 变更内容

- 将教师与学生视觉分析节点排除在普通算子节点的超时恢复范围之外，由视觉协调器实施独立、可判定所有权的恢复策略。
- **BREAKING（内部）**：在视觉 Kafka 命令中增加必填的 `dispatch_attempt` 和 `claim_token`，使每条命令能够绑定到一次确定的节点领取；同一代重复发布保持同一命令身份，新一代领取产生新身份，发布端与消费端必须同步部署。
- Vision Consumer 在调用 VBas 前原子校验节点状态、任务身份和执行代次；陈旧命令不得执行推理或改写节点，但应作为可确认消息提交 offset。
- 将视觉进度、完成和失败写入改为带预期执行代次的比较并交换（CAS）操作，区分当前业务结果、终态幂等、陈旧命令和基础设施故障。
- 隔离单条陈旧命令和单任务业务失败，确保它们不会取消同批后续消息或使必需 Consumer loop unhealthy；数据库、Kafka 等基础设施故障继续保持 fail-closed。
- 保留现有严格状态机，不增加 `PENDING(10) -> FAILED(70)` 等掩盖错误恢复的转换。
- 强化视觉服务镜像的构建来源证明：镜像 revision、源码 manifest/hash 与明确 Git checkout 必须一致，部署验证不得只依赖 OCI revision 标签。
- 增加单元、Repository、Kafka/PostgreSQL 集成竞态测试以及 `192.168.29.11` 的混合负载回归，验证视觉节点不再周期性增加 `attempt`，Consumer readiness 持续健康且 Kafka lag 收敛。
- 将远端验收设为顺序门禁：先同步替换 Orchestrator 与 Vision 镜像并清理旧版本，再定向复现原 `Vision Consumer` 状态机故障；只有故障不再出现，才执行 16 路全量任务与在线人数识别 300 并发、30000 总请求的混合压力验证并形成结果报告。

## 能力

### 新增能力

- `vision-command-generation-and-recovery`: 规定视觉节点恢复所有权、视觉命令执行代次、Consumer 准入校验、CAS 结果写入、陈旧命令确认和批次故障隔离语义。

### 修改能力

- `root-level-platform-services`: 强化 `vision_orchestrator_service` 的 Consumer 健康边界，并要求平台服务镜像声明的 revision 与实际构建源码可验证地一致。

## 影响

- 影响 `orchestrator_service` 的视觉节点领取、恢复、命令生成和 Kafka 发布逻辑。
- 影响 `vision_orchestrator_service` 的命令解析、执行准入、进度/结果写入和 Consumer 批次处理逻辑。
- 影响 `platform_contracts` 的内部视觉命令结构，以及 `platform_common.repository` 的视觉专用查询和条件写入能力。
- 影响 PostgreSQL Repository 测试、Kafka Consumer 测试、部署构建脚本、镜像元数据校验、Harness 和里程碑 2B 远端回归证据。
- 不改变 A 服务 HTTP 接口、VBas HTTP 请求/响应、外部任务状态值、Kafka topic 名称或已有历史任务数据；内部 Kafka 命令格式会增加必填执行代次字段，发布端与消费端必须同步部署。
