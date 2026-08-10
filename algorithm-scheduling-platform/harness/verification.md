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

方案 C 的基础闭环验收单独执行 `harness/scenarios/foundation-scheduling-closure.md`。该场景只要求
真实 PostgreSQL、Redis、Kafka、`control-service`、`orchestrator-service` 和契约 Stub；不得因为
真实 PPT 算子尚未接入而跳过基础运行时验证，也不得把静态 DDL 测试写成 Broker 闭环已通过。

本机 PostgreSQL 现状使用以下只读查询复核；未经用户明确要求，不在审计步骤执行 DDL：

```bash
docker exec algorithm-scheduling-platform-postgres-1 \
  psql -U algorithm -d postgres -X -c \
  "SELECT datname FROM pg_database WHERE datallowconn ORDER BY datname"

docker exec algorithm-scheduling-platform-postgres-1 \
  psql -U algorithm -d algorithm -X -c \
  "SELECT schemaname, tablename FROM pg_tables WHERE schemaname NOT IN ('pg_catalog', 'information_schema') ORDER BY 1, 2"
```
