## Why

当前调度平台使用全局 `instance_id` 区分算子实例，但实例只上报 `gpu` 标签，运维控制台也只能读取一个 GPU Exporter。算子分散到多台 GPU 服务器后，不同主机的 `GPU 0`、`GPU 1` 会冲突，页面无法可靠回答“哪个算子位于哪台服务器的哪张卡”。

需要在不改变 A 服务、任务调度和算子推理契约的前提下，以服务器固定内网 IP 作为 `host_id`，建立多主机实例身份、文件共享、跨机调用和 GPU 资源观测的明确契约。

## What Changes

- 定义多主机算子注册规范：`PLATFORM_INSTANCE_ID` 在全平台唯一，`labels.host_id` 使用服务器固定内网 IP，`labels.gpu` 表示该主机的物理 GPU 编号。
- 将多机算子的 `PLATFORM_SERVICE_URL` 配置为 Orchestrator、Vision Orchestrator 和 Online Gateway 均可访问的宿主机 IP 与发布端口，并同步 Control Service `trusted_service_urls`。
- 定义最小 NFS 部署合同：平台主机可作为 NFS 服务端，远程算子主机挂载其导出的课程与结果目录，所有相关容器内继续使用 `/data/course` 和 `/data/result`。
- 每台 GPU 服务器部署一个 GPU Exporter，由配置注入与算子标签一致的 `host_id`，`/gpu` 响应返回主机身份和该主机的显卡快照。
- 将运维控制台的单个 GPU Exporter 地址扩展为带 `host_id`、显示名和 URL 的节点列表，并向后兼容已保存的单地址配置。
- 前端并行读取多个 `/gpu`，使用 `host_id + gpu_index` 作为唯一键，按服务器展示、筛选算子实例和 GPU，并显示每张卡上关联的算子。
- 对单个 GPU 节点实施独立超时、错误和恢复处理；任一节点不可用不得清空其他节点、Control Service、Gateway 或任务数据。
- 增加重复 GPU 编号、无算子 GPU 主机、标签缺失、单节点失败、旧配置迁移和真实两主机的验证与 Harness。
- 保持 A 服务 HTTP 契约、算子 HTTP/WebSocket 路径与字段、Kafka envelope、容量租约语义和数据库结构不变。

## Capabilities

### New Capabilities

- `multi-host-operator-deployment`: 定义以固定内网 IP 作为 `host_id` 的算子实例身份、跨机可达 `service_url`、可信地址与 NFS 共享路径要求。
- `multi-host-gpu-observability`: 定义每主机 GPU Exporter 身份、多数据源配置、资源聚合、实例与 GPU 关联、局部降级和控制台多主机展示要求。

### Modified Capabilities

无。当前主规范中没有已归档的多主机算子部署或 GPU 观测能力；本变更与未归档的 `standardize-ops-console-deployment-and-observability` 协同，不虚构对主规范的修改。

## Impact

- `algorithm-scheduling-platform/deploy/`：增加平台主机与 GPU 节点的多机部署模板、可达端口、实例标签、可信 URL、NFS 挂载和预检验证。
- `gpu_metrics_exporter/`：增加 `host_id` 配置、响应字段、健康诊断和相关测试；仍只读取本机 NVML，不获取 Docker 控制权限。
- `algorithm-scheduling-ops-console/`：扩展 TypeScript 配置和 GPU 数据模型，增加多主机请求、局部错误、分组、筛选、关联与向后兼容迁移。
- Control Service 继续复用现有注册 `labels` 和 `/ops/operator-instances` 响应，原则上不增加业务接口；需要更新部署端的 `trusted_service_urls`。
- Orchestrator、Vision Orchestrator 和 Online Gateway 继续调用租约返回的 `service_url`，不改变业务契约，但需纳入跨机网络可达性验收。
- 无 PostgreSQL 迁移，无 Redis Key 结构变更，无 Kafka Topic 或消息变更，无 A 服务对接改动。
