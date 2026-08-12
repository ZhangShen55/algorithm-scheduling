## ADDED Requirements

### Requirement: 四个平台服务可一起部署
仓库 SHALL 为 `control-service`、`orchestrator-service`、`vision-orchestrator-service` 和 `online-gateway-service` 提供经过验证的单机部署定义，包括重启策略、就绪检查、共享挂载、网络配置和依赖设置。

#### Scenario: 启动平台服务栈
- **WHEN** 基础设施可用，运维人员启动文档规定的 Compose 服务栈
- **THEN** 四个平台服务全部就绪，并能通过文档规定的地址访问 PostgreSQL、Redis、Kafka、共享存储和彼此

### Requirement: Kafka 支持主机和容器连接
Kafka 部署 SHALL 分别为主机运行的开发环境和 Docker 网络内的服务访问提供正确、独立的 advertised listener。

#### Scenario: 平台服务运行在 Docker 中
- **WHEN** orchestrator 从 Docker 网络连接 Kafka
- **THEN** 它使用 Kafka 服务名 listener，而不是 advertised 的 `127.0.0.1` 地址

### Requirement: 算子镜像包含注册客户端
每个可路由的算子镜像 SHALL 安装带版本的 `algorithm-scheduling-platform` wheel，其中包含 `packages.operator_registry_client`；SHALL NOT 依赖运行时源码挂载或临时 `PYTHONPATH`。

#### Scenario: 构建算子镜像
- **WHEN** 镜像构建完成
- **THEN** 隔离容器能够导入注册客户端、启动算子并提供业务和运维路由

### Requirement: 模型资产发布具有完整性和密钥边界
部署流水线 SHALL 仅从 Git worktree 外的受控资产源发布六个实际明文模型根，并使用精确全量清单
校验普通文件的相对路径、字节数和 SHA-256。六根切换 SHALL 使用互斥锁、持久 journal、fsync 和
同文件系统原子重命名，任一失败或重启 SHALL NOT 留下可继续构建的新旧混合模型。密钥、加密目录、
人脸原图、本地运行配置和 Harness 大文件 SHALL NOT 进入当前镜像或模型清单。

#### Scenario: 模型发布在第二根切换时中断
- **WHEN** 进程在 backup 重命名后、目标替换后或 journal 写入后中断并重新执行
- **THEN** 发布器先恢复未提交事务，再完整发布六根；构建入口只有在工作区与外部清单完全一致后才允许执行 Docker build

#### Scenario: 当前明文模式构建算子镜像
- **WHEN** 构建 ScreenDet 与 VBas 的里程碑 2B 镜像
- **THEN** ScreenDet 和 VBas 只包含各自明文模型根，不包含 `models-encrypted` 或密钥，运行配置由 Compose 只读挂载

### Requirement: 注册事实可持久保存
Control-service SHALL 将注册、生命周期变化、心跳摘要和注销事件持久化到 PostgreSQL；Redis 继续作为当前 TTL 和原子租约的权威来源。

#### Scenario: 重建 Redis
- **WHEN** Redis 状态丢失且算子重新注册
- **THEN** 当前路由状态得到重建，此前的注册和生命周期事实仍可从 PostgreSQL 查询

### Requirement: 调度表和字段具有中文数据库说明
平台 SHALL 通过前向迁移为 10 张正式调度表和每个物理字段写入 PostgreSQL 中文注释。新增字段 SHALL 在新的迁移中同步增加注释，且数据库审计 SHALL NOT 自动删除或修改现有表和数据。

#### Scenario: 运维人员查看数据库结构
- **WHEN** 在已执行全部迁移的目标业务库查询 PostgreSQL catalog
- **THEN** 每张正式调度表及其每个字段都返回非空中文说明
