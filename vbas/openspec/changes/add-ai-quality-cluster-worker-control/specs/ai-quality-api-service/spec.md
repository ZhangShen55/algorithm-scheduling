## ADDED Requirements

### Requirement: ai_quality 必须提供标准 FastAPI 后端结构
ai_quality MUST 将 HTTP 后端组织为可扩展的 FastAPI API 结构，并保留现有启动入口兼容。

#### Scenario: FastAPI app 可创建
- **WHEN** 调用 ai_quality API app 工厂
- **THEN** 系统必须返回包含健康检查、TIAS 查询、Worker 控制和 Worker 查询路由的 FastAPI app

#### Scenario: 旧 HTTP app 入口兼容
- **WHEN** 现有代码调用 `create_app_from_config`
- **THEN** 系统必须仍能创建 ai_quality HTTP 服务，不要求调用方改入口

#### Scenario: CLI serve 兼容
- **WHEN** 执行 `python -m ai_quality.app --config <config> serve`
- **THEN** 系统必须启动 ai_quality FastAPI HTTP 服务

### Requirement: ai_quality API 必须提供健康检查接口
ai_quality API MUST 暴露自身健康检查接口，用于 Nginx、部署系统或人工检查 API 是否可服务。

#### Scenario: 健康检查成功
- **WHEN** 调用 `GET /api/health`
- **THEN** 系统必须返回 API 进程状态、配置的 Redis key 前缀和服务版本摘要

#### Scenario: Redis 不可用
- **WHEN** Redis 连接不可用且健康检查配置为检查 Redis
- **THEN** 系统必须返回非健康状态，并包含简洁错误原因

### Requirement: ai_quality API 必须提供 TIAS 注册表查询接口
ai_quality API MUST 允许查询 Redis 中已注册的 TIAS 实例状态。

#### Scenario: 查询所有 TIAS 实例
- **WHEN** 调用 `GET /api/tias/instances`
- **THEN** 系统必须返回所有心跳未过期的 TIAS 实例列表，包含 instance_id、base_url、status、capabilities、running_batches、queued_batches、max_concurrent_batches、max_queue_size、latency、failure_count 和 last_error

#### Scenario: 查询单个 TIAS 实例
- **WHEN** 调用 `GET /api/tias/instances/{instance_id}` 且实例存在
- **THEN** 系统必须返回该实例的完整注册和心跳状态

#### Scenario: 查询不存在的 TIAS 实例
- **WHEN** 调用 `GET /api/tias/instances/{instance_id}` 且实例不存在或心跳已过期
- **THEN** 系统必须返回 404

### Requirement: ai_quality API 推荐单实例部署并支持可选高可用
ai_quality API MUST 支持单实例生产部署；当需要高可用并部署多个 API 实例时，必须只读写 Redis 共享状态，不依赖单个 API 进程内存表达集群状态。

#### Scenario: 单 API 实例部署
- **WHEN** 第一版生产部署 ai_quality-api
- **THEN** 文档必须推荐单个 API 实例使用 `uvicorn --workers 1`，且 Kafka 消费能力不得依赖 API 实例数

#### Scenario: 请求打到任意 API 实例
- **WHEN** Nginx 将控制或查询请求转发到任意 ai_quality-api 实例
- **THEN** 该实例必须从 Redis 读写共享状态，使所有 API 实例看到一致结果

#### Scenario: 可选双 API 高可用
- **WHEN** 需要 API 高可用
- **THEN** 文档必须建议部署 2 个 ai_quality-api 实例并使用 Nginx/LB 统一入口，而不是在单个 API 实例内增加 Uvicorn worker 数

### Requirement: ai_quality API 控制接口必须鉴权
ai_quality API MUST 对 Worker 控制类接口执行配置化 key 鉴权。

#### Scenario: key 正确
- **WHEN** 请求带有配置指定 header 且 key 正确
- **THEN** 控制接口必须继续处理请求

#### Scenario: key 缺失或错误
- **WHEN** 请求未带 key 或 key 错误
- **THEN** 控制接口必须拒绝请求，并且日志不得打印 key 原文

#### Scenario: 控制接口未启用
- **WHEN** `WorkerControlEnabled=false`
- **THEN** 控制接口必须拒绝修改 Worker 控制状态

### Requirement: ai_quality 必须集中管理部署资产
ai_quality MUST 在服务目录下提供 `docker/` 部署目录，用于集中保存 ai_quality API 和 Worker 的部署相关文件。

#### Scenario: ai_quality docker 目录存在
- **WHEN** 查看 `ai_quality/docker/`
- **THEN** 目录必须包含 API/Worker 镜像构建、compose 示例、环境变量示例和运行说明

#### Scenario: API 和 Worker 使用同一部署目录
- **WHEN** 需要容器化启动 ai_quality-api 或 ai_quality-worker
- **THEN** 文档必须说明同一镜像如何通过不同启动命令运行 API 或 Worker

#### Scenario: 可选 Nginx 示例
- **WHEN** 需要部署 2 个 ai_quality-api 实例做高可用
- **THEN** `ai_quality/docker/` 必须提供可选 Nginx/LB 示例或说明
