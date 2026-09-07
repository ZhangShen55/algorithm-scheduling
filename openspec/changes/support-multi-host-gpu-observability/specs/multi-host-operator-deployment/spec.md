## ADDED Requirements

### Requirement: 算子实例显式上报主机与 GPU 身份
平台 SHALL 要求每个参与多机部署的算子实例使用全平台唯一的 PLATFORM_INSTANCE_ID，并在注册 labels 中同时上报 host_id 与 gpu。host_id MUST 是该部署主机固定、可路由的内网 IPv4；gpu MUST 是该主机上的非负物理 GPU 编号。PLATFORM_INSTANCE_ID、Docker 容器名和 service_url 均不得作为解析主机或 GPU 身份的隐式协议。

#### Scenario: 算子注册完整拓扑标签
- **WHEN** 位于 192.168.29.12 的算子实例使用 GPU 1 向 Control Service 注册
- **THEN** 注册信息包含全局唯一 instance_id、labels.host_id=192.168.29.12 和 labels.gpu=1

#### Scenario: 两台主机使用相同 GPU 编号
- **WHEN** 192.168.29.11 与 192.168.29.12 均有实例使用 GPU 0
- **THEN** 平台分别以 192.168.29.11+0 和 192.168.29.12+0 标识两张不同的物理 GPU

#### Scenario: 自定义标签覆盖默认标签
- **WHEN** 部署配置设置 PLATFORM_INSTANCE_LABELS
- **THEN** 该配置同时包含 host_id 和 gpu，不因替换默认标签而丢失任一字段

### Requirement: 跨机服务地址对所有调用方可达
多机算子的 PLATFORM_SERVICE_URL SHALL 使用 Orchestrator、Vision Orchestrator 和 Online Gateway 中实际调用该算子的服务均可访问的宿主机地址与发布端口。Control Service 的 trusted_service_urls MUST 为 instance_id 配置与注册值一致的 URL。系统 MUST NOT 使用仅在单个 Docker 网络内可解析的容器名作为跨机 service_url。

#### Scenario: 注册远程算子地址
- **WHEN** 算子容器在 192.168.29.12 将内部端口 8083 发布为宿主机端口 28083
- **THEN** PLATFORM_SERVICE_URL 与 trusted_service_urls 均配置为 http://192.168.29.12:28083

#### Scenario: 拒绝不可信注册地址
- **WHEN** 算子上报的 service_url 与其 instance_id 对应的 trusted_service_urls 不一致
- **THEN** Control Service 按现有可信地址校验拒绝该不一致注册

#### Scenario: 调度服务调用远程实例
- **WHEN** 调度租约选择了一台远程算子实例
- **THEN** 对应调用服务直接使用租约返回的跨机可达 service_url，且请求与响应契约保持不变

### Requirement: 多机共享课程与结果路径
平台主机 SHALL 将实际的 /data/course 与 /data/result 目录作为共享文件系统源，远程算子主机 SHALL 将同一共享内容挂载到相同宿主机路径并映射到容器内同名路径。任务载荷和算子代码继续使用 /data/course 与 /data/result，MUST NOT 因多机部署引入主机专属路径。

#### Scenario: 远程算子读取课程文件
- **WHEN** 平台服务在 /data/course 写入任务输入且远程主机已挂载 NFS
- **THEN** 远程算子在容器内通过相同绝对路径读取到该输入

#### Scenario: 远程算子写入结果
- **WHEN** 远程算子在 /data/result 写入并完成结果文件
- **THEN** 平台主机和其他已挂载主机可通过相同绝对路径读取该结果

#### Scenario: 结果目录受到清理保护
- **WHEN** 执行课程临时文件的常规清理
- **THEN** 清理流程不得删除 /data/result 中的持久化结果

### Requirement: NFS 上线前执行文件语义预检
多机部署流程 SHALL 在承载生产任务前验证共享目录的身份权限、创建、写入、fsync、同文件系统原子 rename、跨机可见性和删除行为，并 SHALL 验证 /data/result 的保护规则。任何关键检查失败 MUST 阻止该远程算子节点进入可用部署状态。

#### Scenario: NFS 文件语义全部通过
- **WHEN** 部署人员在平台主机与远程主机间执行共享目录预检
- **THEN** 报告分别给出 UID/GID、root_squash、读写、fsync、rename、跨机可见和删除检查结果

#### Scenario: 权限不一致
- **WHEN** 远程容器用户不能在共享目录创建或完成原子 rename
- **THEN** 预检失败并明确报告目录、操作和主机，远程算子不得标记为可上线

### Requirement: 多机部署不改变既有业务契约
多机部署 SHALL 复用 Control Service 已有的实例 labels、注册和租约能力，保持算子 HTTP/WebSocket 路径与字段、A 服务对接、Kafka envelope、PostgreSQL 数据结构、Redis Key 和容量租约语义不变。

#### Scenario: A 服务继续提交任务
- **WHEN** 算子实例从平台主机迁移到远程 GPU 主机
- **THEN** A 服务仍使用原接口和字段提交与查询任务，无需感知 host_id 或远程地址

#### Scenario: Control Service 返回拓扑标签
- **WHEN** 运维控制台读取已有算子实例运维接口
- **THEN** 响应通过现有 labels 字段提供 host_id 和 gpu，无需新增数据库字段或业务接口

### Requirement: 多机部署具备端到端验证证据
平台 SHALL 提供多机配置和 Harness，至少验证重复 GPU 编号、共享路径、远程注册地址、调用方网络可达性和 trusted_service_urls 一致性。无算子的第二台 GPU 主机可用于资源观测验证，但 MUST NOT 被视为远程算子调用链的完整证据。

#### Scenario: 无算子测试主机验证范围
- **WHEN** 第二台 GPU 主机只运行 GPU Exporter 而没有部署算子
- **THEN** Harness 将其证据限定为多主机 GPU 身份、重复编号、空主机、CORS 和局部故障

#### Scenario: 真实远程算子验收
- **WHEN** 至少一个算子部署到远程 GPU 主机
- **THEN** Harness 验证注册、租约选择、跨机调用、共享输入读取、结果回写和 host_id+gpu_index 关联
