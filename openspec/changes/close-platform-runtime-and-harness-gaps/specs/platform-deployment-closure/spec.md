> **后续范围调整已废止（2026-08-21）**
>
> 本规格中的八算子/Text Analysis 部署要求保留为历史范围。当前部署基线为七算子、21 实例，
> 由 `retire-text-analysis-from-scheduling-platform` 覆盖。

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

#### Scenario: Redis 持久化恢复旧容量租约
- **WHEN** Redis 通过 AOF 恢复了前一 Redis 进程签发且 TTL 尚未到期的容量租约
- **THEN** 平台按 Redis `run_id` 将旧世代租约视为不存在并原子清理，旧租约不阻塞新容量申请，同时保留实例注册和 PostgreSQL 审计事实

#### Scenario: 受控部署恢复持久生命周期
- **WHEN** 权威 Compose 中的实例已经注册，但 PostgreSQL 仍保存此前维护产生的 `DRAINING` 或 `OFFLINE`
- **THEN** 部署 Harness 在成功发布本轮容器账本后按 profile 或显式实例调用鉴权生命周期接口恢复 `ONLINE`，再验证首次就绪心跳；重新注册本身不覆盖运维意图

### Requirement: 调度表和字段具有中文数据库说明
平台 SHALL 通过前向迁移为 10 张正式调度表和每个物理字段写入 PostgreSQL 中文注释。新增字段 SHALL 在新的迁移中同步增加注释，且数据库审计 SHALL NOT 自动删除或修改现有表和数据。

#### Scenario: 运维人员查看数据库结构
- **WHEN** 在已执行全部迁移的目标业务库查询 PostgreSQL catalog
- **THEN** 每张正式调度表及其每个字段都返回非空中文说明

### Requirement: 跨 SHA 容器维护保持可恢复和不可变
部署场景 SHALL 在同一 release tag 内串行化容器维护，并区分 active 前驱与已完成 restore 的前驱。
active 前驱 SHALL 只读继承原维护 authority；已恢复前驱只有在其 snapshot、唯一不可写 audit、
archive metadata 清理和容器恢复事实均严格通过校验后，才允许当前新 SHA 开启新的维护事务。
旧 release SHALL NOT 被修改、复制 paused ledger 或重新绑定 authority。

#### Scenario: 前一 SHA 已经成功恢复原业务容器
- **WHEN** 当前新 SHA 指定该不可变 release 为 `PREVIOUS_RELEASE_ROOT`
- **THEN** resolver 验证终态归档与容器当前事实，授权权威 Compose 已占用端点，在当前 release 新建 snapshot/paused，并继续继承前驱算子 baseline/new 所有权

#### Scenario: 前一 SHA 的归档或恢复事实不可信
- **WHEN** audit 可写、为链接、数量不唯一、包含 active 状态、残留 archive metadata，或容器身份/状态与 snapshot 不一致
- **THEN** 部署在 snapshot/pause、Compose 变更和算子启动前 fail closed，旧 release 与现有容器均不被修改

#### Scenario: 新事务在 snapshot 或 pause 后中断
- **WHEN** 当前新 SHA 已为立即前驱发布不可变 predecessor marker，并在 snapshot 完成后或 pause 完成后中断
- **THEN** 同一 SHA 携带相同 `PREVIOUS_RELEASE_ROOT` 续跑时分别只继续 pause 或直接复用本地 active 账本，不重做 snapshot、不重复暂停，也不修改前驱 release

#### Scenario: predecessor marker 不可信
- **WHEN** 当前 release 已出现本地 snapshot/paused，但 predecessor marker 缺失、可写、为 symlink、具有额外硬链接，或其 root/SHA 与 `PREVIOUS_RELEASE_ROOT` 不一致
- **THEN** resolver 在任何进一步维护或 Compose 动作前 fail closed，且不得替换 marker 或猜测前驱

#### Scenario: provenance authority 已经完成 restore
- **WHEN** 立即前驱是 provenance，且其 canonical authority paused 已按合同归档为唯一终态 audit
- **THEN** resolver 严格验证 completed authority 的 snapshot、audit、archive metadata 和当前容器事实后，允许当前新 SHA 发布绑定立即前驱的 marker 并开启新事务；任何 active/archive 混合、partial 或漂移均拒绝

#### Scenario: active provenance authority 不完整或后续漂移
- **WHEN** provenance 发布时的 active snapshot/paused 为空、schema/status/binding/hash 不完整，或者已发布 provenance 的 authority 文件、policy 或当前 Docker binding 后续发生漂移
- **THEN** 发布器或每次 resolver 加载均执行完整 active transaction 校验并 fail closed，不创建或复用不可信 provenance，且不修改 authority 所属旧 release

#### Scenario: reuse-local 的 paused ledger 不完整
- **WHEN** marker 与当前 snapshot/paused 同时存在，但原本 running 的实例缺少唯一 `stopped` 记录、包含 `pending_stop`/`restoring`/终态状态、binding 或 snapshot hash 不一致，或者与 audit/archive metadata 混合
- **THEN** resolver 拒绝 reuse-local；原本 running 时只有 schema 完整、非空且唯一 `stopped` 记录与当前 Docker exited/policy-neutralized binding 一致才可继续，原本不是 running 时只允许空 paused 且当前 Docker binding 必须与 snapshot 完全一致

#### Scenario: marker 发布后 predecessor 发生漂移
- **WHEN** marker-only、snapshot-only 或 reuse-local 再次解析时，predecessor 的 snapshot/audit 被篡改、出现 archive metadata，或当前容器 binding 不再能由 predecessor 恢复态和当前 active transaction 连续证明
- **THEN** resolver fail closed，并保持 predecessor 全部文件、权限、链接数和 metadata 状态不变

#### Scenario: 尝试向 completed authority 发布 provenance
- **WHEN** canonical paused 已归档为 completed audit，并调用 provenance 发布器指向该 authority
- **THEN** 发布器在创建 provenance 前拒绝；completed authority 只能用于严格判断新事务起点，不能伪装为 active inherited authority

### Requirement: 部署控制程序不与子进程共享脚本输入流
Canonical 部署控制器 SHALL 在同一 Bash 程序中连续执行规定阶段，并 SHALL NOT 通过可被子进程
继承和消费的标准输入传递该程序。完成判定 SHALL 要求显式终态标记，而不是以最后一个已见
preflight 的零退出码代替。

#### Scenario: 阶段中的子进程读取标准输入到 EOF
- **WHEN** preflight 或探针读取其继承的标准输入直到 EOF
- **THEN** 控制程序后续的算子启动、GPU 验证、deployment 用例、恢复与终态标记仍全部执行
