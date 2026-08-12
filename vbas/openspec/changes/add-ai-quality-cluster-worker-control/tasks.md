## 1. FastAPI 控制面结构

- [x] 1.1 新增 `ai_quality/api/` 结构，拆分 app 工厂、依赖注入、健康检查、TIAS 查询、Worker 控制和 Worker 查询路由。
- [x] 1.2 保留 `ai_quality/http_app.py:create_app_from_config` 兼容入口，并改为复用新的 API app 工厂。
- [x] 1.3 保留 `python -m ai_quality.app serve` 启动方式，默认只启动 FastAPI API，不启动 Kafka Worker。
- [x] 1.4 增加 `GET /api/health`，返回 API 进程、Redis 连接状态、配置摘要和服务版本。

## 2. Redis Worker 控制模型

- [x] 2.1 实现 Worker 集群控制状态仓储，支持读取和写入 `desired_state`、version、updated_at、updated_by、reason。
- [x] 2.2 实现 `resume`、`pause`、`drain`、`state` 控制服务，并保证单 API 部署可用、可选多 API 部署时读写一致。
- [x] 2.3 实现控制接口鉴权，header 名和 key 从配置读取，日志不得输出 key 原文。
- [x] 2.4 在 `config.toml.example` 中补充 Worker 控制、Redis key、心跳、暂停轮询间隔、默认状态等配置和中文注释。

## 3. Worker 注册表和状态上报

- [x] 3.1 实现 Worker 注册表仓储，使用 Redis 保存 `ai_quality:workers` 和 `ai_quality:worker:{worker_id}`。
- [x] 3.2 Worker 启动时生成或读取 `worker_id`，写入启动时间、topic、consumer group、actual_state 和 TTL。
- [x] 3.3 Worker 主循环中按配置周期刷新心跳，包含 current_task_id、partition、offset、processed_count、failed_count、last_error。
- [x] 3.4 API 查询 Worker 列表时清理已过期 Worker ID，并对不存在或过期 Worker 返回 404。

## 4. Kafka Worker 受控消费

- [x] 4.1 新增 `python -m ai_quality.app worker` 推荐入口，`consume` 保留为兼容别名。
- [x] 4.2 将 Kafka 消费循环改为读取 Redis `desired_state` 后再决定是否 poll Kafka。
- [x] 4.3 `RUNNING` 状态允许消费并处理课堂视觉任务，任务开始、调度、完成、失败、offset 提交都记录简洁中文关键日志。
- [x] 4.4 `PAUSED` 状态不拉取新 Kafka 消息，保持 Worker 心跳。
- [x] 4.5 `DRAINING` 状态完成当前任务并提交 offset 后停止拉新消息，实际状态切换为 `PAUSED`。
- [x] 4.6 `STOPPED` 状态停止消费循环或保持空转心跳，具体行为由配置控制，且不得继续 poll Kafka。

## 5. TIAS 注册表查询和调度兼容

- [x] 5.1 增加 `GET /api/tias/instances` 和 `GET /api/tias/instances/{instance_id}`，返回心跳未过期的 TIAS 实例状态。
- [x] 5.2 保持 ai_quality-worker 通过 Redis TIAS 注册表选择 TIAS 实例，并继续使用远程 HTTP 推理接口。
- [x] 5.3 保持现有小批次调度策略和 `max_concurrent_batches`、`running_batches`、`queued_batches` 容量判断口径。
- [x] 5.4 保持课堂质量数据库写入口径不变，只操作本阶段确认由 ai_quality 负责的业务表。

## 6. 集群部署文档

- [x] 6.1 更新 `ai_quality/RUNNING.md`，补充生产推荐形态：`ai_quality-api` 默认单实例且 `uvicorn --workers 1`，高可用时可部署 2 个 API 实例并接入 Nginx/LB，Worker 独立扩容。
- [x] 6.2 在运行文档中说明 `/api/worker-control/resume|pause|drain` 是修改 Redis 集群期望状态，不是启动或杀死本地进程。
- [x] 6.3 增加 API 健康检查示例、控制接口 curl 示例、Worker 查询示例，并补充可选 Nginx upstream 示例。
- [x] 6.4 说明 Kafka partition 数、Worker 数、TIAS 容量三者共同决定有效并发。
- [x] 6.5 新增 `ai_quality/docker/`，放置 ai_quality API/Worker 的 Dockerfile、compose 示例、env 示例、可选 Nginx 示例和 README。
- [x] 6.6 新增 `tias/docker/`，放置受支持的普通/安全 TIAS Dockerfile、compose 示例、env 示例和 README。
- [x] 6.7 明确旧 CUDA 11.3 + Python 3.8 镜像因统一算子注册客户端要求 Python 3.10 及以上而退役，并记录普通镜像与安全镜像迁移路径。

## 7. 测试和验证

- [x] 7.1 增加 Worker 控制状态仓储单元测试，覆盖 resume、pause、drain、version 递增和默认状态。
- [x] 7.2 增加 Worker 注册表单元测试，覆盖注册、心跳、TTL 过期清理和单 Worker 查询 404。
- [x] 7.3 增加 API 路由测试，覆盖健康检查、TIAS 查询、Worker 控制鉴权、Worker 查询。
- [x] 7.4 增加受控消费循环测试，覆盖 RUNNING 消费、PAUSED 不 poll、DRAINING 完成当前任务后暂停。
- [x] 7.5 本地使用 Docker Redis、1 个 ai_quality-api、至少 2 个 ai_quality-worker、4 个 TIAS 实例完成受控消费联调。
- [x] 7.6 可选启动第 2 个 ai_quality-api 和 Nginx/LB，验证请求随机命中任意 API 时控制状态一致。
- [x] 7.7 向 `10.67.65.8:9092` 的 `classroom_cv_task` 投递 6 节课测试消息，验证任务完成、offset 口径、日志、Worker 状态和数据库结果。
- [ ] 7.8 使用 `ai_quality/docker/` 和 `tias/docker/` 中的示例命令完成一次本地容器化冒烟验证，至少验证 Redis、1 个 API、1 个 Worker、1 个 TIAS 可启动并互相连通。
