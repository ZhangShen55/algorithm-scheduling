# 公共算子实时负载路由验证

本场景对应 OpenSpec `balance-operator-routing-by-live-load`。它记录当前设计、可重复验证入口和
证据边界，不改写既有 `d449dbad` Campaign attempt，也不删除任何 Text Analysis 历史记录。

## 后续范围调整

旧实现按 `instance_id` 排序，选择第一个 `active_lease_count < declared_capacity` 的实例。
`test-260827` 的真实学生行为任务已证明 108 个成功 VBas 批次全部进入 `vbas-gpu0`，GPU1/2
没有该任务批次。此事实继续作为旧首次适配路由基线，只能说明问题已复现，不能作为新实现
通过证据。

从本变更起，`LOAD-007` 和相关均衡断言使用以下口径：

```text
effective_inflight = max(active_lease_count, reported_inflight)
load_ratio = effective_inflight / declared_capacity
```

Redis 在单个 Lua 原子操作中清理过期租约、过滤候选、比较负载率、对同负载候选轮询并创建
租约。实例全部 capability 共享容量；在线与离线调用也共享容量。同负载时允许处理速度差异
导致最终数量不相等，但不允许其他健康实例持续空闲时由固定实例长期独占。

## 固定配置

```toml
# VBas
max_concurrent_requests = 1024
MaxConcurrentBatches = 1024
MaxQueueSize = 0
# max_concurrent_requests 注册为 declared_capacity=1024

# Vision Orchestrator
max_batch_size = 8
max_concurrency = 16
```

Vision 的 `16` 是全部课程共享的服务级 batch 并发；单个 batch 最多 8 图并只占一个租约。
VBas 不保留本地等待队列。Kafka Consumer 按 partition 只提交连续完成 offset，停止时未完成
消息保持可重放。

## 本地自动化门禁

本地门禁覆盖 Redis 原子评分、不同容量归一化、同负载轮询、跨 capability 共享容量、
`reported_inflight` 差异、运维快照、VBas `running_batches`、Vision 服务级并发以及 Kafka
乱序完成/停止行为。命令和实际结果记录在 `../verification.md`。

本地测试只能证明算法、配置和运行时边界，不能替代远端三卡真实推理。

## 远端验证矩阵

| 场景 | 北向输入 | 必需路由证据 | 当前状态 |
|---|---|---|---|
| `VBAS-OFF20` | 20 个唯一 `task_id`，`task_types=["STUDENT_BEHAVIOR"]` | 首批三个租约覆盖三实例；逐 batch 租约与 VBas 日志；最终收敛 | 待新 revision 执行 |
| `IMG-VBAS-1000` | Online Gateway 1000 个唯一单图请求 | 网关实例增量、Control 租约时序、VBas `ImageId` 日志、响应分类 | 待新 revision 执行 |
| `MIXED-VBAS-OFF20-ONLINE1000` | 离线三实例租约形成后释放在线 1000 路 | 按来源和实例统计，共享容量不超卖，两类流量不饥饿 | 待新 revision 执行 |
| `ASR-16` | 16 个唯一 `task_id`，`task_types=["ASR"]` | ASR Offline 三实例租约/调用/终态分布 | 旧路由调查，不修改现场 |
| `PPT-16` | 16 个唯一 `task_id`，`task_types=["PPT"]` | PPT Slice 和后续 OCR 各自三实例租约/调用/终态分布 | 旧路由调查，不修改现场 |

`PPT_OCR` 是 PPT DAG 内部节点，不是合法 A 服务 `task_types`；北向验证必须提交 `PPT`。其他
算子共享公共注册表修复，但旧 revision 的调查结果不能被重标为新 revision 通过。

## 证据要求

每次远端执行至少保留：完整 Git SHA、Compose 展开值、实例注册/心跳、租约申请/取得/释放
时序、实例容器日志增量、业务响应/任务终态和负载停止后的收敛快照。GPU PID、显存和利用率
是补充证据，不可单独证明路由。失败必须保留中文原因，不把部分成功写为全部通过。

A 服务提交/查询和在线接口合同保持不变，不增加实例、租约、batch 或 GPU 字段。远端新版本
全部门禁通过前保留旧容器和镜像；只有账本核对通过后才按完整 ID 精确清理。
