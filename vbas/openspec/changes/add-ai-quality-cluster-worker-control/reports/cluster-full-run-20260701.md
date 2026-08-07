# ai_quality 集群受控消费全量验证报告（2026-07-01）

## 验证环境

- 代码分支：`dev_6.0_ai_quality`
- OpenSpec 变更：`add-ai-quality-cluster-worker-control`
- Redis：Docker 本地 `ai-quality-redis`，`127.0.0.1:6379`
- ai_quality API：本地端口 `9101`，持久会话启动
- ai_quality Worker：2 个进程，`worker-cluster-apply-1`、`worker-cluster-apply-2`
- TIAS：4 个本地实例，`127.0.0.1:8981`、`8982`、`8983`、`8984`
- Kafka：`10.67.65.8:9092`，topic `classroom_cv_task`
- DB：`10.67.65.8:23308/ai_quality_eval`
- 快照挂载：`10.80.5.131:/image` 挂载到项目 `mnt`

## Kafka 投递

使用 `scripts/produce_ai_quality_kafka_message.py` 向 `classroom_cv_task` 投递 6 条消息：

| task_id | course_id | partition | offset |
| --- | --- | --- | --- |
| `lesson-cluster-apply-0001` | `cv-cluster-apply-001` | 0 | 20 |
| `lesson-cluster-apply-0002` | `cv-cluster-apply-002` | 0 | 21 |
| `lesson-cluster-apply-0003` | `cv-cluster-apply-003` | 0 | 22 |
| `lesson-cluster-apply-0004` | `cv-cluster-apply-004` | 0 | 23 |
| `lesson-cluster-apply-0005` | `cv-cluster-apply-005` | 0 | 24 |
| `lesson-cluster-apply-0006` | `cv-cluster-apply-006` | 0 | 25 |

Kafka topic 当前只有 partition `0`。因此两个 Worker 同组运行时，只有一个 Worker 被分配 partition 并实际消费，另一个 Worker 保持心跳但空闲。这不是调度故障，是 Kafka partition 数限制。

最终 consumer group `cv-analysis-service-cluster-20260701-205507` 在 partition 0 的 committed offset 为 `26`。

## API 与 Worker 控制验证

- `GET /api/health` 返回 `status=ok`，Redis 检查为 `ok`。
- `GET /api/workers` 可查询 2 个 Worker 心跳。
- `POST /api/worker-control/resume` 后 Worker 从 `PAUSED` 收敛到 `RUNNING`。
- 6 节课完成后执行 `POST /api/worker-control/pause`，两个 Worker 最终均收敛到 `PAUSED`，不继续 poll 新 Kafka 消息。
- Worker 日志包含控制状态变化、Kafka 消费、TIAS 实例选择和 offset 提交关键日志。
- 额外启动 2 个临时 ai_quality API（`9101`、`9102`）和 Nginx/LB（`19091`）验证多 API 场景；请求命中过两个 API，控制状态由任意 API 写入后两个 API 读取一致。

## TIAS 调度验证

4 个 TIAS 实例均在注册表中可见，最终状态均为 `UP`：

| instance_id | success_count | failure_count |
| --- | ---: | ---: |
| `tias-8981` | 84 | 0 |
| `tias-8982` | 84 | 0 |
| `tias-8983` | 84 | 0 |
| `tias-8984` | 84 | 0 |

Worker 通过 Redis TIAS 注册表选择实例，日志中可见 `worker_id`、`task_id`、`batch_id`、`stream_type`、`instance_id` 和选择原因。

## 数据库结果

6 个任务均写入 `lesson_ai_workflow` 成功终态：`status=3`、`progress=100`、`error_msg=NULL`。

| 表 | 每节课记录数 |
| --- | ---: |
| `lesson_behavior_timeline` | 55 |
| `lesson_snapshot_event` | 19 |
| `lesson_student_behavior_stat` | 1 |
| `indicator_score_result` | 5 |

快照文件写入 `mnt/cv/lesson-cluster-apply-*`，共 114 张，与 `lesson_snapshot_event` 的 6 * 19 条一致。

## 容器冒烟

- `docker compose -f ai_quality/docker/docker-compose.yml config` 通过。
- `docker compose -f tias/docker/docker-compose.yml config` 通过。
- 使用临时 compose 避免端口冲突，成功启动 `ai-quality-redis-smoke`、`ai-quality-api-smoke`、`ai-quality-worker-smoke`。
- `GET http://127.0.0.1:9011/api/health` 返回 `status=ok`。
- `GET http://127.0.0.1:9011/api/workers` 可看到 `worker-docker-smoke-1` 以 `PAUSED` 状态注册并上报心跳。
- smoke 容器已清理。

TIAS Docker 完整容器启动未完成：`pytorch/pytorch:2.6.0-cuda11.8-cudnn9-runtime` 的 Docker Hub manifest 查询超时，无法继续拉基础镜像。当前已完成 TIAS compose 静态验证和 4 个本地 TIAS 进程端到端联调。

## 测试命令

- `conda run -n jy-tias env PYTHONPATH=. pytest -q`：117 passed，2 skipped。
- `openspec validate add-ai-quality-cluster-worker-control --strict`：通过。
- `docker compose -f ai_quality/docker/docker-compose.yml config`：通过。
- `docker compose -f tias/docker/docker-compose.yml config`：通过。

## 结论

本次完成 1 个 API、2 个 Worker、4 个 TIAS、本地 Docker Redis、Kafka 6 节课投递和业务入库全量验证。受 Kafka topic 单 partition 限制，实际消费并发为 1；要让多个 ai_quality Worker 同时处理多节课，需要扩容 `classroom_cv_task` partition 数，并同步评估 TIAS 容量。

部署侧发现 ai_quality Dockerfile 复用了 `tias/requirements.txt`，会拉取 torch/CUDA/ultralytics 等重依赖，导致控制面和 Worker 镜像构建过重。建议后续拆分 ai_quality 轻量依赖文件，只保留 FastAPI、Kafka、Redis、DB、OpenCV/requests 等必要依赖。
