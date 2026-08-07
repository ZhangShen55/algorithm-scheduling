## Why

当前 `ai_quality` 已具备 FastAPI 注册服务和 Kafka Worker 能力，但 Kafka Worker 仍是命令行直接消费，是否消费不受集群级控制。第一版不需要默认部署多个 `ai_quality-api`，因为 API 只做轻量 Redis 读写和状态查询，真正耗资源的是 Kafka Worker、视频处理和 TIAS 推理调度。但即使 API 先单实例部署，也不能把 Kafka consumer 绑定在 API 进程里，否则后续做双实例高可用或上层 Nginx/LB 时，请求只能控制局部进程，无法形成稳定的集群控制面。

需要把 `ai_quality` 明确拆成同一项目内的两个运行角色：`ai_quality-api` 作为控制面，`ai_quality-worker` 作为执行面。API 只写 Redis 中的集群期望状态和查询注册表；Worker 独立运行、注册心跳、读取 Redis 控制状态，并按状态决定是否消费 Kafka。

## What Changes

- 将 `ai_quality` HTTP 部分整理为标准 FastAPI 后端结构，保留现有 `serve`、`consume`、`run-json` 入口兼容。
- 新增 `ai_quality-worker` 集群控制能力：API 写入 Redis `desired_state`，Worker 周期读取该状态并执行 `RUNNING`、`PAUSED`、`DRAINING`、`STOPPED`。
- 新增 Worker 注册表：每个 Worker 独立进程启动后写 Redis 心跳，记录 `worker_id`、状态、topic、consumer group、当前任务、offset、成功数、失败数、最近错误等。
- 新增 API 控制接口，建议使用控制语义而不是进程语义：
  - `POST /api/worker-control/resume`
  - `POST /api/worker-control/pause`
  - `POST /api/worker-control/drain`
  - `GET /api/worker-control/state`
  - `GET /api/workers`
  - `GET /api/workers/{worker_id}`
- 新增 TIAS 注册表查询接口：
  - `GET /api/tias/instances`
  - `GET /api/tias/instances/{instance_id}`
- 新增 ai_quality 自身健康检查接口：
  - `GET /api/health`
- 新增控制接口鉴权 key：请求必须带配置中的 key，例如 `X-AI-QUALITY-KEY`。
- 新增 Worker 启动入口：`python -m ai_quality.app worker`。保留 `consume` 作为兼容别名。
- 新增本地开发 `all` 模式可选项，用于单进程开发环境同时启动 API 和 Worker；生产不推荐使用。
- 明确生产部署建议：第一版 `ai_quality-api` 推荐单实例；如需高可用可部署 2 个 API 实例并在上层加 Nginx/LB；每个 API 实例建议 `uvicorn --workers 1`；Kafka Worker 由独立进程或容器扩容，数量与 Kafka partition、课程并发和 TIAS 容量共同决定。
- 明确 `/api/worker-control/resume` 这类接口不是启动某个机器进程，而是修改 Redis 中的集群期望状态；所有 Worker 自行收敛到该状态。
- 明确集群版部署形态：上层 Nginx 仅在多 API 实例或统一入口需要时使用；Nginx 只代理 `ai_quality-api`，不代理 `ai_quality-worker`；`ai_quality-worker` 不由 API 本地拉起，而是作为独立进程或容器运行。
- 明确“API worker=1”的含义：单个 `ai_quality-api` 实例内部只启动 1 个 Uvicorn worker；如果需要 API 高可用，应启动第二个 API 实例并由 Nginx/LB 分发请求，而不是在一个 API 实例内开多个 Uvicorn worker。
- 明确 Redis 是集群控制面的事实来源：是否允许消费、Worker 心跳、Worker 实际状态、TIAS 注册表均从 Redis 读写，不能依赖单个 API 进程内存。
- 在 `ai_quality/docker/` 中集中存放 ai_quality 部署资产，包括 API/Worker 容器构建文件、compose 示例、环境变量示例和可选 Nginx 示例。
- 在 `tias/docker/` 中集中存放 TIAS 部署资产，包括 TIAS 容器构建文件、compose 示例、环境变量示例和实例多开配置示例；保留根目录旧 Dockerfile 的兼容说明或迁移说明。

## Capabilities

### New Capabilities

- `ai-quality-worker-control`: 定义 ai_quality API 控制面如何通过 Redis 管理 Worker 集群期望状态、Worker 注册心跳、控制接口鉴权和消费状态切换。
- `ai-quality-api-service`: 定义 ai_quality FastAPI 后端结构、健康检查、TIAS 注册表查询接口、单 API 推荐部署和可选双 API 高可用约束。

### Modified Capabilities

- `ai-quality-tias-dispatch`: Kafka 任务消费入口从单纯 CLI consume 扩展为 Worker 角色；Worker 必须读取 Redis 控制状态后决定是否 poll Kafka，同时保留既有课程处理、TIAS 调度和 offset 提交语义。

## Impact

- 影响 `ai_quality` 目录结构：新增 `api/`、`api/routes/`、Worker 控制相关 application/infrastructure 模块。
- 影响 `ai_quality/app.py`：新增 `worker` 命令，`consume` 作为兼容别名；可选新增本地 `all` 开发命令。
- 影响 Redis：新增 Worker 控制状态 key、Worker 注册表 key、Worker 心跳 TTL 和控制版本号。
- 影响 Kafka Worker：从“启动即消费”调整为“启动后注册心跳，读取 desired_state，只有 RUNNING 时消费”。
- 影响配置：新增 `WorkerControlEnabled`、`WorkerControlKey`、`WorkerControlHeaderName`、`WorkerDesiredStateKey`、`WorkerRegistryKeyPrefix`、`WorkerHeartbeatIntervalSeconds`、`WorkerHeartbeatTimeoutSeconds`、`WorkerId`、`WorkerPollWhenPausedSeconds` 等。
- 影响文档和部署：需要说明 API 单实例推荐、可选双 API 高可用、Worker 集群、Redis 共享状态、Kafka partition 与 Worker 数量的关系。
- 影响运行方式：生产推荐至少拆成 `ai_quality-api`、`ai_quality-worker`、Redis、TIAS 实例池四类运行单元；Nginx/LB 仅在多 API 或统一入口时引入；本地开发可以简化，但文档必须区分生产和本地。
- 影响部署目录：新增 `ai_quality/docker/` 和 `tias/docker/`，后续部署相关文件优先放入各自服务目录，避免继续散落在项目根或服务根目录。
