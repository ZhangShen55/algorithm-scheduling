# Verification Commands

Use the project `.venv` from the platform root.

```bash
.venv/bin/python -m compileall -q packages ../control_service/app ../orchestrator_service/app ../vision_orchestrator_service/app ../online_gateway_service/app
.venv/bin/pytest -q tests/test_harness_consistency.py
.venv/bin/python scripts/check_migrations.py
.venv/bin/pytest -q tests/test_database_comments.py
.venv/bin/pytest -q tests/test_infrastructure_config.py
.venv/bin/pytest -q tests/contract
.venv/bin/pytest -q tests
.venv/bin/ruff check packages tests ../control_service/app ../control_service/tests ../orchestrator_service/app ../orchestrator_service/tests ../vision_orchestrator_service/app ../vision_orchestrator_service/tests ../online_gateway_service/app ../online_gateway_service/tests
.venv/bin/python -m mypy packages scripts
MYPYPATH="$PWD" .venv/bin/python -m mypy packages scripts ../control_service/app ../orchestrator_service/app ../vision_orchestrator_service/app ../online_gateway_service/app
docker compose -f deploy/docker-compose.infrastructure.yml config --quiet
docker compose -f deploy/docker-compose.operators.yml config --quiet
docker compose -f deploy/docker-compose.platform.yml config --quiet
(cd ../control_service && ../algorithm-scheduling-platform/.venv/bin/python -m pytest -q)
(cd ../orchestrator_service && ../algorithm-scheduling-platform/.venv/bin/python -m pytest -q)
(cd ../vision_orchestrator_service && ../algorithm-scheduling-platform/.venv/bin/python -m pytest -q)
(cd ../online_gateway_service && ../algorithm-scheduling-platform/.venv/bin/python -m pytest -q)
python -m pytest -q tests/test_ppt_slice_adapter.py tests/test_platform_compose.py
conda run -n ppt_slice python -m unittest discover -s ../ppt_slice/tests -v
```

算子本机真实运行的输入、环境、结果与缺口见
`harness/scenarios/operator-local-runtime-validation.md`。该场景必须与课程 DAG 验收分开计数。

Root-service relocation image checks use the workspace root as build context:

```bash
docker build -f control_service/docker/Dockerfile -t algorithm-scheduling/control-service:relocation-check .
docker build -f orchestrator_service/docker/Dockerfile -t algorithm-scheduling/orchestrator-service:relocation-check .
docker build -f vision_orchestrator_service/docker/Dockerfile -t algorithm-scheduling/vision-orchestrator-service:relocation-check .
docker build -f online_gateway_service/docker/Dockerfile -t algorithm-scheduling/online-gateway-service:relocation-check .
```

On 2026-08-07 all four images built, started independently and returned HTTP 200 from
`/health`. Container inspection showed only the current service under `/app/app`; no sibling
service source was copied. The platform suite reported `192 passed`, the four service suites
reported `4`, `5`, `8` and `9` passed, Ruff and strict Mypy passed, all three Compose files
parsed, and `openspec validate relocate-platform-services-to-workspace-root --strict` passed.
The root allowlist `.dockerignore` limited actual service build contexts to roughly 11-127 KB,
and the old-path gate covered runtime source, Dockerfiles, Compose, Makefile, scripts and current
delivery documentation.

Integration and runtime commands must record infrastructure versions and container status. A skipped integration test is not passing evidence. Full end-to-end evidence must show Kafka offsets, Worker-produced database state, operator HTTP/WebSocket traffic and filesystem results.

## 2026-08-10 PPT 视频输入字段规范化

本轮将 PPT 算子的规范视频输入字段统一为 `video_path`。算子接受远程 URL 与绝对本地路径，旧
`uri` 仅作为兼容输入；orchestrator 只发送已经准备好的绝对本地路径。验证结果如下：

- `ppt_slice`：`compileall`、`app.main:app` 导入及完整 `unittest` 通过，共 `99` 项。
- 本地文件能力：测试在临时目录生成并解码真实 MP4，共读取 `2` 帧；临时目录自动清理，不保留或提交视频。
- 平台适配器：`tests/test_ppt_slice_adapter.py` 共 `10` 项通过。
- 代码质量：orchestrator 相关 Ruff 与严格 Mypy 均通过。
- 运行时：临时启动 `127.0.0.1:19001` 后，`/health` 与版本接口成功；OpenAPI 请求模型只暴露
  `video_path`，不暴露旧 `uri`。
- OpenSpec：`openspec validate detect-ppt-video-playback-segments --strict` 通过。

该证据覆盖算子契约、真实本地文件解码、平台适配器和服务运行时冒烟，不表示 orchestrator
后台循环或 Kafka 驱动的完整 PPT 端到端链路已经验收。

## 方案 C 里程碑 1 验收

从平台目录执行：

```bash
docker compose -f deploy/docker-compose.infrastructure.yml ps postgres redis

.venv/bin/python -m pytest -q -rs \
  tests/integration/test_course_repository.py \
  tests/integration/test_redis_operator_registry.py \
  tests/integration/test_operator_audit_repository.py \
  tests/integration/test_control_service_foundation.py

PYTHONPATH="$PWD:$PWD/..:$PWD/../control_service" \
  .venv/bin/python -m pytest -q -rs tests ../control_service/tests

.venv/bin/ruff check packages tests ../control_service/app ../control_service/tests
MYPYPATH="$PWD" .venv/bin/python -m mypy packages ../control_service/app
.venv/bin/python -m compileall -q packages ../control_service/app
.venv/bin/python scripts/check_migrations.py
docker compose -f deploy/docker-compose.platform.yml config --quiet
(cd .. && openspec validate close-platform-runtime-and-harness-gaps --strict)

# 临时 PostgreSQL 测试库残留，预期返回 0。
docker compose -f deploy/docker-compose.infrastructure.yml exec -T postgres \
  psql -U algorithm -d postgres -X -Atc \
  "SELECT count(*) FROM pg_database WHERE datname ~ '^algorithm_control_milestone1_(main|gw[0-9]+)_[0-9a-f]{8}_test$'"

# Redis DB 14/15 测试前缀残留，两条命令均预期无输出。
docker compose -f deploy/docker-compose.infrastructure.yml exec -T redis \
  redis-cli -n 14 --scan --pattern 'milestone1-control-test:*'
docker compose -f deploy/docker-compose.infrastructure.yml exec -T redis \
  redis-cli -n 15 --scan --pattern 'algorithm-platform:test:operator-registry:*'
```

2026-08-07 证据：PostgreSQL 17.10 和 Redis 7.4.10 容器均为 healthy；四组联合集成测试
`63 passed`，平台与 Control 完整回归 `255 passed`，没有 skipped。临时 PostgreSQL
数据库与 Redis 测试前缀在测试后无残留。readiness 已覆盖并行依赖检查、总截止预算、DSN 原有 PostgreSQL options 保留、缺字段和未执行 `0005`；注册已覆盖首次心跳激活和短暂心跳故障重试。该证据只完成里程碑 1，不包含 Kafka 或 DAG。

方案 C 的基础闭环验收单独执行 `harness/scenarios/foundation-scheduling-closure.md`。该场景只要求
真实 PostgreSQL、Redis、Kafka、`control-service`、`orchestrator-service` 和契约 Stub；不得因为
真实 PPT 算子尚未接入而跳过基础运行时验证，也不得把静态 DDL 测试写成 Broker 闭环已通过。

## 2026-08-11 算子本机运行与 PPT 最新终态合同

- 注册客户端构建为 `algorithm_operator_registry_client-0.1.0-py3-none-any.whl`，要求 Python 3.10+，不携带平台内部包。
- ASR 最终环境 `asr` 使用 Python 3.11.13；FaceRec 最终环境 `facerecapi` 使用 Python 3.10.19。
- FaceRec 3.11 被 FastDeploy 的 CPython 3.10 macOS 扩展阻塞；未更换推理后端。
- 八类算子均完成本机业务调用；PPT 使用合成 MP4，只证明机制，真实课程 P 视频仍待提供。
- PPT 平台适配器现在接收并持久化 `dynamic_segments`，处理失败终态，拒绝祖先符号链接，并保证续租后台任务失败时仍释放容量。
- 这些结果不表示 orchestrator factory 已注入 PPT handler；当前默认应用上的真实回调路由仍会返回 503，直到里程碑 2 接线完成。

本机 PostgreSQL 现状使用以下只读查询复核；未经用户明确要求，不在审计步骤执行 DDL：

```bash
docker exec algorithm-scheduling-platform-postgres-1 \
  psql -U algorithm -d postgres -X -c \
  "SELECT datname FROM pg_database WHERE datallowconn ORDER BY datname"

docker exec algorithm-scheduling-platform-postgres-1 \
  psql -U algorithm -d algorithm -X -c \
  "SELECT schemaname, tablename FROM pg_tables WHERE schemaname NOT IN ('pg_catalog', 'information_schema') ORDER BY 1, 2"
```
