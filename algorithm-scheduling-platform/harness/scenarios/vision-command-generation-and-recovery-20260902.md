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

## 2026-09-02 目标镜像构建与静态门禁

- 目标 Git SHA：`60a0c25fc8c98bd358010f74ba48df6b86003b97`。
- 干净 detached release checkout：`/root/workspace/algorithm-scheduling-release-60a0c25`。
- 使用 BuildKit 和现有缓存只构建 Orchestrator 与 Vision；FFmpeg Debian 依赖下载较慢，但构建最终正常完成，未清理构建缓存。
- Orchestrator 新镜像：`sha256:2b0c00d030be9a91d709fb3136b9fbbd436aef98053d1ef930d86b1d0737442e`，架构 `amd64`，OCI revision 等于目标完整 SHA。
- Vision 新镜像：`sha256:7e6940d496333c57b0af36efc697f9c8d3fc3db42d26d74b7abddd11c776f0d4`，架构 `amd64`，OCI revision 等于目标完整 SHA。
- 对两个镜像分别创建临时容器，提取 `/app/app`、`/app/packages`、`/app/.source-manifest.json` 与 `/app/.source-manifest.sha256`，使用目标 checkout 重新生成期望 manifest；两镜像的 revision、嵌入 digest、manifest 与实际文件哈希全部通过校验。
- 本阶段只证明新镜像来源可信。旧 Orchestrator/Vision 容器与镜像仍保留，等待新容器通过运行时门禁后再精确删除。

## 2026-09-02 同步替换与运行门禁

- 第一次替换使用临时环境变量覆盖 `media.max_concurrent_processes`，字符串值被严格整数校验拒绝，Vision 在应用导入阶段退出。该次失败没有进入消息处理或压力测试；保留现网 `config.toml` 挂载并移除临时数值环境覆盖后，重新同步创建两个服务。
- 运行配置从既有服务器文件读取：Orchestrator `worker.node_concurrency=16`；Vision `worker.concurrency=16`、`media.max_concurrent_processes=6`、`vbas.max_concurrency=3`。
- Orchestrator 新容器：`f902f31b062a35f296d4a5bf2e13beedaf9cdafdfc53d92c6ea0fea42e52e509`，镜像 `sha256:2b0c00d030be9a91d709fb3136b9fbbd436aef98053d1ef930d86b1d0737442e`，health 为 healthy，重启数 `0`。
- Vision 新容器：`d8cd1f120c8a5eaf9eb49eb4b0bd2a04e079f9730c684c27e9b72b6ced1031e5`，镜像 `sha256:7e6940d496333c57b0af36efc697f9c8d3fc3db42d26d74b7abddd11c776f0d4`，health 为 healthy，重启数 `0`。
- 两容器分别完成 `/app/app` 与 `/app/packages` 的 `compileall`、`from app.main import app`、进程启动检查；Orchestrator `/health` 与 `/ops/readiness`、Vision `/health` 与 `/ready` 均返回 HTTP 200，所有必需后台循环及 PostgreSQL、Kafka、Control 依赖均为 ready。
- 运行容器再次通过 `preflight-source-manifests`，最近日志没有 `10 -> 70`、`节点状态不允许从 10 转换到 70`、traceback 或 Consumer unhealthy；切换后的视觉 Consumer lag 从基线 `1334` 降至 `37`，仍需在定向回归中确认最终收敛。
- 删除旧 Orchestrator 容器 `b12a73ea9e5e8941849b32628b5a8f052e9b9e27ef14fe575c06ffec25885042` 与旧镜像 `sha256:6888afcdb4d70761fb67af2ef0f4d1598ceca03c1faad4a5c20f968c5840bfed`。
- 删除旧 Vision 容器 `eb687df1a70f9a29597a1ef1a3d895c0a77d7e7d27c226fa216daecd23acc796` 与旧镜像 `sha256:e60f3b329f892933fd3f164c34955205907bbc8977fb4427fce3fc886dca8126`。

## 2026-09-02 原状态机故障定向回归

- 使用新 Vision 镜像启动隔离消费组 `vision-directed-regression-20260902`，容器 ID `b3400f5e2fada64b5c42a7d35aec15373600aafe265ebdd3afb9ac5a7cf8abc1`；该组只消费定向注入消息，不修改生产消费组 offset。
- 对已完成节点 `22484` 注入三条消息：旧 `attempt/token`、匹配当前终态身份、缺少代次的旧格式消息，Kafka offset 为 `4269~4271`。隔离组连续提交到 `4272`、lag 为 `0`，节点的 `status=60`、`attempt=70`、token、原因和更新时间逐字不变，三个受控 command ID 均未出现在容量租约或 VBas 日志中，Consumer readiness 保持 HTTP 200。
- 对 claimed 时间已超过普通恢复阈值的运行视觉节点 `22494`，远端 Repository 的 `list_stale_claimed_nodes()` 返回 0 个视觉节点，直接调用 `recover_stale_claimed_node()` 返回 `False`；调用前后状态、attempt、token、claimed_by、claimed_at 和 updated_at 完全一致。
- 复制节点 `22474` 的真实当前代次命令到 offset `4272`。隔离组重启前 `CURRENT-OFFSET=4272`、`LOG-END-OFFSET=4273`、lag 为 `1`，证明消息未提交；重启后 Consumer ID 变化，消息按终态幂等路径重投并提交到 `4273`、lag 为 `0`。节点原处理因视频末端 `2205.0s` 未生成抽帧图片进入业务失败 `70`，重投未覆盖终态，也未触发状态冲突。
- 生产组在 630 秒监控窗口内从 lag `31` 收敛到 `0`，运行视觉节点从 `8` 收敛到 `0`；期间 remaining attempt 范围只随节点终结缩小，没有任何节点增加 attempt，Vision `/ready` 每次均返回 `200 ready`。
- 最终生产组 `CURRENT-OFFSET=4273`、`LOG-END-OFFSET=4273`、lag 为 `0`；自新版本启动以来，Vision 日志中 `10 -> 70`、`节点状态不允许从 10 转换到 70`、Consumer unhealthy、traceback 和 ERROR 计数均为 `0`，生产 Vision 容器重启数为 `0`。
- 定向门禁全部通过，因此任务 8.6 的“失败即停止并留存现场”分支未触发；可以进入混合压力验收。

## 当前结论

## 2026-09-02 混合压力验收

原始证据保存在远端且未覆盖历史失败报告：

- 本次报告：`/root/workspace/algorithm-scheduling/algorithm-scheduling-platform/deploy/reports/vision-fix-16full-300x30000-20260902-212943`
- 历史失败报告：`/root/workspace/algorithm-scheduling/algorithm-scheduling-platform/deploy/reports/mixed-16full-300x30000-20260902-172431`
- 主要文件：`summary.json`、`full-submissions.json`、`full-progress.jsonl`、`full-result.json`、`online-result.json`、`resource-samples.jsonl`、`platform-monitor.log` 和 `final-gates.txt`

执行窗口为 `2026-09-02T13:34:04Z` 至 `2026-09-02T14:38:30Z`，总墙钟时间 `3866.595s`。16 路全量任务与在线人数识别 300 并发、30000 总请求同时启动，16 路北向提交全部受理，最终 16 路课程和 30000 个在线请求均得到可判定结果。

### 全量课程结果

- 全量任务总耗时 `3866.237s`；首路在 `683.694s` 进入终态，末路在 `3866.236s` 进入终态。
- PPT：`16/16` 完成；`PPT_SLICE` 单节点耗时最小/平均/最大为 `389.915/499.331/568.174s`，`PPT_OCR` 为 `8.356/21.463/29.822s`。
- ASR：`16/16` 完成；`ASR_TRANSCRIPTION` 单节点耗时最小/平均/最大为 `101.595/266.210/410.445s`。
- 教师行为：`16/16` 完成；单节点耗时最小/平均/最大为 `373.872/2080.985/3392.403s`。
- 学生行为：`15/16` 完成、`1/16` 业务失败；成功节点耗时最小/平均/最大为 `545.438/1956.674/3174.958s`，失败节点耗时 `3346.158s`。
- 唯一失败为 `full-15/STUDENT_BEHAVIOR_ANALYSIS` 的 VBas 批次 `s-0033` 调用失败。该节点始终属于当前执行代次，使用 CAS 从 `RUNNING(50)` 合法进入 `FAILED(70)`，`attempt=1`；不是旧命令触发的 `PENDING(10) -> FAILED(70)` 状态冲突。

### 在线人数识别结果

- 300 并发、30000 请求全部返回 HTTP 200，总耗时 `1874.514s`，吞吐量 `16.004 req/s`。
- 业务成功 `29957`，业务失败 `43`；样本错误为 `50301 等待 VBas 在线容量超时` 和 `50000 VBas 在线分析调用失败`，均为可判定业务响应，与 Vision Consumer 状态机无关。
- 延迟最小/平均/P50/P90/P95/P99/最大为 `0.854/18.686/13.634/38.665/50.729/81.704/221.443s`。
- 临时压测驱动以“零业务失败”为额外退出条件，因此进程退出码为 `1`；OpenSpec 本变更的状态机门禁要求全部请求得到可判定结果并且不得再次出现 Vision Consumer 状态冲突，二者需要分开判断。43 个在线业务错误和 1 个离线 VBas 批次错误作为后续容量与调用可靠性问题保留，不伪装为本状态机修复成功项。

### 资源与一致性结果

- GPU0 显存最小/最大/结束为 `13251/14653/13253 MiB`，平均/峰值利用率 `28.48%/88%`。
- GPU1 显存最小/最大/结束为 `13237/14055/13239 MiB`，平均/峰值利用率 `28.20%/100%`。
- GPU2 显存最小/最大/结束为 `13357/14655/13359 MiB`，平均/峰值利用率 `39.86%/100%`。
- 三个 VBas 在结束后均为 `ONLINE`、`model_ready=true`、`inflight=0`；Orchestrator 与 Vision 容器重启数均为 `0`。
- 本批 64 个节点均只领取一次；所有节点的 `attempt` 最小/最大均为 `1/1`。没有周期性回到 `10/30/40`，也没有陈旧结果覆盖。
- 压测期间 Vision `/ready` 持续 HTTP 200；结束后 Orchestrator readiness 和 Vision readiness 均为 ready。
- Vision Consumer lag 在长视频处理期间随 32 条视觉命令增长，最终 `CURRENT-OFFSET=4305`、`LOG-END-OFFSET=4305`、lag 为 `0`。
- 压测窗口内 Orchestrator/Vision 日志扫描中，`10 -> 70`、`节点状态不允许从 10 转换到 70`、Consumer unhealthy、Traceback 计数均为 `0`。

## 当前结论

`stabilize-vision-command-generation-and-recovery` 的状态机验收通过：原故障在定向复现与 64 分钟混合压力中均未再次出现，视觉节点没有周期性重领，Consumer 始终健康，容器未重启且 lag 最终收敛。混合负载同时暴露 43 个在线 VBas 容量/调用业务错误和 1 个离线 VBas 批次业务失败；它们不属于本变更的命令代次与恢复故障，但必须作为后续容量等待和 VBas 调用可靠性工作继续处理。
