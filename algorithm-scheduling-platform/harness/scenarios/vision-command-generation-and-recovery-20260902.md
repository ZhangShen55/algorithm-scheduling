# Vision 命令代次与恢复一致性验证（2026-09-02）

## 变更范围

- OpenSpec：`stabilize-vision-command-generation-and-recovery`
- 受影响组件：`orchestrator_service`、`vision_orchestrator_service`、`platform_contracts`、`platform_common.repository` 和平台部署门禁
- 外部契约：A 服务 HTTP 接口、VBas HTTP 接口、Kafka topic、任务状态整数值均保持不变
- 内部破坏性变更：视觉命令新增必填 `dispatch_attempt` 与 `claim_token`，Orchestrator 与 Vision 必须同步部署

## 修复前现场

- 普通 `StaleNodeRecovery` 查询所有状态 `40/50` 的节点，只排除 `PPT_SLICE`，没有排除 `TEACHER_BEHAVIOR_ANALYSIS` 与 `STUDENT_BEHAVIOR_ANALYSIS`。
- 普通执行器使用 `orchestrator-*`，视觉协调器使用 `orchestrator-visual-*`；视觉节点处理期间没有普通节点租约，超过 `stale_node_recovery_seconds=120` 后会被错误恢复。
- 现场视觉节点出现 `50 -> 30 -> 10 -> 40 -> 50` 循环，观察到 `attempt` 已增长至 `26~31`。
- 旧视觉命令只有固定 `command_id`、`node_id` 和 `submission_id`，没有 claim 代次；节点已恢复到 `PENDING(10)` 后，旧命令失败分支仍调用无条件 `transition_node(..., FAILED)`，触发 `节点状态不允许从 10 转换到 70`。
- 异常使同批后续任务取消、offset 不提交并导致 Vision 必需 Consumer loop unhealthy。
- 远端 Vision 镜像声明 revision 为 `ae4f2e6d3a0f2f8af6b0b8e1cb450ed54b0c99b0`，但容器内 `WorkContext` 不包含该提交已有的 `capacity_pool` 字段，出现 `WorkContext.__init__() got an unexpected keyword argument 'capacity_pool'`；这证明 revision 标签不能单独作为源码一致性证据。
- 现场报告保留在远端 `/root/workspace/algorithm-scheduling/algorithm-scheduling-platform/deploy/reports/mixed-16full-300x30000-20260902-172431/final-summary.json`，本变更不得覆盖或重标该失败证据。

## 与上一变更的边界

`fix-vision-consumer-terminal-state-race` 已解决节点进入 `COMPLETED(60)`、`FAILED(70)` 或 `CANCELLED(80)` 后的迟到进度/完成幂等问题。它没有覆盖节点被错误恢复到 `10/30/40`、命令 `attempt/claim_token` 不匹配、普通恢复器误回收视觉节点或业务失败执行无条件状态转换。本变更是新的执行代次一致性修复，不否定上一变更的历史证据。

## 当前实现

- 普通过期节点 SQL 查询、条件更新和应用恢复循环三层排除教师/学生视觉节点。
- `VisualAnalysisCommand` 新增必填 `dispatch_attempt` 与 `claim_token`；同 claim 重发使用稳定命令 ID，新 claim 生成新 ID。
- 旧格式命令被识别为 `LEGACY_STALE` 语义并按分区连续确认，等待新 Orchestrator 启动恢复重发。
- Repository 新增视觉命令准入，以及进度、完成、失败的代次 CAS；陈旧和终态命令不再进入无条件 `10 -> 70` 写入。
- Vision Consumer 在媒体处理和 VBas 调用前检查权威代次；陈旧命令和已落库业务失败不取消同批后续消息，基础设施故障仍 fail-closed。

## 本地验证进度

执行：

```bash
algorithm-scheduling-platform/.venv/bin/pytest -q \
  orchestrator_service/tests/test_visual_runtime.py \
  vision_orchestrator_service/tests/test_runtime.py \
  algorithm-scheduling-platform/tests/test_vision_kafka_boundary.py \
  vision_orchestrator_service/tests/test_visual_analyzer.py \
  orchestrator_service/tests/test_recovery.py \
  -k 'not vision_vbas_calls_use_control_service_capacity_lease'
```

结果：`64 passed, 1 deselected`。被排除用例属于工作树中既有 `capacity_pool` 请求断言差异，不是本变更的视觉状态机回归结论。

四服务项目回归结果：

- `control_service`：`25 passed`
- `online_gateway_service`：`64 passed`
- `orchestrator_service`：`98 passed`
- `vision_orchestrator_service`：`63 passed`

源码 manifest 工具测试为 `5 passed`，覆盖确定性输出、源码篡改、manifest 缺失、revision 伪装和目标 checkout 不一致。

真实依赖使用 `192.168.29.11`，结果如下：

- PostgreSQL 恢复排除和视觉 CAS：`2 passed`；覆盖成功、代次变化、claim 变化、并发终态、节点不存在、事务回滚和 claim 并发刷新。
- PostgreSQL + Kafka 跨服务：`1 passed`；覆盖陈旧命令、当前命令、Consumer 未提交重投，以及 Orchestrator 启动时同代稳定重发。

## 2026-09-02 远端发布前基线

- 远端 checkout：detached `d19e5e46b9cb0c78d775727e1cf33a75a4321df8`，存在受控运行配置和旧 Vision 热修复的未提交改动，因此禁止在该脏目录直接构建。
- Orchestrator：容器 ID `b12a73ea9e5e8941849b32628b5a8f052e9b9e27ef14fe575c06ffec25885042`，image ID `sha256:6888afcdb4d70761fb67af2ef0f4d1598ceca03c1faad4a5c20f968c5840bfed`，revision `6ddbbf3aa2688d3d15905d8af7f54d5ed21c87bf`，readiness 为 ready。
- Vision：容器 ID `eb687df1a70f9a29597a1ef1a3d895c0a77d7e7d27c226fa216daecd23acc796`，image ID `sha256:e60f3b329f892933fd3f164c34955205907bbc8977fb4427fce3fc886dca8126`，revision `ae4f2e6d3a0f2f8af6b0b8e1cb450ed54b0c99b0`，readiness 为 503。
- Vision readiness 原因：`节点状态不允许从 10 转换到 70`。
- `vision-orchestrator` Consumer lag：`1334`。
- 19 个运行视觉节点的 `attempt` 为 `65~76`，证明普通恢复器仍在周期性回收长时视觉节点。
- 基线保存后已停止上述两个旧容器；旧容器和旧镜像暂时保留，等待新版本全部门禁通过后再精确删除。

## 远端验收门禁

1. 从同一最终 Git SHA 构建并同步替换 Orchestrator/Vision；新容器通过源码 manifest、health 和 readiness 后，精确删除旧容器与旧镜像。
2. 定向验证超时视觉节点、旧 claim 命令、终态迟到与 Consumer 重投；任一 `10 -> 70`、周期性 `attempt` 增长、Consumer unhealthy 或 lag 不收敛均立即停止。
3. 只有第二步通过，才同时执行 16 路全量任务与在线人数识别 300 并发、30000 总请求。
4. 最终报告必须包含镜像身份与删除清单、分泳道耗时、在线吞吐/延迟、三卡 GPU/显存、节点代次、Consumer lag、readiness 时间线和明确结论。

## 当前结论

当前只达到内存单元回归层级。真实 PostgreSQL/Kafka、服务启动、镜像来源证明、远端定向复现和混合压力尚未完成，不得宣称该 OpenSpec 已通过或归档。
