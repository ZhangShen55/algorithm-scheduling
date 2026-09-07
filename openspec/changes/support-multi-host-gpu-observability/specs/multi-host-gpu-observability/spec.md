## ADDED Requirements

### Requirement: 每台 GPU 主机暴露显式主机身份
每台需要被观测的 GPU 服务器 SHALL 独立运行一个 GPU Exporter。Exporter SHALL 从 GPU_EXPORTER_HOST_ID 读取该服务器固定内网 IPv4，并在 /gpu 成功响应中返回 host_id 与本机 GPU 快照。Exporter MUST 只读取本机 NVML 数据，不通过 Docker Socket 控制或推断容器。

#### Scenario: 返回本机 GPU 快照
- **WHEN** 192.168.29.12 上的 Exporter 配置 GPU_EXPORTER_HOST_ID=192.168.29.12 且 NVML 可用
- **THEN** /gpu 响应包含 host_id=192.168.29.12 以及该主机每张 GPU 的 index、利用率、显存、温度和现有指标

#### Scenario: 无算子的 GPU 主机
- **WHEN** GPU 主机运行 Exporter 但没有部署任何算法算子
- **THEN** Exporter 仍正常返回本机 GPU 快照，且不把无算子状态报告为采集错误

### Requirement: 控制台可配置多个 GPU 数据源
运维控制台 SHALL 提供 GPU 节点列表配置，每个节点包含 host_id、显示名称和 Exporter URL，并允许配置统一的 GPU 刷新周期。host_id MUST 是固定内网 IPv4，URL MUST 包含协议、IP 和端口。刷新周期的最小值 SHALL 保持为 1 秒。

#### Scenario: 配置两个 GPU 节点
- **WHEN** 运维人员保存 192.168.29.11:9400 和 192.168.29.12:9400 两个 Exporter
- **THEN** 控制台持久化两个独立节点的 host_id、显示名称和 URL，并在后续刷新中读取两者

#### Scenario: 拒绝重复主机身份
- **WHEN** 节点列表中存在相同 host_id 的两个配置项
- **THEN** 控制台阻止保存并提示重复的服务器 IP

#### Scenario: 配置刷新周期
- **WHEN** 运维人员将 GPU 刷新周期设置为 1 秒或更高的有效值
- **THEN** 控制台按该周期刷新所有已启用 GPU 节点且不因状态提示增删导致页面跳动

### Requirement: 旧单地址配置可迁移
控制台 SHALL 在发现既有 gpuBaseUrl 且尚无 GPU 节点列表时，将该地址迁移为单节点配置并保留用户原地址。迁移 SHALL 可重复执行而不产生重复节点，也不得覆盖已经存在的多节点配置。

#### Scenario: 首次读取旧配置
- **WHEN** 浏览器存储仅包含 gpuBaseUrl=http://192.168.29.11:9400
- **THEN** 控制台生成一个使用该 URL 的 GPU 节点并继续请求原数据源

#### Scenario: 已存在节点列表
- **WHEN** 浏览器存储同时存在旧 gpuBaseUrl 和有效 GPU 节点列表
- **THEN** 控制台使用节点列表且不追加重复节点

### Requirement: 多节点 GPU 读取局部降级
控制台 SHALL 并行读取所有已启用 GPU 节点，并为每个节点维护独立的超时、成功时间、错误状态和最近成功快照。单个节点超时或失败 MUST NOT 清空其他 GPU 节点、算子实例、课程任务、网关或系统状态的数据。刷新任务 MUST 避免同一节点的上一轮请求未完成时继续叠加请求。

#### Scenario: 一个节点读取失败
- **WHEN** 两个 GPU 节点中一个超时而另一个成功
- **THEN** 控制台展示成功节点的最新数据，并仅在失败节点上显示错误和最近成功时间

#### Scenario: 失败节点恢复
- **WHEN** 之前失败的 GPU Exporter 再次成功响应
- **THEN** 控制台自动恢复该主机的实时数据并清除其局部错误状态

#### Scenario: 请求跨越刷新周期
- **WHEN** 某节点的请求在下一个刷新时刻仍未结束
- **THEN** 控制台不为该节点启动重叠请求，并继续保留其最近成功快照

### Requirement: 配置身份与响应身份必须一致
控制台 SHALL 校验节点配置 host_id 与 /gpu 响应 host_id。两者不一致或响应缺少 host_id 时，该节点 MUST 标记为身份错误且不得参与实例与 GPU 关联。其他身份正常的节点继续展示。

#### Scenario: Exporter 返回错误主机身份
- **WHEN** 配置节点 host_id 为 192.168.29.12 但响应 host_id 为 192.168.29.22
- **THEN** 控制台显示预期值和实际值，且不把该响应中的 GPU 与任何实例关联

#### Scenario: 多节点响应缺少主机身份
- **WHEN** 已配置多个 GPU 节点且其中一个 /gpu 响应没有 host_id
- **THEN** 该节点标记为身份错误，其他节点不受影响

### Requirement: 使用主机与 GPU 组合键关联实例
控制台 SHALL 使用算子 labels.host_id 与规范化后的 labels.gpu 组成 host_id+gpu_index 唯一键，并以该键关联 GPU 快照。控制台 MUST NOT 解析 instance_id、Docker 容器名或 service_url 来推断归属。不能可靠关联的实例 SHALL 显示明确原因。

#### Scenario: 精确关联算子与显卡
- **WHEN** 实例 labels 为 host_id=192.168.29.12、gpu=1 且对应主机返回 GPU index 1
- **THEN** 控制台在该 GPU 下列出此实例，并在实例清单中显示服务器 192.168.29.12 和 GPU 1

#### Scenario: 多主机 GPU 0 不冲突
- **WHEN** 两台主机均返回 GPU index 0 且各有不同算子实例
- **THEN** 每个实例只关联到其 labels.host_id 对应主机的 GPU 0

#### Scenario: 多节点实例缺少主机标签
- **WHEN** 配置了两个或以上 GPU 节点且实例只有 gpu 标签
- **THEN** 控制台显示“未标记主机”并且不把实例关联到任意 GPU

#### Scenario: GPU 标签无效
- **WHEN** 实例 labels.gpu 缺失、不是整数或小于零
- **THEN** 控制台显示“未标记 GPU”或“GPU 标签无效”，且不参与 GPU 关联

### Requirement: 单节点部署提供受限兼容映射
仅当控制台恰好配置一个 GPU 节点时，系统 MAY 将缺少 labels.host_id 但具有有效 labels.gpu 的旧实例关联到该唯一主机，并 SHALL 标记为“兼容映射”。配置两个或以上节点时 MUST 禁止该行为。

#### Scenario: 单节点旧实例兼容
- **WHEN** 仅配置一个 GPU 节点且旧实例只有 gpu=0
- **THEN** 控制台可将其关联到唯一主机的 GPU 0并显示“兼容映射”标识

#### Scenario: 增加第二个节点后停止兼容
- **WHEN** 运维人员在已有单节点配置中增加第二个 GPU 节点
- **THEN** 所有缺少 host_id 的实例停止按 GPU 编号关联并显示“未标记主机”

### Requirement: 控制台按主机展示和筛选资源
控制台 SHALL 在 GPU 资源区域按 host_id 分组展示主机与 GPU，并提供主机筛选。实例清单 SHALL 增加服务器和 GPU 字段，允许按主机和算子筛选；每张 GPU SHALL 展示关联算子实例，未关联任何实例的主机或 GPU SHALL 显示“未部署算子”而不是错误。

#### Scenario: 查看指定服务器
- **WHEN** 运维人员选择主机 192.168.29.12
- **THEN** GPU 区域和实例清单只显示该主机的 GPU 与实例，任务和其他观测模块不被清空

#### Scenario: 查看空算子主机
- **WHEN** 192.168.29.12 的 GPU 数据正常但没有实例以该 host_id 注册
- **THEN** 控制台展示该主机的 GPU 利用率并标记“未部署算子”

### Requirement: 存储容量不得按 GPU 主机重复聚合
课程临时目录与结果持久化目录的容量 SHALL 表示共享文件系统的整体容量，并由指定的存储观测端读取一次。控制台 MUST NOT 因配置多个 GPU Exporter 而对相同 NFS 的容量求和或重复显示为集群总容量。

#### Scenario: 多台主机挂载同一 NFS
- **WHEN** 平台主机和两台 GPU 主机均挂载相同的 /data/course 与 /data/result
- **THEN** 控制台只展示一次共享文件系统容量，GPU Exporter 节点数量不改变该容量值

### Requirement: 多主机观测具备测试与 Harness 证据
系统 SHALL 测试旧配置迁移、重复 GPU 编号、身份不一致、缺少标签、无算子主机、单节点超时、恢复和真实两主机数据。浏览器级 Harness SHALL 证明 CORS、刷新、局部降级、主机筛选和实例关联行为。

#### Scenario: 两台真实 GPU 主机观测
- **WHEN** Harness 同时连接两台各自运行 Exporter 的真实 GPU 服务器
- **THEN** 证据包含不同 host_id、各自主机 GPU 快照、同编号 GPU 不冲突和无算子主机的正确状态

#### Scenario: 局部故障浏览器验证
- **WHEN** Harness 暂停或阻断其中一个 Exporter 后继续刷新页面
- **THEN** 页面保持可操作且另一节点、实例、任务与网关数据继续更新
