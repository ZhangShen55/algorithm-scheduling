## 1. 固化故障复现与测试基线

- [x] 1.1 增加普通过期恢复器错误返回视觉节点的 Repository 失败测试，并断言现有视觉节点会被错误增加 `attempt`。
- [x] 1.2 增加节点已回到 `PENDING(10)` 后旧视觉命令尝试写入 `FAILED(70)` 的处理器失败测试，证明原始状态冲突链路。
- [x] 1.3 增加同一轮 poll 中一条状态冲突导致后续消息取消、offset 未提交和必需 Consumer loop 退出的失败测试。
- [x] 1.4 在 Harness 记录本次远端现场、节点 `attempt` 异常增长、Consumer readiness 错误和上一终态竞态修复未覆盖的边界。

## 2. 隔离视觉节点恢复所有权

- [x] 2.1 修改 `list_stale_claimed_nodes()`，在 SQL 查询层排除教师与学生视觉分析节点，并保留普通算子恢复行为。
- [x] 2.2 修改 `recover_stale_claimed_node()`，在条件更新层再次排除教师与学生视觉分析节点，防止其他调用方绕过查询保护。
- [x] 2.3 补充 Repository 与 `StaleNodeRecovery` 测试，覆盖超时视觉节点不变、普通失效节点仍恢复、并发更新不会误回收。
- [x] 2.4 收敛 `VisualNodeCoordinator.recover()`：仅重发 `RUNNING(50)` 且 claim 身份完整的视觉节点，缺失身份时输出结构化恢复报告且不得伪造 token。

## 3. 升级视觉命令执行代次

- [x] 3.1 为 `VisualAnalysisCommand` 增加必填 `dispatch_attempt` 与 `claim_token` 的序列化、反序列化和字段校验。
- [x] 3.2 修改视觉命令生成逻辑，从 `NodeRecord` 复制 claim 身份，并使用提交、节点、代次和 token 稳定派生 `command_id`。
- [x] 3.3 增加同代恢复重发 ID 稳定、新代领取 ID 变化以及 claim 身份缺失拒绝发布的契约和协调器测试。
- [x] 3.4 为旧格式视觉命令增加明确的 `LEGACY_STALE` 解析/分类路径，保证不执行 VBas 且可以按迁移规则确认消息。

## 4. 实现视觉命令准入和 CAS Repository

- [x] 4.1 定义结构化的视觉命令准入结果和 CAS 结果，区分 `CURRENT`、`STALE`、`TERMINAL`、`APPLIED` 与不可判定基础设施错误。
- [x] 4.2 实现执行前 Repository 身份校验，核对节点状态、任务、提交、`attempt` 和 `claim_token`。
- [x] 4.3 实现 `update_visual_progress_if_current()`，仅允许当前 `RUNNING(50)` 代次更新进度和原因。
- [x] 4.4 实现 `complete_visual_node_if_current()`，在同一事务中条件写入结果、完成节点、聚合任务类型并释放依赖。
- [x] 4.5 实现 `fail_visual_node_if_current()`，仅允许当前代次写入 `FAILED(70)` 并聚合任务类型。
- [x] 4.6 增加真实 PostgreSQL Repository 测试，覆盖成功写入、代次变化、claim 变化、并发终态、节点不存在和事务回滚。

## 5. 修复 Vision Consumer 处理语义

- [x] 5.1 在任何媒体处理或 VBas 调用前执行命令准入，陈旧、旧格式和终态命令不进入模型路径。
- [x] 5.2 将进度、完成和业务失败分支切换到视觉专用 CAS，并按结构化结果决定继续、幂等结束或提交消息。
- [x] 5.3 保留 PostgreSQL、Kafka、身份不可判定和结果事务失败的 fail-closed 语义，不得将基础设施故障伪装成业务失败。
- [x] 5.4 增加 Consumer 批次测试，验证陈旧命令、终态重复和已落库业务失败不会取消后续消息且 offset 按分区连续提交。
- [x] 5.5 增加基础设施故障测试，验证失败 offset 不提交、不可越过的后续 offset 不提交且 `/ready` 报告脱敏原因。
- [x] 5.6 回归上一变更覆盖的 `COMPLETED(60)`、`FAILED(70)`、`CANCELLED(80)` 迟到进度与重复完成语义。

## 6. 建立镜像源码一致性门禁

- [x] 6.1 增加从完整 Git SHA detached checkout 生成确定性源码 manifest 的脚本，覆盖两个服务 `app/`、视觉契约和相关公共 Repository 文件。
- [x] 6.2 修改 Orchestrator 与 Vision Docker 构建，使镜像保存 revision、源码 manifest、manifest 哈希和实际受管源码。
- [x] 6.3 扩展部署 preflight，联合校验不可变 image ID、目标 Git SHA、镜像 manifest 与容器内实际文件哈希。
- [x] 6.4 增加部署脚本测试，覆盖正确镜像通过、旧源码伪装新 revision、manifest 缺失、文件篡改和构建缓存命中场景。

## 7. 本地集成和迁移验证

- [x] 7.1 运行 `platform_contracts`、Repository、Orchestrator 视觉协调器、Vision Consumer 和四服务相关测试。
- [x] 7.2 使用真实 PostgreSQL 与 Kafka 验证当前命令、陈旧命令、重新领取竞态、Consumer 重启重投和 Orchestrator 启动重发。
- [x] 7.3 验证新 Consumer 安全确认旧格式命令，且新协调器能为仍在运行并具有完整 claim 的节点发布新格式命令。
- [x] 7.4 分别执行 `orchestrator_service` 与 `vision_orchestrator_service` 的 `compileall`、`app.main:app` 导入、启动、health 和 readiness 检查。

## 8. 远端同步部署与回归

- [x] 8.1 在 `192.168.29.11` 暂停视觉任务领取，保存数据库运行节点、Kafka lag、旧容器、旧镜像 ID/revision 和 readiness 基线。
- [x] 8.2 从同一目标 Git SHA 的干净 release checkout 构建 Orchestrator 与 Vision 新镜像并保留构建缓存，校验两镜像的公共契约版本、revision、源码 manifest 和实际文件哈希。
- [x] 8.3 同步替换 Orchestrator 与 Vision 容器；新容器通过 health、readiness 和源码一致性门禁后，删除被替换的旧容器和旧镜像并记录删除清单。
- [x] 8.4 使用受控视觉任务定向复现原故障，覆盖超过普通恢复阈值、旧 `attempt`/`claim_token` 命令、终态迟到和 Consumer 重启重投。
- [x] 8.5 验证定向复现期间节点 claim 身份不被普通恢复器修改、陈旧命令不调用 VBas、后续消息继续处理、`/ready` 持续 ready 且 lag 收敛；扫描并确认不存在 `10 -> 70` 和相关 Consumer unhealthy。
- [x] 8.6 若 8.5 任一条件失败，停止后续压力测试并在 Harness 保存数据库、Kafka offset/lag、容器日志和 readiness 现场证据。
- [x] 8.7 仅在 8.5 全部通过后，同时执行 16 路全量任务与在线人数识别 300 并发、30000 总请求，持续运行直至所有任务和请求得到可判定结果。
- [x] 8.8 采集 16 路全量任务各泳道的提交/开始/完成/总耗时，以及在线请求总数、成功/失败数、总耗时、吞吐量、延迟分位值、三个 VBas 的 GPU/显存、容器重启、节点状态/代次、Consumer lag 和 readiness 时间线。
- [x] 8.9 确认混合压力期间及结束后不存在 `10 -> 70`、周期性 `attempt` 增长、陈旧结果覆盖、Vision Consumer unhealthy、状态冲突重启或 lag 无法收敛，并给出明确通过/不通过结论。

## 9. 文档、证据与变更复审

- [x] 9.1 更新部署手册和故障处理说明，记录内部命令同步升级、视觉专用恢复、源码 manifest 门禁和回滚步骤。
- [x] 9.2 在 Harness 记录本地测试、真实依赖竞态、远端镜像证明与旧版本删除清单、定向复现、混合回归原始证据和最终判定，不覆盖历史失败证据。
- [x] 9.3 逐条复审 proposal、design、delta specs 与实现、测试和部署证据的一致性，并运行 `openspec validate`。
- [x] 9.4 仅在所有门禁通过后将 OpenSpec 任务标记完成，规范提交并推送代码；未通过项保留失败原因和后续动作。
