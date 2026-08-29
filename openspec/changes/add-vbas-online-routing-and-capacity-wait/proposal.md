## Why

当前在线 VBas 入口仍使用统一的 `/api/online/vbas/analyze`，无法直接表达教师行为、学生行为和纯人数检测三类能力；同时 VBas 只有一个通用并发参数，容量不足时会立即拒绝请求，不能满足在线请求等待租约释放后继续处理的要求。

本变更把三个在线能力拆成稳定的网关路由，按在线和离线工作类型分别管理 VBas 容量，并让在线、离线调用方在容量暂不可用时等待最多 300 秒。容量释放通过 Redis 通知快速唤醒，保留轮询作为通知丢失时的兜底。

## What Changes

- **BREAKING** 将 `/api/online/vbas/analyze` 拆分为 `/online/vbas/teacher` 和 `/online/vbas/student`，移除 `/api` 前缀。
- 新增 `/online/vbas/person-count`，内部调用 VBas `/AE/SyncTasks2`。
- 三个在线接口分别透传对应 VBas 的请求和响应结构；人数接口支持 `AnalysisRule.AlgParams.PolygonList` 坐标区域。
- VBas 配置改为 `MaxConcurrentOfflineBatches`、`MaxConcurrentOnlineRequests` 和 `MaxQueueOnlineSize`。
- 移除 `MaxConcurrentBatches`、`MaxQueueSize` 和 `MaxQueueOfflineSize`。
- 注册、租约和路由增加在线/离线容量池，在线容量按 HTTP 请求计数，离线容量按 batch 计数。
- `online-gateway-service` 和 `vision-orchestrator-service` 在租约不足时按 0.2 秒基础间隔重试，单次等待上限为 300 秒。
- 租约释放时发布 Redis 容量释放通知，等待请求收到通知后立即重新申请；通知丢失时使用退避轮询兜底。
- 增加三类在线接口、容量隔离、等待、队列上限和多实例分配测试。

## Capabilities

### New Capabilities

- `vbas-online-gateway-routes`: 三个在线 VBas 网关路由及原始请求/响应透传。
- `vbas-online-offline-capacity`: VBas 在线、离线容量池和在线内部队列配置。
- `capacity-lease-wait-notification`: 在线和离线租约等待、Redis 释放通知及轮询兜底。

### Modified Capabilities

无。当前主规范中没有可直接修改的对应能力规范，本变更新增独立能力规范。

## Impact

- `online_gateway_service` 路由、适配器、请求校验、配置和测试。
- `vbas` 配置加载、请求准入控制、运行状态、注册心跳和接口测试。
- `vision_orchestrator_service` 离线租约申请等待逻辑和配置。
- `control_service` 注册容量、租约申请/释放接口和 Redis 注册中心。
- `algorithm-scheduling-platform/packages/platform_common` 的租约模型、Redis Lua 脚本和共享契约。
- 在线旧路径 `/api/online/vbas/analyze` 属于显式 breaking change；A 服务需要切换到三个新路径。
