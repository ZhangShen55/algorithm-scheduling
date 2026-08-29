## Context

当前在线网关只有一个 `/api/online/vbas/analyze` 入口，通过 `stream_type` 在教师和学生行为之间分流；VBas 另有兼容接口 `/AE/SyncTasks2`，接收包含 `AnalysisRule.AlgParams.PolygonList` 的 Base64 批量请求并返回人数、人脸及区域结果。现有租约申请失败会立即返回，VBas 只维护一个通用 batch 准入计数，无法表达在线请求和离线 batch 的不同容量。

本变更按三个在线能力拆分路由，并让同一 VBas 实例分别维护离线运行容量、在线运行容量和在线等待队列。在线和离线调用方都在租约暂不可用时等待，Redis 的容量释放通知用于快速唤醒，定时重试作为可靠兜底。

## Goals / Non-Goals

**Goals:**

- 在一个 `online-gateway-service` 中提供 `/online/vbas/teacher`、`/online/vbas/student` 和 `/online/vbas/person-count`。
- 三个网关接口分别透传对应 VBas 接口的请求和响应；人数接口完整支持多边形坐标裁剪。
- VBas 使用 `MaxConcurrentOfflineBatches`、`MaxConcurrentOnlineRequests` 和 `MaxQueueOnlineSize` 三个参数。
- 在线租约和离线租约使用独立容量池，并按各自池子的实时负载路由。
- 租约不足时，在线网关和视觉编排器最多等待 300 秒；租约释放通知到达时立即重新申请。
- 释放通知丢失时仍能通过 `acquire_retry_interval_seconds=0.2` 的兜底重试取得容量。

**Non-Goals:**

- 不在本变更中修改 VBas `/AE/SyncTasks2`、`/ImageDetect/student/v1.0.0` 或 `/ImageDetect/teacher/v1.0.0` 的请求和响应字段。
- 不增加 RTSP 接入或视频抽帧逻辑到在线网关或 VBas。
- 不把 `total_capacity` 作为人工配置项；本变更只记录各容量池的运行指标。
- 不保证系统故障时请求无限等待；达到 300 秒必须结束等待并返回超时结果。

## Decisions

### 1. 三个路由归属于一个网关服务

三个接口是路由边界，不是三个独立容器。这样可以共享 HTTP 连接池、租约客户端、日志和指标，同时让 A 服务明确选择教师、学生或人数能力。

成功响应直接使用对应 VBas 的响应模型，不再使用旧的统一 `BusinessResponse` 包装。网关发生容量等待超时或下游不可用时，使用对应算子响应模型表达错误，避免调用方需要同时解析两种成功结构。

### 2. 人数接口原样透传 `SyncTasks2`

`/online/vbas/person-count` 接受与 `/AE/SyncTasks2` 相同的 `TaskInfo`，包含 `ImageList`、`AnalysisRule` 和 `PolygonList`。网关只做图片大小、基本字段和租约校验，然后把请求原样转发；`TaskResult`、`PersonInfo`、`FaceInfo`、`FreeCapacity` 均保留。

### 3. 在线队列只在 VBas 内存在，离线等待由编排器负责

VBas 实例内部只允许在线请求排队：

```text
online_running <= MaxConcurrentOnlineRequests
online_waiting <= MaxQueueOnlineSize
offline_running <= MaxConcurrentOfflineBatches
```

离线没有本地队列，`vision-orchestrator-service` 在租约不足时等待。在线租约代表一个“已接纳的在线请求”，平台路由需要把运行中和排队中的在线请求都计入实例的可接纳量，避免 VBas 队列尚未满而平台提前拒绝。

### 4. 租约等待使用通知加轮询

Control Service 释放租约的 Redis Lua 脚本在原子删除租约后发布容量释放事件。等待方订阅对应能力和容量池的事件，收到事件后立即重新申请租约；Pub/Sub 丢失、订阅竞态或 Redis 重启时，按 `acquire_retry_interval_seconds` 作为基础间隔进行退避轮询。

等待方必须在订阅后先申请一次，避免错过已发布事件；所有申请仍由 Redis 原子脚本裁决，通知只用于唤醒，不能直接视为有容量。

### 5. 配置按实例生效

`MaxConcurrentOnlineRequests`、`MaxQueueOnlineSize` 和 `MaxConcurrentOfflineBatches` 都是单个 VBas 实例的配置。三实例部署时，集群可接纳量是各实例实际容量之和，路由器仍按实例实时负载选择，而不是按实例列表顺序固定选择。

## Risks / Trade-offs

- [在线请求体在队列中占用内存] → `MaxQueueOnlineSize` 保持有限，并监控队列长度、请求体大小和等待时长。
- [平台租约与 VBas 队列计数不一致] → 注册协议明确“已接纳量”包含运行中和排队中请求，租约释放必须在请求最终完成后执行。
- [Redis Pub/Sub 通知丢失] → 保留 0.2 秒基础间隔的退避轮询和 300 秒总超时。
- [旧在线路径调用方未迁移] → `/api/online/vbas/analyze` 作为 breaking change 明确下线，并在 A 服务对接文档中提供新路径映射。
- [高并发等待请求造成控制面压力] → 使用事件唤醒、指数退避和随机抖动，避免所有等待请求固定频率轮询。

## Migration Plan

1. 先发布共享契约和 Redis 租约变更，允许注册在线/离线容量池。
2. 发布 VBas，加载新配置，验证教师、学生和人数接口的响应兼容性。
3. 发布 `control_service`，启用按容量池申请、释放和容量释放通知。
4. 发布 `online-gateway-service`，切换到三个新路径；A 服务同步切换调用地址。
5. 发布 `vision-orchestrator-service`，启用离线租约等待。
6. 完成单实例、三实例、在线 512 并发和在线/离线混合等待测试后，删除旧配置字段和旧网关路径。

回滚时可以先恢复调用方到旧网关版本，但新配置已经写入的容量池字段必须由兼容代码忽略；不删除 Redis 中的租约审计记录。

## Open Questions

- A 服务是否直接使用三个网关路径，还是仍由 A 的上层接口再封装一次。
- 在线队列满载时是否允许网关继续等待其他实例，还是在所有实例都满载后才进入 300 秒等待。
