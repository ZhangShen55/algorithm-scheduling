# Vision 到 VBas 批次调度稳定化证据（2026-09-03）

## 1. 范围与结论边界

本记录对应 OpenSpec `stabilize-vision-vbas-batch-dispatch`，只覆盖：

- Vision 有效 VBas 批次并发与注册实例 `offline` 容量一致；
- 教师多阶段扫描批次 ID 碰撞；
- 瞬时 VBas HTTP 传输错误的有限重试和非空原因；
- A 服务和 VBas HTTP 路径、字段保持不变。

本记录不修改 `test_all_0903_11` 的历史失败事实。修复镜像已发布到远端，但尚未
完成真实课程回归，因此不宣称历史失败已通过业务验收。

## 2. 远端失败现场

目标机为 `192.168.29.11`。查询 `GET /ops/course-jobs/test_all_0903_11` 得到：

```text
task_type=STUDENT_BEHAVIOR
node_id=22660
node_code=STUDENT_BEHAVIOR_ANALYSIS
status=70
updated_at=2026-09-03T10:20:23.829751+08:00
reason=视觉分析失败: VBas 批次调用失败: test_all_0903_11/test_all_0903_11-s-0028:
```

Control 日志显示 `s-0028` 于 `2026-09-03T02:20:23.746Z` 成功取得 `vbas-gpu1` 租约，
约 80ms 后释放；三个 VBas 容器均没有该 batch 的接收日志，容器无重启、OOM 或模型失败。
该证据只支持 Vision 到 VBas 之间发生了请求尚未到达算子的瞬时传输异常，不能事后断言更
具体的 TCP 原因。旧代码使用 `str(exc)`，`TimeoutError()` 等空消息异常会形成上述空原因，
并且没有任何传输重试，故单批失败直接终结学生节点。

## 3. 教师批次 ID 冲突

从 Control 容器 JSONL 中按 `task_id` 和 `work_type=vbas_teacher_batch` 汇总：

```text
63 test_all_0903_11-t-0000
18 test_all_0903_11-t-0001
 4 test_all_0903_11-t-0002
 3 test_all_0903_11-t-0003
 2 test_all_0903_11-t-0004
```

代码审计确认教师粗扫及每个候选窗口、每个加密间隔都会重新调用 `VbasBatchClient.analyze()`，
而旧实现每次把 `batch_index` 从 0 重新编号。`AdaptiveScanPlanner` 已按时间点缓存并只把 missing
点交给 detector，因此这些统计主要表示不同帧集合共用了同一租约工作 ID，不等于同一帧被
执行了 63 次。身份碰撞会破坏审计和未来幂等，仍属于必须修复的严重问题。

修复后 batch ID 使用以下规范身份的 SHA-256 短摘要：流类型、有序 `image_id`、frame index、
毫秒时间戳和区域坐标。不同帧集合即使 `batch_index` 相同也使用不同 ID；同一逻辑批次因网络
故障重试时保持同一 ID，并用结构化 `attempt` 区分尝试。

## 4. 容量行为

远端 `GET /ops/operator-instances/snapshot` 在调查时返回三台 VBas：

```text
vbas-gpu0 lifecycle=ONLINE model_ready=true offline=1 online=24
vbas-gpu1 lifecycle=ONLINE model_ready=true offline=1 online=24
vbas-gpu2 lifecycle=ONLINE model_ready=true offline=1 online=24
```

旧 Vision 固定 `max_concurrency=16`，最多 16 个协程进入租约申请，但 Control 只会发出 3 个
离线租约，其他申请在 Control 客户端内退避轮询。新实现先通过 Control 快照计算：

```text
effective_concurrency = sum(schedulable_vbas.capacity_pools.offline) = 3
```

等待批次先停在 Vision 共享门控中，不申请租约、不调用 VBas；取得门控槽位后仍必须申请
Control 原子租约。快照决定本地并发，租约决定最终准入和具体实例，二者职责不冲突。

## 5. 本地验证

执行命令：

```bash
cd algorithm-scheduling-platform
./.venv/bin/python -m pytest -q tests/test_vbas_batch_client.py
./.venv/bin/python -m pytest -q \
  tests/integration/test_unified_capacity_cross_service.py -k vision_vbas_batch

cd ../vision_orchestrator_service
../algorithm-scheduling-platform/.venv/bin/python -m pytest -q tests
../algorithm-scheduling-platform/.venv/bin/python -m compileall -q app
../algorithm-scheduling-platform/.venv/bin/python -c \
  'from app.main import app; print(app.title)'

cd ..
openspec validate stabilize-vision-vbas-batch-dispatch --strict
git diff --check
```

当前结果：

```text
VBas 批次聚焦测试：16 passed
Vision 服务全量：64 passed
真实 Redis/Control/VBas 租约定向集成：1 passed, 10 deselected
compileall：exit 0
app.main:app 导入：vision-orchestrator-service
OpenSpec strict：valid
```

新增断言覆盖：三实例容量求和、排空/未就绪/零容量实例排除、快照失败回退限流、共享门控动态变化、不同帧集合 ID
不同、相同逻辑批次 ID 稳定、教师自适应扫描不重复提交相同 frame identity、首次 `ReadError`
后释放旧租约并重试成功、空 `TimeoutError` 最终原因包含异常类型/实例/batch/attempt、HTTP
业务失败不进行传输重试。

曾将整个 `test_unified_capacity_cross_service.py` 与聚焦测试合并执行；一个既有在线 OCR 用例
仍期待容量不足立即返回 `50301`，但当前在线容量等待语义使它得到成功响应，随后测试进入最长
300 秒等待，人工中断后结果为 `1 failed, 14 passed`。该失败与本次 Vision 代码无调用关系，
不计为本变更通过证据，也没有被删除或改写；本次使用明确的 Vision 定向集成测试作为边界。

## 6. 远端发布验证

2026-09-03 在 `192.168.29.11` 使用固定提交
`0c6186cbb374db07f3a3b1a1b0be749de75a80b9` 和现有 BuildKit/pip 缓存只重建
`vision-orchestrator-service`。目标机 GitHub Deploy Key 暂时无法鉴权，故从本地已提交分支生成
完整 Git bundle，通过 SCP 传输后创建干净 checkout；远端 checkout 的 HEAD 和镜像 revision
均精确等于上述 SHA。

发布前三个 VBas 实例均为 `ONLINE`、`model_ready=true`、`offline=1`，且在途数为 0。
运行配置保留现场课程并发 `worker.concurrency=16` 和 FFmpeg 并发
`media.max_concurrent_processes=6`；旧 `vbas.max_concurrency` 已不存在，实际 VBas 有效并发由
三实例 `offline` 容量求和得到 3。

替换和清理证据：

```text
old container: d8cd1f120c8a5eaf9eb49eb4b0bd2a04e079f9730c684c27e9b72b6ced1031e5
new container: d0aa0e65163e850eb41c45197527abb917f44768a98483737153452e4f333beb
old image:     7e6940d496333c57b0af36efc697f9c8d3fc3db42d26d74b7abddd11c776f0d4
new image:     a51018ad8c02d6d906e421d8e0fc7983b3abc632e1912e67976e9bbed3284d41
```

Compose 已删除旧运行容器，旧镜像在确认无容器引用后按精确 image ID 删除。新容器
`health=healthy`，`/health` 返回 `ok`，`/ready` 的 Kafka、PostgreSQL、Control 和视觉消费循环
全部就绪，启动日志无 `ERROR/CRITICAL` 记录。

## 7. 当前判定与后续门禁

本地静态、单元、真实 Redis/Control 租约定向集成和远端发布健康门禁已通过。尚待使用
新 `task_id` 重跑学生和教师真实课程任务，检查三个 VBas 实例接收、批次 ID、瞬时重试和
节点终态。
