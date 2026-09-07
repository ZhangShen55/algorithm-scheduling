# VBas 在线路由与容量等待验证

## 范围

本记录对应 OpenSpec 变更 `add-vbas-online-routing-and-capacity-wait`。验证在线教师、学生、
纯人数三个网关路由，在线/离线容量池隔离，实例内在线 FIFO 队列，以及容量暂不可用时的等待、
退避、超时和租约释放补位。

## 本地自动化验证

从工作区根目录执行：

```bash
PYTHONPATH="$PWD/online_gateway_service:$PWD/algorithm-scheduling-platform:$PWD" \
  algorithm-scheduling-platform/.venv/bin/python -m pytest -q online_gateway_service/tests

PYTHONPATH="$PWD/vision_orchestrator_service:$PWD/algorithm-scheduling-platform:$PWD" \
  algorithm-scheduling-platform/.venv/bin/python -m pytest -q vision_orchestrator_service/tests

PYTHONPATH="$PWD/vbas:$PWD/algorithm-scheduling-platform:$PWD" \
  algorithm-scheduling-platform/.venv/bin/python -m pytest -q \
  vbas/tests/test_capacity_pools.py vbas/tests/test_config_loader.py \
  vbas/tests/test_tias_api_surface.py vbas/tests/test_tias_worker_state.py

PYTHONPATH="$PWD:$PWD/algorithm-scheduling-platform" \
  algorithm-scheduling-platform/.venv/bin/python -m pytest -q \
  algorithm-scheduling-platform/tests/test_operator_deployment_integration.py \
  algorithm-scheduling-platform/tests/integration/test_redis_operator_registry.py

PYTHONPATH="$PWD:$PWD/algorithm-scheduling-platform" \
  algorithm-scheduling-platform/.venv/bin/python -m compileall -q \
  control_service/app orchestrator_service/app vision_orchestrator_service/app \
  online_gateway_service/app algorithm-scheduling-platform/packages
```

本次结果：Online Gateway `63 passed`，Vision `50 passed`，VBas 准入/配置/API `13 passed`，
部署契约和 Redis 注册集成 `43 passed`，平台服务与共享包 `compileall` 通过。FastAPI 的弃用警告
不影响测试结果。

## 关键行为断言

- `/online/vbas/teacher`、`/online/vbas/student`、`/online/vbas/person-count` 均按单个 HTTP 请求
  申请 `online` 租约，成功响应透传 VBas 原始 JSON。
- 单个实例 `MaxConcurrentOnlineRequests=24`、`MaxQueueOnlineSize=24` 时，运行 24 个、等待 24 个；
  队列满载返回 429，离线 batch 使用独立 `MaxConcurrentOfflineBatches`。
- 等待请求以 0.2 秒基础间隔退避并带抖动，最多等待 300 秒；超时不产生释放请求，不泄漏租约。
- 512 个在线模拟请求能够在三个实例各 24 个运行槽位下等待释放并全部完成，单实例峰值不超过 24。
- 注册中心不可用的 503 不伪装成容量不足；只有容量不足响应进入等待循环。

## 目标机验收结果（192.168.29.11）

验证时间：2026-08-29（中国标准时间）。发布工作树为
`/root/workspace/algorithm-scheduling-release-6ddbbf3`，平台代码修复版本为
`3182118d7a78debdd4b4cd3975e749dbd35a6e8f`，VBas 镜像为 `algorithm-vbas:v1.0_260829`。
远端运行时使用与算子容器一致的注册 token；切换网关时曾误用另一份 token，造成 VBas 心跳
短暂出现 HTTP 401，已恢复正式 token 后三实例重新注册，后续证据均在恢复后采集。

### 注册、健康与容量

- Control `/ops/readiness`、Orchestrator `/ops/readiness`、Vision `/ready`、Online Gateway
  `/health` 均返回成功。
- Control 注册快照显示 `vbas-gpu0`、`vbas-gpu1`、`vbas-gpu2` 均为 `ONLINE`、`model_ready=true`。
- 三个实例的容量池均为 `offline=1`、`online=24`；验证结束时在线/离线活动租约均归零。
- 三个 VBas 容器分别绑定 GPU 0、1、2，`nvidia-smi` 可见每个容器的 `vbas` 进程。

### 三路真实图片请求（5.5）

使用目标机 `/data/course/_harness/fixtures/vbas-00000001-ddna.jpg`，通过网关提交真实图片：

| 路由 | 下游接口 | HTTP | 耗时 | 结果 |
| --- | --- | ---: | ---: | --- |
| `/online/vbas/teacher` | `/ImageDetect/teacher/v1.0.0` | 200 | 1.83 秒 | 原始 `StatusObject/DataList` |
| `/online/vbas/student` | `/ImageDetect/student/v1.0.0` | 200 | 1.83 秒 | 原始 `StatusObject/DataList` |
| `/online/vbas/person-count` | `/AE/SyncTasks2` | 200 | 0.04 秒 | 原始 `Response/TaskID/FreeCapacity/TaskResult` |

人数接口同时验证了 `ImageList[].Data` Base64 字段和 `AnalysisRule.AlgParams.ImageResolution`，
网关不改变下游响应结构。

### 三实例分配（5.4）

72 个学生单图请求同时提交，网关日志记录的下游实例分布为：`vbas-gpu0=22`、
`vbas-gpu1=27`、`vbas-gpu2=24`，三实例均被实际选择；Control 租约和释放计数相互对应。

该组中 GPU0 在测试前已被其他算子占用约 24 GiB，学生模型有 14 个请求记录
`OutOfMemoryError`，但路由仍覆盖三实例且租约均正确释放。该结果作为算力容量风险记录，不能
把声明容量 24 解释为在当前三卡混部状态下保证 24 路模型推理成功。

### 在线 512 并发与等待（5.6）

请求为 512 个不同 `TaskID` 的纯人数单图请求，配置为每实例
`MaxConcurrentOnlineRequests=24`、`MaxQueueOnlineSize=24`。负载机同时创建 512 个 HTTP 请求，
不跨实例拆分单个请求。

- 首轮：512/512 成功，15.07 秒，P50 10.62 秒，P95 14.47 秒。
- 第二轮：511/512 成功，14.15 秒；1 个请求返回业务 `50301`，其余请求成功。Control 日志
  显示高峰期间反复出现“暂无可用算子容量”的 503，随后租约持续释放并重新取得；该轮作为
  瞬时容量/控制面压力风险保留，不作为通过轮次。
- 空闲恢复后的复测：512/512 成功，14.39 秒，P50 9.57 秒，P95 13.81 秒；三实例峰值活动
  租约均为 24，租约取得增量为 `vbas-gpu0=169`、`vbas-gpu1=176`、`vbas-gpu2=167`，
  三实例均覆盖且最终归零。

结果证明超过单实例 24 个运行槽位的请求会在网关等待容量释放后继续完成；同时保留第二轮的
单次 `50301` 作为后续稳定性优化输入。

### 本地与远端回归（5.7）

- Online Gateway：`63 passed`。
- Vision Orchestrator：`50 passed`。
- VBas 准入、配置、API、运行状态：`13 passed`。
- 部署契约与 Redis 注册集成：`43 passed`。
- 平台服务与共享包 `compileall`：通过。
- 远端四个平台服务和基础设施容器：健康；三 VBas 容器：健康。

本记录不保存 Base64、完整请求/响应、凭据或模型内容，只保留可复核的路由、计数、耗时、状态和
资源摘要。

## 2026-09-01 - 在线人数识别 256 并发 / 10240 总请求

目标机：`192.168.29.11`，接口：`/online/vbas/person-count`，请求为单图人数识别，
通过 Online Gateway 动态路由到 `vbas-gpu0`、`vbas-gpu1`、`vbas-gpu2`。压测参数为并发
`256`、总请求 `10240`，每个请求使用独立的请求上下文；未修改代码、镜像或容器。

### 压测结果

| 指标 | 结果 |
| --- | ---: |
| 总请求 | 10240 |
| HTTP 200 | 10240 |
| 失败 | 0 |
| P50 | 9.3388 秒 |
| P95 | 29.9537 秒 |
| 最大耗时 | 119.1523 秒 |

脚本最终汇总为 `success=10240`、`status_counts={"200":10240}`。因此本轮没有网关拒绝、
业务失败或客户端超时。

### 三实例和 GPU 观测

压测结束后的 Control `/ops/operator-instances` 快照：

- `vbas-gpu0`：`ONLINE`、`model_ready=true`、`inflight=0`，在线池 `0/24`；
- `vbas-gpu1`：`ONLINE`、`model_ready=true`、`inflight=0`，在线池 `0/24`；
- `vbas-gpu2`：`ONLINE`、`model_ready=true`、`inflight=0`，在线池 `0/24`。

结束时 `/ops/queues` 为 `queues=[]`、`outbox_pending=0`，Control `/ops/readiness` 的
PostgreSQL、Redis 和 schema 检查均为 ready。三实例容器均为 `healthy`，没有重启。

压测前显存约为 GPU0/1/2：`12255/12253/12373 MiB`；采样期间最高观测约为
`13089/13087/13243 MiB`，结束采样为 `13089/13087/13243 MiB`。压测期间 GPU 利用率
随请求处理在 `0%` 至约 `88%` 之间波动，说明请求确实进入 GPU 推理路径；结束时三实例
VBas 进程仍分别驻留约 `3194/3194/3806 MiB`。未观察到显存随请求数持续单调增长。

VBas `WorkerStatus` 的成功计数是进程生命周期累计值，不能直接当作本轮请求分配数；本轮
成功率以压测客户端的 10240 次 HTTP 汇总为准。GPU1 状态中的一条历史 `failure_count=1`
（图片读取错误）不属于本轮客户端失败，已与本轮 `0` 失败事实分开记录。

### 结论与边界

本轮证明在当前三实例、每实例在线容量 24、在线队列 24 的配置下，256 并发、10240 总请求
可以通过网关等待容量释放并全部完成，三实例保持健康，租约和队列最终归零。该结果不代表
更大图片、不同模型组合或跨请求 GPU 显存上限已得到证明；后续如需提升并发，应继续结合
显存峰值和 P95/P99 延迟进行分级压测。

## 2026-09-01 - 40 路全量任务与在线人数压测混合验证

本次验证同时提交 40 个 `URGENT` 全量课程任务（每个任务包含 `PPT`、`ASR`、
`TEACHER_BEHAVIOR`、`STUDENT_BEHAVIOR`）并启动在线人数识别 256 并发、10240 总请求。
全量任务使用独立前缀 `full-load-260901-rerun-`，在线请求仍通过
`/online/vbas/person-count`，未向算子传递实例或 GPU 信息。

### 在线人数结果

- 10240 个请求中 10236 个 HTTP 200；4 个客户端在 180 秒请求超时上限返回状态 0；
- P50 `22.4971` 秒，P95 `68.2556` 秒，最大 `180.1135` 秒；
- 网关日志中其余请求均由三个 VBas 实例返回 200，容量不足时出现 503 租约尝试，随后
  释放并重试成功；测试结束三实例 `running_online_requests=0`、`queued_online_requests=0`，
  活动租约归零；
- 结束时三实例仍为 `ONLINE`/`model_ready=true`，容器健康，GPU 显存约
  `13167/13153/13273 MiB`（采样峰值约 `14455/13973/14173 MiB`），没有观察到持续单调
  增长。该轮相对空闲时全成功轮次的差异，说明混合离线负载下客户端等待上限会影响在线
  成功率。

### 全量任务结果与阻塞

首批临时提交曾误用不存在的 `教师2.mp4`，其 ASR 返回 404；该批次被停止，不作为有效
全量结果。修正批次使用用户提供的 `教师1.mp4`，40/40 提交成功；截至停止轮询时：

- `ASR`：40/40 完成；
- `PPT`：部分切片已完成，其余仍在 CPU 容量中运行或等待；
- 教师/学生视觉节点：已领取或等待，未形成 40/40 终态。

继续执行被 `vision-orchestrator-service` 的必需消费循环阻塞。其 `/ready` 返回 503，原因
为 `视觉进度基础设施处理失败: 只有处理中节点可以更新进度: 21729`；节点 21729 属于首批
错误 URL 任务 `full-load-260901-005` 的学生视觉节点。重启视觉编排容器后，Kafka 重放该
异常消息，状态仍为 `not_ready`，因此没有通过修改数据库或伪造终态继续测试。该问题应在
视觉命令消费循环的异常隔离/幂等恢复变更中修复后，使用全新任务前缀重跑。

测试期间 `/data` 根文件系统剩余约 102 GiB（约 93% 已用），每个全量课程目录约 1.9 GiB。
为避免继续提交导致磁盘耗尽，停止了本次全量压测轮询脚本；已提交任务及其数据未被人工删除，
不把本轮视为全量泳道通过证据。
