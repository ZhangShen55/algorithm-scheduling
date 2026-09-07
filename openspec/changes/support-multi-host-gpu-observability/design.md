## Context

当前平台已经通过 PLATFORM_INSTANCE_ID 为算子实例提供全局身份，并由算子在注册和心跳时向 Control Service 上报 service_url、capacity 和 labels。现有部署仅在 labels 中使用 gpu 编号，运维控制台也只保存一个 GPU Exporter 地址；单机时 gpu=0 足以关联实例与显卡，多机后不同服务器上的同编号 GPU 会发生碰撞。

本变更面向平台运维人员和部署人员。它必须保持七类算子业务接口、A 服务对接、Kafka 消息、任务租约和数据库结构不变，并兼容当前单服务器部署。多机部署采用固定内网 IPv4 作为 host_id，以显式配置代替从实例名、容器名或 URL 推断物理位置。

目标拓扑由一台平台主机和若干 GPU 算子主机构成。平台主机可运行 Control Service、Orchestrator、Vision Orchestrator、Online Gateway、PostgreSQL、Redis、Kafka，并将本地 /data/course 和 /data/result 作为 NFS 服务端导出。远程算子主机把导出的目录挂载到相同路径；每台 GPU 主机独立运行一个只读取本机 NVML 的 GPU Exporter。已确认第二台测试 GPU 主机为 192.168.29.12，可通过 SSH 用户 root、端口 22 连接；认证凭据只在执行环境中提供，不写入 OpenSpec、Harness 或 Git 跟踪文件。

## Goals / Non-Goals

**Goals:**

- 使用固定内网 IPv4 作为 host_id，并以 host_id + gpu_index 唯一标识集群中的物理 GPU。
- 在实例清单中准确展示算子所在服务器与 GPU，不依赖 PLATFORM_INSTANCE_ID 或容器名的命名格式。
- 让控制台并行聚合多台 GPU 主机的数据，并在单节点故障时保留其他数据源的观测能力。
- 给出跨机 service_url、trusted_service_urls、NFS 共享路径和部署预检的可执行约束。
- 兼容现有单 GPU Exporter 配置，并提供可回滚的渐进迁移路径。
- 使用无算子的第二台 GPU 服务器验证重复 GPU 编号、空主机关联、局部故障和 CORS。

**Non-Goals:**

- 不修改算子 HTTP 或 WebSocket 契约、默认端口和推理逻辑。
- 不修改 A 服务接口、Kafka envelope、PostgreSQL 表结构、Redis Key 或容量租约语义。
- 不通过 GPU Exporter 管理 Docker，也不提供容器启动、停止、重启或调度能力。
- 不实现动态服务发现、自动分配 GPU、自动发现集群主机或跨主机容器网络编排。
- 不要求 PostgreSQL、Redis、Kafka 与平台服务位于同一服务器；其既有网络地址配置继续独立生效。
- 本次不以无算子的测试服务器证明远程算子真实调用链，后续仍需用至少一个远程算子补齐端到端 Harness。

## Decisions

### 1. 主机身份使用显式固定内网 IPv4

host_id MUST 使用部署主机固定、可路由的内网 IPv4，例如第二台测试主机 192.168.29.12。每个算子同时配置全局唯一的 PLATFORM_INSTANCE_ID 和包含 host_id、gpu 的 PLATFORM_INSTANCE_LABELS。由于 PLATFORM_INSTANCE_LABELS 会替换默认 GPU 标签，部署模板必须一次性写入两个字段。

选择 IP 是因为用户已有固定内网地址，运维人员能直接定位主机，并且无需引入主机名解析。备选方案是使用 hostname 或独立 UUID，但 hostname 可能在不同环境重复，UUID 对人工排障不直观。代价是主机换 IP 时必须同步更新 Exporter、算子标签和控制台配置。

PLATFORM_INSTANCE_ID 只表示实例身份，不承载可解析的拓扑语义。控制台不得从 asr-offline-29-21-gpu1 等命名、Docker 容器名或 service_url 反推 host_id 与 GPU。

### 2. 多机调用使用宿主机可达地址

跨机算子的 PLATFORM_SERVICE_URL MUST 使用所有调用方可达的宿主机 IP 和发布端口，例如 http://192.168.29.12:28083，不能使用仅 Docker 单机网络可解析的容器名。Control Service 的 operator_registry.trusted_service_urls MUST 为该 instance_id 配置完全一致的可信 URL。

Orchestrator、Vision Orchestrator 和 Online Gateway 继续使用租约返回的 service_url，不新增发现协议。这样可复用现有注册和调度机制，改动集中在部署配置和网络预检。备选方案是 Consul、Kubernetes Service 或跨主机 overlay network，但当前规模不值得引入额外控制面。

### 3. NFS 保持容器内路径契约不变

平台主机 A 的 /data/course 与 /data/result 为实际目录并由 A 导出；远程主机 B、C 挂载 A 的导出目录到同名宿主机路径，再映射进算子容器。所有生产者和消费者仍读取 /data/course、/data/result，从而避免修改任务载荷中的共享路径。

部署预检 MUST 覆盖 UID/GID、root_squash、创建与删除、fsync、同文件系统原子 rename、跨机可见性和结果目录保护。控制台的存储容量表示共享 NFS 文件系统整体容量，只从指定存储观测端读取一次，不按 GPU 主机重复相加。

备选方案是对象存储或将文件改为 HTTP 传输，均会改变当前共享路径契约和多个算子的数据读取方式，不在本次范围。

### 4. 每台 GPU 主机部署独立 Exporter

GPU Exporter 增加 GPU_EXPORTER_HOST_ID 配置，并在 /gpu 成功响应中返回 host_id。Exporter 只读取本机 NVML，不访问 Docker Socket，也不负责推断算子归属。每台 GPU 主机独立发布可被运维浏览器访问的端口，并按当前工具定位允许配置 CORS。

使用显式 host_id 可保证 Exporter 数据和算子 labels 使用相同主键。备选方案是由 Exporter自动读取主机 IP，但多网卡、容器 NAT 和地址选择会造成不确定性，因此不采用。

### 5. 控制台以节点列表聚合 GPU 数据

控制台配置从单个 gpuBaseUrl 扩展为 GPU 节点列表；每项包含 host_id、name、url。启动时把旧 gpuBaseUrl 迁移为一个节点，保留原用户配置，之后统一按列表读写。

刷新时并行请求每个节点的 /gpu，并为每个请求设置独立超时、错误状态和最近成功数据。一个节点失败只在该主机区域显示异常，不能清空其他 GPU、实例、任务、网关或系统状态数据。配置中的 host_id 与响应 host_id 不一致时，该节点数据标记为身份错误且不参与实例关联，避免错误归属。

控制台按 host_id 分组展示 GPU，提供主机筛选，并在实例清单增加服务器和 GPU 列。实例与 GPU 的唯一关联键为 labels.host_id + labels.gpu，其中 gpu 在比较前规范为非负整数 gpu_index。

单节点兼容期允许缺少 host_id 的旧实例仅按 gpu 关联，并明确显示“兼容映射”。配置两个及以上 GPU 节点后 MUST 禁止该降级映射；缺少 host_id 的实例显示“未标记主机”，不能被挂到任意同编号 GPU。无实例关联的 GPU 主机显示“未部署算子”，不视为错误。

### 6. 变更范围保持在部署、Exporter 和控制台

Control Service 已能保存并返回实例 labels，不需要新增业务接口或数据库字段。七类算子已能通过环境配置上报 PLATFORM_INSTANCE_ID、PLATFORM_SERVICE_URL 和 PLATFORM_INSTANCE_LABELS，原则上不改业务源码。主要改动位于多机部署模板和预检、trusted_service_urls、GPU Exporter、运维控制台、测试与 Harness。

如果实施时发现某个算子未透传 PLATFORM_INSTANCE_LABELS，应只修复该算子的共享注册接入并补兼容测试，不改变其推理 API。

## Risks / Trade-offs

- [主机 IP 变化导致身份断裂] -> 要求使用固定或 DHCP 保留的内网 IP，并在变更 IP 时同步更新算子标签、Exporter 和控制台节点配置。
- [多网卡主机配置了错误 IP] -> 部署预检从平台调用方验证 service_url，从浏览器验证 Exporter URL，并比对配置与响应 host_id。
- [浏览器无法直接访问远程 Exporter] -> 每个 Exporter 明确配置监听地址、宿主机端口、防火墙和 CORS；控制台逐节点显示具体错误。
- [旧实例缺少 host_id 造成错误关联] -> 仅单节点允许显式兼容映射，多节点强制显示未标记，不猜测归属。
- [NFS 权限或缓存语义造成任务失败] -> 上线前执行跨机读写、fsync、rename、可见性和删除验证，并保持两个共享目录的容器内绝对路径一致。
- [共享 NFS 容量被重复统计] -> 存储指标继续由一个指定观测端采集，GPU 节点只贡献 GPU 指标。
- [并行刷新增加浏览器请求量] -> 各节点复用统一刷新周期、独立超时，防止上一轮未完成时重复并发，并保留最近成功快照。
- [测试主机没有算子导致验证不完整] -> 本变更先验证多源聚合和空主机语义；上线前另加至少一个真实远程算子的注册、租约、调用和结果回写 Harness。

## Migration Plan

1. 使用已确认的第二台 GPU 主机 192.168.29.12，通过 root@192.168.29.12:22 执行部署；凭据通过执行环境或本机不入库配置提供，并确认 Exporter 端口和浏览器网络可达。
2. 升级 GPU Exporter，使 /gpu 返回 host_id；先在第二台无算子主机部署，再升级现有主机。
3. 升级控制台并迁移旧 gpuBaseUrl，验证单节点旧标签兼容行为。
4. 在所有现有算子部署中同时配置 PLATFORM_INSTANCE_ID、PLATFORM_SERVICE_URL 和含 host_id、gpu 的 PLATFORM_INSTANCE_LABELS，更新 trusted_service_urls。
5. 将 192.168.29.12 配置为第二个 GPU 节点，验证两台主机都存在 GPU 0 时不冲突、空主机显示、筛选、单节点断开与恢复；不得停止、覆盖或改变该主机既有媒体源服务及其端口。
6. 为远程算子主机挂载 NFS 并运行文件系统预检；部署至少一个真实远程算子，补注册、调用、结果和 GPU 关联 Harness。
7. 完成验证后移除单节点旧标签兼容依赖，但保留旧 gpuBaseUrl 到节点列表的数据迁移。

回滚时先把控制台恢复为单节点配置，再回滚控制台和 Exporter 镜像。算子新增 labels 对旧 Control Service 无破坏性，可保留；若远程算子回迁平台主机，必须恢复可达 service_url 和 trusted_service_urls。NFS 数据目录不得在回滚中删除。

## Open Questions

- 192.168.29.12 的 GPU Exporter 发布端口和浏览器可达策略仍需实施前确认；默认优先评估 9400，且不得与该主机既有媒体源端口 5555、5556 冲突。
- 首个真实迁移到远程主机的算子类型和宿主机发布端口需要在端到端 Harness 前确定。
- 主机显示名由浏览器本地配置维护，还是未来由 Control Service 提供集中式运维配置，可在完成本地列表方案后再评估。
