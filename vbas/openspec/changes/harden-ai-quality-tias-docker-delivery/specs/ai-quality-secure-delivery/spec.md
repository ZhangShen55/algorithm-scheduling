## ADDED Requirements

### Requirement: ai_quality 必须作为独立 Docker 服务交付
ai_quality MUST 在 Docker 构建和运行时保持独立服务边界，不得依赖复制 TIAS 项目内部模块来启动 API 或 Worker。

#### Scenario: Dockerfile 不复制 TIAS 内部模块
- **WHEN** 构建 ai_quality Docker 镜像
- **THEN** Dockerfile 不得复制 `tias/core`、`tias/services`、`tias/api` 或 `tias/__init__.py` 作为 ai_quality 运行依赖

#### Scenario: ai_quality 使用自身配置加载逻辑
- **WHEN** ai_quality API 或 Worker 读取 `config.toml`
- **THEN** 系统必须通过 ai_quality 自身模块完成配置加载，不得从 `tias.core.config_loader` 导入配置加载函数

#### Scenario: ai_quality 远程调用 TIAS
- **WHEN** `TiasInferenceMode="remote"`
- **THEN** ai_quality 必须通过 TIAS HTTP 接口完成推理调度，不得在镜像内打包 TIAS 推理实现

### Requirement: ai_quality 必须支持 Cython 编译保护构建
ai_quality MUST 提供 Cython 编译保护构建模式，用于生产镜像减少明文业务源码暴露。

#### Scenario: 保护构建生成编译产物
- **WHEN** 使用 ai_quality 保护构建模式构建镜像
- **THEN** 镜像内必须包含 ai_quality 业务模块对应的 `.so` 编译产物，并能正常启动 API 和 Worker

#### Scenario: 保护构建移除明文业务源码
- **WHEN** ai_quality 保护镜像构建完成
- **THEN** 镜像内被编译保护的业务模块不得保留对应明文 `.py` 文件

#### Scenario: 薄入口可保留
- **WHEN** 某些入口文件必须保留明文以支持 `python -m ai_quality.app`
- **THEN** 这些入口文件必须只承担启动和导入职责，不得包含核心业务逻辑

#### Scenario: 开发构建保持可用
- **WHEN** 不启用 ai_quality 保护构建模式
- **THEN** Docker 镜像必须仍能按普通源码方式启动，便于开发和问题排查

### Requirement: ai_quality 必须提供完整 Docker 部署文档
ai_quality MUST 在 `ai_quality/docker/` 和运行文档中提供可直接执行的部署说明。

#### Scenario: config.toml 挂载说明
- **WHEN** 用户查看 ai_quality Docker 部署文档
- **THEN** 文档必须说明如何将宿主机 `ai_quality/config.toml` 只读挂载到容器内 `/workspace/ai_quality/config.toml`

#### Scenario: NFS 快照目录挂载说明
- **WHEN** 用户查看 ai_quality Docker 部署文档
- **THEN** 文档必须说明先将 NFS 挂载到宿主机项目 `mnt` 目录，再将该目录挂载到容器内 `/mnt`

#### Scenario: 容器内快照路径配置
- **WHEN** ai_quality 以 Docker 方式运行
- **THEN** 文档必须要求 `SnapshotMountRoot` 使用容器内路径 `/mnt`，不得使用宿主机绝对路径

#### Scenario: Redis 部署说明
- **WHEN** 本地没有 Redis
- **THEN** 文档必须提供使用 Docker 启动 Redis 的命令或 compose 示例

#### Scenario: API 和 Worker 启动说明
- **WHEN** 用户查看 ai_quality Docker 部署文档
- **THEN** 文档必须分别给出 API 容器和 Worker 容器的启动命令，并说明 Worker 不暴露 HTTP 端口

### Requirement: ai_quality 必须支持双 API 实例 Nginx 部署说明
ai_quality MUST 提供 2 个 API 实例通过 Nginx 或 LB 统一入口访问的部署说明。

#### Scenario: Nginx upstream 示例
- **WHEN** 用户需要部署 2 个 ai_quality API 实例
- **THEN** 文档必须提供 Nginx upstream 示例，将请求转发到两个 API 实例

#### Scenario: 控制接口状态一致
- **WHEN** 控制接口请求通过 Nginx 随机命中任意 ai_quality API 实例
- **THEN** API 必须读写 Redis 中的共享控制状态，使 Worker 看到一致的 `desired_state`

#### Scenario: Nginx 不代理 Worker
- **WHEN** 用户查看 ai_quality Nginx 部署说明
- **THEN** 文档必须明确 Nginx 只代理 API，不代理 ai_quality-worker，不提升 Kafka 消费并发

### Requirement: ai_quality Docker 构建必须排除敏感和无关文件
ai_quality MUST 通过 Docker 构建上下文控制避免敏感文件、临时文件和大体积无关文件进入镜像。

#### Scenario: .dockerignore 存在
- **WHEN** 查看 Docker 构建配置
- **THEN** 仓库或服务目录必须存在 `.dockerignore` 或等效构建上下文排除配置

#### Scenario: 排除敏感文件
- **WHEN** Docker 构建上下文被打包
- **THEN** `.git`、密钥文件、明文模型、测试视频、临时目录和本地配置文件不得进入受保护镜像

#### Scenario: compose 静态校验
- **WHEN** 执行 `docker compose -f ai_quality/docker/docker-compose.yml config`
- **THEN** compose 配置必须能通过静态校验

### Requirement: ai_quality 必须通过 4 节课端到端回归
ai_quality MUST 在本变更完成后使用 4 条 Kafka 课堂任务进行端到端回归验证。

#### Scenario: 4 条 Kafka 消息投递
- **WHEN** 向 `classroom_cv_task` 投递 4 条课堂视觉任务
- **THEN** ai_quality-worker 必须消费这些任务并记录 topic、partition、offset 和 task_id

#### Scenario: 4 节课成功写库
- **WHEN** 4 条任务处理完成
- **THEN** 每条任务必须写入 `lesson_ai_workflow` 成功终态、行为时间线、核心快照、学生行为统计和指标得分结果

#### Scenario: 快照写入挂载目录
- **WHEN** 任务产生核心快照
- **THEN** 快照文件必须写入 Docker 挂载的 `/mnt` 对应宿主机目录，数据库 `image_url` 必须保存相对路径

#### Scenario: 回归报告生成
- **WHEN** 4 节课回归完成
- **THEN** 必须在 OpenSpec change 的 `reports/` 目录生成运行报告，记录环境、任务 ID、验证命令、数据库计数和已知问题
