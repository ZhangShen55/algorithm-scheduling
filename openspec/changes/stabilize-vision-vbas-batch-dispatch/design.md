## 背景

`VbasBatchClient` 当前在进程启动时创建固定大小为 `[vbas].max_concurrency=16` 的共享信号量。三台 VBas 实例实际各上报 `capacity_pools.offline=1`，因此最多只有 3 个批次能取得离线租约，其余协程会持续向 Control 申请租约并收到容量不足。Control 的租约没有超卖，但 Vision 的本地并发与集群真实容量脱节。

教师自适应扫描会分别执行粗扫和多轮加密扫描。每轮调用 `VbasBatchClient.analyze()` 都从 `batch_index=0` 重新编号，现有 `{task_id}-t-{batch_index}` 不能代表逻辑批次。远端日志因此出现同一 `test_all_0903_11-t-0000` 先后租给不同实例几十次，实际多数是不同帧集合的身份碰撞，而不是自适应点缓存失效。

学生批次 `s-0028` 在 Control 取得租约后约 80ms 释放，三个 VBas 容器均无接收记录，错误原因又为空。这与 Vision 到实例之间的连接、写入或任务级 `TimeoutError` 一致。当前客户端不重试任何这类瞬时故障，并会取消该课程尚未开始的全部批次，最终直接写入 `FAILED(70)`。

## 目标 / 非目标

**目标：**

- Vision 实际同时调用 VBas 的批次数等于当前可调度 VBas 实例离线容量总和。
- Control 租约继续负责跨 Vision、实例生命周期和并发竞争的最终原子准入。
- 每个逻辑批次具有稳定、可审计且不会跨帧集合碰撞的 ID。
- 瞬时 HTTP 故障有限恢复，最终失败具有完整异常类型和调用上下文。
- 保持 A 服务和 VBas 的现有 HTTP 契约不变。

**非目标：**

- 不改变 `MaxConcurrentOfflineBatches`、在线容量池或 VBas 内部推理并发。
- 不让 Vision 直接读取 Redis，也不绕过 Control 租约。
- 不承诺网络结果丢失时算子绝不重复计算；本次通过稳定批次 ID 为后续算子侧结果幂等提供可靠身份。
- 不修改教师自适应扫描算法和学生聚合口径。

## 技术决策

### 1. 从 Control 快照动态计算离线容量

Vision 通过现有 `GET /ops/operator-instances/snapshot` 读取实例快照，只统计：

```text
operator_code == "vbas"
lifecycle == "ONLINE"
model_ready == true
capacity_pools.offline > 0
```

有效并发采用：

```text
effective_concurrency = sum(instance.capacity_pools.offline)
```

当实例容量一致时，这正是用户确认的 `VBas 实例数 × MaxConcurrentOfflineBatches`；求和还能正确处理滚动发布期间实例容量不一致的情况。快照按短周期缓存，避免每帧请求都访问 Control。

备选方案是把配置固定改为 3。该方案无法随实例上线、排空、模型未就绪或容量调整变化，故不采用。

### 2. 使用服务级动态容量门控，不替代租约

`VbasBatchClient` 所有课程和所有教师/学生轮次共享一个动态门控器。进入门控的批次不超过最新有效离线容量；门控内部等待，不向 VBas 发送请求，也不占用 Control 租约。取得门控槽位后，批次仍按现有流程向 Control 申请 `offline` 租约，Control 根据实例实时负载选择具体实例。

容量快照只决定 Vision 本地并发，不用于选实例。即使快照短暂陈旧，Control 仍能防止租约超卖；若实例减少，已在途批次完成，新的批次在租约层等待。

### 3. 批次身份由工作内容稳定派生

逻辑批次 ID 保留任务和流类型前缀，并加入由以下规范数据计算的 SHA-256 短摘要：

- 流类型；
- 按请求顺序排列的 `image_id`；
- `frame_index` 与毫秒时间戳；
- 区域坐标。

格式为 `{task_id}-{stream}-{batch_index:04d}-{digest}`。`batch_index` 便于阅读，摘要负责区分不同扫描轮次和区域。同一帧集合重试得到同一 ID，不同帧集合即使索引同为 `0000` 也得到不同 ID。

不使用随机 UUID，因为随机值无法识别真正的同批重试，也不能支持后续幂等。

### 4. 仅重试瞬时传输故障

连接、连接池、读写、远端断开和总超时属于可重试故障。每次尝试都先取得新的 Control 租约，并在失败后释放原租约；重试保持同一逻辑 batch ID，并记录 `attempt/max_attempts`。HTTP 4xx/5xx、VBas 业务状态失败、JSON/图片数量/图片 ID 不一致不自动重试。

最终错误文本必须至少包含异常类名、任务 ID、批次 ID、实例 ID、尝试次数和非空消息。这样 `TimeoutError()` 即使 `str(exc)` 为空，也会呈现 `TimeoutError`。

### 5. 当前课程的失败隔离保持有界

单批瞬时故障达到重试上限后，当前课程仍按既有规则停止尚未开始的批次并将节点失败；其他课程不受影响。已经成功的批次不会在同一次 `analyze()` 内重新执行。跨进程重启后的批次级持久化结果复用不在本次范围。

## 风险 / 权衡

- [快照短暂不可用] -> 在容量等待时按退避重试；Control 租约仍为权威，不回退到任意固定大并发。
- [实例下线导致快照容量降低] -> 动态门控不取消已在途调用，只限制后续进入；租约再次校验实例生命周期。
- [HTTP 响应丢失后重试可能重复算子计算] -> 保持稳定 batch ID 并记录 attempt；后续可在 VBas 增加结果幂等缓存，本次不虚构已经完成的算子幂等。
- [动态门控增加一次 Control 查询] -> 使用短 TTL 缓存，且只读取现有运维快照，不增加 Redis 直连或数据库负载。

## 迁移计划

1. 本地完成容量快照解析、动态门控、稳定批次 ID、瞬时重试和错误文本测试。
2. 从固定 Git SHA 构建新的 `vision-orchestrator-service` 镜像并保留构建缓存。
3. 替换 Vision 容器，确认 health/readiness 后删除被替换的旧容器和旧镜像。
4. 用不同 task_id 重跑学生行为任务，并复核三个 VBas 日志、Control 租约和节点终态。
5. 重跑教师行为任务，确认同一任务中不同帧集合不再共用 batch ID；真正重试保留同一 ID 且 attempt 可区分。

## 待确认问题

无。当前三实例、每实例 `MaxConcurrentOfflineBatches=1` 时预期有效并发明确为 3。
