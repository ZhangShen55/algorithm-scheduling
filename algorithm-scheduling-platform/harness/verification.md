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

## 2026-08-12 MongoDB authentication and FaceRec readiness

Run the isolated MongoDB authentication check with Docker available:

```bash
.venv/bin/python -m pytest -q tests/integration/test_mongodb_auth_runtime.py
```

The test creates a uniquely named `mongo:7.0` container with a random host port and
tmpfs-backed `/data/db`, verifies that configured credentials can ping and a wrong
password cannot, then removes that exact container in `finally`. It does not use or
delete the deployment `mongodb_data` volume. On 2026-08-12 the test completed with
`1 passed` against Docker Desktop 29.5.3.

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

### Kafka 客户端选择与兼容性证据

正式客户端选择 `aiokafka` 0.14.x：共享 adapter 需要原生异步 Producer/Consumer、
`send_and_wait`、手动 offset 提交、有界轮询和 lag 查询，`aiokafka` 可直接提供这些能力，
无需在 asyncio 运行时外再包装线程模型。安装元数据显示 0.14.0 的 `Requires-Python` 为
`>=3.10`，平台基线为 `>=3.11`，因此兼容；`orchestrator-service` 显式限定
`aiokafka>=0.14,<0.15`，避免未验证的次版本漂移。此 Kafka 客户端仅安装在平台运行环境，
不进入算子模型环境，不改变算子 wheel 与业务协议。

```bash
.venv/bin/python -c 'from importlib.metadata import metadata, version; print("aiokafka", version("aiokafka")); print("Requires-Python", metadata("aiokafka")["Requires-Python"])'
rg -n '^requires-python|^aiokafka' pyproject.toml ../orchestrator_service/requirements.txt
```

2026-08-11 实测输出为 `aiokafka 0.14.0`、`Requires-Python >=3.10`、平台
`requires-python = ">=3.11"` 与 orchestrator `aiokafka>=0.14,<0.15`。

## 方案 C 里程碑 2A 真实运行时验收

从平台目录执行一键 Harness：

```bash
.venv/bin/python scripts/run_milestone_2a.py
```

或在基础设施已 healthy 时直接执行验收命令：

```bash
.venv/bin/python -m pytest -q -rs \
  tests/integration/test_kafka_runtime.py \
  tests/integration/test_milestone_2a_runtime.py
```

命令不得出现 skipped。运行时每次创建唯一 `_test` PostgreSQL 数据库、Redis DB 14 UUID 前缀、
唯一 Kafka Topic/Consumer Group 和临时服务端口；不会连接或清理 `algorithm` 业务数据库。JSON
证据写入 gitignore 的 `harness/reports/milestone-2a/`，应包含容器健康和版本、Outbox
`published_at`/尝试次数、重启前后 Kafka committed/end offset、节点/任务轨迹、Stub 调用顺序、
运行中 `lease:*` hash 得到的 `selected_instances`、终态后的有界租约清理轮询、
Control/Stub/orchestrator 健康响应、两次 orchestrator readiness 和不同
PID/启动序号/停止日志/真实退出码/强杀与提前退出标记，以及本次唯一 Consumer Group 精确删除验证和
最终 GET。所有健康/readiness 探针必须为 HTTP 200；404 等非 200 响应不算启动成功。重启恢复必须先
等待 Outbox 重新发布完成，再证明 committed offset 和 Topic end offset 均精确为 4，并经过短暂静默窗口
复核仍为 4。teardown 必须尝试全部进程、Redis、Kafka Group/Topic 和临时目录清理；删除 Topic 前先校验
完整测试命名，删除后轮询确认不存在。

2026-08-11 单场景实测：PostgreSQL 17.10、Redis 7.4.10、Kafka 4.0.0 均 healthy；首次 offset
为 2，停止并重启 orchestrator、注入重复消息及恢复一条 Outbox 后 offset 为 4；任务事实仍为 2 个
task type 和 4 个节点，重复发布项 `publish_attempts=2`。NORMAL/URGENT 均观察到状态 30、50、60，
URGENT 的 ASR Stub 调用先于 NORMAL，最终租约为零。该证据完成 2A 契约 Stub 层级，不包含真实
PPT、OCR、离线 ASR、VBas；ScreenDet 仅属于 `online-gateway-service`。

8.4 的“实例选择”不使用注册响应推断。E2E 在节点执行轮询期间扫描本次唯一
Redis 前缀下的 `lease:*` hash，按 `lease_id` 去重记录 `lease_id`、`instance_id`、
`capability` 和 `service_url` 到 `evidence.selected_instances`。测试断言运行时实际观察到
`asr_offline` 和 `text_analysis`，实例 ID 等于本次对应注册实例且 URL 均为契约
Stub；证据在租约释放前采集，终态后仍独立断言租约清零。

Outbox 发布失败保留待发布的证据来自 `tests/test_outbox_publisher.py` 的组件故障注入；
真实 Broker Harness 通过将一条 Outbox 恢复为待发布、重启 orchestrator，验证其重投后
`published_at` 恢复且 `publish_attempts>=2`。这是“组件故障注入 + 真实 Broker 恢复闭环”，
未执行、也不声称执行过真实 Broker 停机。

Broker 集成用例另外验证真实发布/消费、手动提交、同 group 重启 offset 恢复和
未提交消息重投；里程碑 Harness 验证重复消息幂等。`orchestrator_service/tests/test_runtime.py`
使用 `FakeConsumer.lag()` 注入“Kafka 依赖不可用”，验证 `/ops/readiness` 返回 503、
`checks.kafka.ready=false` 且中文诊断可见。该就绪用例不停止真实 Kafka 容器，与真实
Broker 行为集成证据合并支撑 2.6。

2026-08-11 状态同步复审：`compileall`、Ruff 和严格 Mypy 均通过；平台完整套件
`276 passed`，orchestrator 完整套件 `17 passed`；两份真实集成文件直接运行与一键
Harness 分别为 `12 passed`，均无 skipped。基础设施和平台两份 Compose 配置通过
`config --quiet`，`openspec validate close-platform-runtime-and-harness-gaps --strict` 通过。

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
