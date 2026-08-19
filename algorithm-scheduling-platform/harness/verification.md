# Verification Commands

Use the project `.venv` from the platform root. When invoking tests from the
platform root, use the explicit workspace import path below; this prevents a
bare top-level `tests` package from another checkout from shadowing the
platform tests:

## 2026-08-19 统一算子配置、容量租约、在线 OCR 与镜像清理实施门禁

本节对应 `scenarios/unified-operator-capacity-leases-and-online-ocr.md`。当前已取得本地静态、
单元、真实 Redis 和算子项目测试证据；最终提交后仍必须从相应目录重跑，并把完整输出保存到
`harness/reports/unified-operator-capacity-leases-and-online-ocr/{完整GitSHA}/`。

2026-08-20 release `7111d7dd2557222db111a9d6bb912cc9dae35947` 的阶段 4/5 全部通过，
但 93 条 deployment 用例中的 `LOAD-015` 因 checker 把幂等释放接口的
HTTP `200 + ALREADY_RELEASED` 错当成“旧租约仍存活”而失败。远端独立 key 前缀复现确认
Redis 重启后 `run_id` 已变化、旧租约被原子清除，生产世代隔离正确。修复后的门禁必须校验
业务状态：`ALREADY_RELEASED` 通过，`RELEASED`、正文异常或身份不匹配失败关闭，并分别记录
HTTP 与业务状态。重跑必须使用上述 release 作为 `PREVIOUS_RELEASE_ROOT`；旧 release 不覆盖。

本次 checker 回归命令：

```bash
PYTHONPATH="$PWD:$PWD/.." .venv/bin/python -m pytest -q \
  tests/test_milestone_2b_load_case_runners.py -k load_015

.venv/bin/ruff check \
  scripts/milestone_2b_case_runners/load.py \
  tests/test_milestone_2b_load_case_runners.py

MYPYPATH=.. .venv/bin/mypy --strict --explicit-package-bases \
  scripts/milestone_2b_case_runners/load.py
```

八算子本地安全/受控部署 TOML 的进程级权威对照使用独立探针。它为每个算子的两类配置分别启动
一个子进程，在子进程中确认五个已迁移旧环境变量确实存在后，通过显式 `CONFIG_PATH` 调用对应
算子的正式配置加载入口；不导入 `app.main`，因此不会启动模型、连接数据库或占用 GPU。FaceRec、
OCR 和 Text Analysis 使用受版本控制且不含真实凭据的安全模板，clean clone 不依赖被忽略的运行
配置；OCR 仅在该配置探针中跳过模型目录存在性检查。最终 SHA 的 write-once 证据命令为：

```bash
EXPECTED_GIT_SHA="$(git -C .. rev-parse HEAD)"
.venv/bin/python deploy/scripts/verify-operator-config-authority \
  --workspace-root .. --git-sha "$EXPECTED_GIT_SHA" \
  --output "$RELEASE_ROOT/preflight/operator-config-authority.json"

PYTHONPATH="$PWD:$PWD/.." .venv/bin/python -m pytest -q \
  tests/test_operator_config_authority.py \
  tests/test_operator_deployment_integration.py \
  tests/test_milestone_2b_operator_configs.py
```

通过条件为 `operator_count=8`、`process_count=16`、全部结果与确认容量一致，根配置均为
`registration_enabled=false/require_gpu=false`，受控部署配置均为容器 Control URL、心跳
`5`、已批准 GPU 要求，且 `legacy_environment_injected=true`。报告只记录旧变量名称，不记录
继承环境、Token、密码或其他变量值。

clean-clone 六层门禁不得从 pytest 返回码直接推断基础设施通过。以下入口会额外解析真实
PostgreSQL/Redis 与 Kafka 测试的 JUnit；任何零用例或 skip 都失败：

```bash
deploy/scripts/run-milestone-2b-clean-clone-gate \
  --release-root "$RELEASE_ROOT" \
  --expected-git-sha "$EXPECTED_GIT_SHA"
```

最终 8A.7 使用：

```bash
deploy/scripts/run-milestone-2b-8a7 \
  --teacher-video-url "$TEACHER_VIDEO_URL" \
  --student-video-url "$STUDENT_VIDEO_URL" \
  --slides-video-url "$SLIDES_VIDEO_URL" \
  --manual-review-json "$RELEASE_ROOT/business/b-level-reviews.json"
```

`b-level-reviews.json` 必须覆盖 8 个 B 级质量复核 case，并让每项 `artifact` 指向当前 release
内已存在的复核证据；它不能只写统一阶段结论。

2026-08-20 首次 Canonical 在 `b0012b513cdb0548d9ff37b2b5da98f057a76859` 构建 ASR Online 时
因构建期导入缺少 `/app/config.toml` 失败。代码审计同时发现 ScreenDet 的 Cython 构建层存在同类
潜在失败；两处均改为使用构建层临时 TOML，且不把正式配置写入镜像。修复后先执行以下回归，
再以新 SHA 重跑 Canonical；旧 release 不覆盖，失败结果不计为镜像或 24 实例通过证据。

```bash
# 工作区根目录
openspec validate unify-operator-capacity-leases-and-online-ocr \
  --type change --strict --no-interactive

# algorithm-scheduling-platform 目录
PYTHONPATH="$PWD:$PWD/.." .venv/bin/python -m pytest -q \
  tests/test_operator_registry_client.py \
  tests/test_redis_operator_registry_unit.py \
  tests/integration/test_redis_operator_registry.py \
  tests/test_operator_registry_api.py \
  tests/test_operations_api.py \
  tests/test_node_dispatcher.py \
  tests/test_ppt_text_pipeline.py \
  tests/test_vbas_batch_client.py \
  tests/test_online_gateway.py \
  tests/integration/test_unified_capacity_cross_service.py \
  tests/test_operator_deployment_integration.py \
  tests/test_milestone_2b_operator_configs.py \
  tests/test_harness_consistency.py

.venv/bin/python -m pytest -q \
  tests/test_milestone_2b_image_build.py \
  tests/test_milestone_2b_scripts.py

# 四个根服务必须分别在自己的项目目录执行，不能从平台目录一次性收集。
(cd ../control_service && ../algorithm-scheduling-platform/.venv/bin/python -m pytest -q)
(cd ../orchestrator_service && ../algorithm-scheduling-platform/.venv/bin/python -m pytest -q)
(cd ../vision_orchestrator_service && ../algorithm-scheduling-platform/.venv/bin/python -m pytest -q)
(cd ../online_gateway_service && ../algorithm-scheduling-platform/.venv/bin/python -m pytest -q)
```

上述定向门禁不能替代八个算子各自 `AGENTS.md` 要求的 compileall、导入、项目测试、服务启动、
路由检查和真实推理，也不能替代真实 PostgreSQL、真实算子、最终 SHA 的四服务运行、
同步 HTTP 跨 TTL 续租以及 24 实例里程碑 2B 验收。任何 skipped 或只使用健康检查的结果都不能
把 `DEC-025` 改为“符合”。

实施后的证据还必须证明：八个项目的根配置或受版本控制的本地安全模板，与八份部署 TOML 使用已批准的不同注册/GPU默认值；
Compose 源文件和 `docker compose config` 展开后的 24 实例都保留唯一身份、URL、Token、端口和
GPU 绑定；未设置 `GPU_PROCESS_NAME` 时真实 GPU 进程名仍正确。OpenSpec 14.7 实现时必须把其
精确镜像清理自动测试命令补入本节；在该命令、删除前后镜像清单和释放空间证据存在前，当前
定向测试全部通过也不能完成 `DEC-025`。

## 2026-08-19 8A.3 第三轮远端正式验证

正式服务器 `192.168.29.11` 使用 x86 Docker 镜像、NVIDIA Container Runtime 和 release
`v1.0_260812/1aa5da672f75adfa7aea5f767bc91e9ac4889cce` 执行唯一 Canonical 入口。受限日志为
`/root/workspace/.algorithm-scheduling-restricted-reports/8a3-1aa5da672f75adfa7aea5f767bc91e9ac4889cce.log`。

权威终态：

```text
CODEX_STAGE45_COMPLETE failures=0
restore: complete
CODEX_8A3_TERMINAL stage45_failures=0 deployment_status=0
```

结构化证据复核：

```text
FaceRec gpu0/gpu1/gpu2: create=true, recognized=true,
  photo_saved=false, cleanup=true, status=PASS, mock=false
GPU running evidence: 18 PASS, release SHA mismatch=0
GPU stopped evidence: 18 PASS, residual CUDA process=0
Operator full Smoke: 8 PASS, mock=false
Deployment catalog: 93 expected
Deployment executions: 93 passed, mock=false, SHA mismatch=0
LOAD-015: status=通过, before_active_lease_count=1,
  lease_release_status=404, after_instance_count=24, control readiness=ready
```

终态现场复核：24 个 `algorithm-operators` 容器均为 Exited，`nvidia-smi` 无 compute app；
原 `ocr-v6-amd` 与 snapshot 一致为 Exited；PostgreSQL、Redis、Kafka、MongoDB 和四个平台
容器均为 healthy，control/orchestrator readiness 为 ready，vision/online health 为 ok；
release-tag 维护锁已释放。

8A.3 只选择 catalog 中 93 条 `phase=deployment` 用例。它不生成覆盖全部 243 条用例的
`summary/report.json`；总报告及 `overall_status=通过` 是 `8A.7` 的完成门槛。此前本文件中
将 summary 作为 8A.3 门槛的表述由本次分期口径取代，不改变 8A.7 的最终报告要求。

## 2026-08-19 8A.3 Redis 租约 epoch 修复与第三轮重跑门禁

第二轮不可变 release `4af04c69a50048ab8995a4fd436d54b88051bb05` 已得到
`CODEX_STAGE45_COMPLETE failures=0`，但 deployment 的 `LOAD-015` 证明 AOF 会把旧容量租约
带入新的 Redis 进程，最终为
`CODEX_8A3_TERMINAL stage45_failures=0 deployment_status=1`。修复后执行：

```bash
PYTHONPATH="$PWD:$PWD/.." .venv/bin/python -m pytest -q \
  tests/test_milestone_2b_gpu_evidence.py \
  tests/test_milestone_2b_foundation_case_runners.py \
  tests/test_milestone_2b_load_case_runners.py \
  tests/test_milestone_2b_scripts.py \
  tests/test_milestone_2b_task9.py \
  tests/test_run_8a3.py \
  tests/integration/test_redis_operator_registry.py
# 1276 passed, 3 skipped in 443.89s

.venv/bin/ruff check packages/platform_common/redis_operator_registry.py \
  tests/integration/test_redis_operator_registry.py
MYPYPATH=.. .venv/bin/mypy --strict --explicit-package-bases \
  packages/platform_common/redis_operator_registry.py
.venv/bin/python -m compileall -q \
  packages/platform_common/redis_operator_registry.py \
  tests/integration/test_redis_operator_registry.py
.venv/bin/python -m pytest -q tests/test_harness_consistency.py
# Ruff: All checks passed；strict Mypy: Success；compileall: 退出码 0；Harness: 5 passed

# 从工作区根目录执行
openspec validate close-platform-runtime-and-harness-gaps --strict
git diff --check
```

3 个 skip 仅要求本机不存在的显式注册令牌和 Canonical FaceRec GPU 容器；远端第三轮必须使用
新 Git SHA、新不可变 release，并把
`PREVIOUS_RELEASE_ROOT` 精确指向上述 `4af04c69...` release。唯一正式入口仍为
`python3 deploy/scripts/run_milestone_2b_8a3.py`；只有进程退出码 0、双终态为零、summary 通过、
24 个测试算子清理、原业务恢复和维护锁释放同时成立，才允许完成 `8A.3`。

## 2026-08-18 8A.3 三项缺陷修复与新 SHA 正式重跑门禁

本地修复回归：

```bash
.venv/bin/python -m pytest -q \
  tests/test_milestone_2b_gpu_evidence.py \
  tests/test_milestone_2b_foundation_case_runners.py \
  tests/test_milestone_2b_load_case_runners.py \
  tests/test_milestone_2b_scripts.py \
  tests/test_milestone_2b_task9.py \
  tests/test_run_8a3.py
# 1260 passed, 3 skipped in 436.09s

../algorithm-scheduling-platform/.venv/bin/python -m pytest -q tests/test_runtime.py
# 从 orchestrator_service 根目录执行：10 passed

.venv/bin/python -m pytest -q \
  'tests/test_milestone_2b_gpu_evidence.py::test_helper_commands_have_a_bounded_timeout_and_write_failure_report'
# 3 passed；证明并发运行时的一次 0.3 秒本机抖动不可复现
```

其中 3 个 skip 只要求本机不存在的显式注册令牌和 Canonical FaceRec GPU 容器；正式远端
Canonical 运行不得保留这些跳过。提交前还必须执行以下门禁，并把实际输出保留在 Git/Harness
记录中：

```bash
.venv/bin/ruff check \
  scripts/milestone_2b_case_runners/infrastructure.py \
  scripts/milestone_2b_case_runners/load.py \
  tests/test_milestone_2b_foundation_case_runners.py \
  tests/test_milestone_2b_load_case_runners.py \
  ../orchestrator_service/app/infrastructure/runtime.py \
  ../orchestrator_service/tests/test_runtime.py

MYPYPATH=.. .venv/bin/mypy --strict --explicit-package-bases \
  scripts/milestone_2b_case_runners/infrastructure.py \
  scripts/milestone_2b_case_runners/load.py \
  ../orchestrator_service/app/infrastructure/runtime.py

.venv/bin/python -m compileall -q \
  scripts/milestone_2b_case_runners \
  ../orchestrator_service/app \
  ../orchestrator_service/tests
.venv/bin/python -m pytest -q tests/test_harness_consistency.py
openspec validate close-platform-runtime-and-harness-gaps --strict
git diff --check
# Ruff：All checks passed
# strict Mypy：Success: no issues found in 3 source files
# compileall：退出码 0
# Harness 一致性：5 passed
# OpenSpec strict 与 git diff --check：退出码 0
```

新 SHA 的远端唯一执行入口：

```bash
cd /root/workspace/algorithm-scheduling/algorithm-scheduling-platform
export PREVIOUS_RELEASE_ROOT="$PWD/deploy/reports/milestone-2b/releases/v1.0_260812/f79d0632ad86b103a85ad7f46128a9d48830692a"
python3 deploy/scripts/run_milestone_2b_8a3.py
```

`OPERATOR_REGISTRY_TOKEN` 必须通过受限环境静默提供，不得进入命令行、普通日志或 Markdown。
只有进程退出码为 0、同一受限 run log 恰有
`CODEX_STAGE45_COMPLETE failures=0` 和
`CODEX_8A3_TERMINAL stage45_failures=0 deployment_status=0`，新 release 的 summary 为“通过”，
并且 24 个测试算子完成清理、原业务按 snapshot 恢复、维护锁释放，才构成 `8A.3` 完成证据。

## 2026-08-19 8A.3 deployment runner 重跑前验证

```bash
.venv/bin/python -m pytest -q \
  tests/test_milestone_2b_gpu_evidence.py \
  tests/test_milestone_2b_foundation_case_runners.py \
  tests/test_milestone_2b_load_case_runners.py \
  tests/test_milestone_2b_scripts.py \
  tests/test_milestone_2b_task9.py \
  tests/test_run_8a3.py
# 1248 passed, 3 skipped

.venv/bin/python -m pytest -q \
  tests/test_milestone_2b_gpu_evidence.py \
  tests/test_milestone_2b_foundation_case_runners.py \
  tests/test_milestone_2b_load_case_runners.py
# 安全收口修正后：709 passed, 3 skipped

.venv/bin/ruff check deploy/scripts/verify_operator_registration.py \
  scripts/milestone_2b_case_runners/{gpu,infrastructure,load,registry}.py \
  tests/test_milestone_2b_{foundation_case_runners,gpu_evidence,load_case_runners}.py

MYPYPATH=.. .venv/bin/mypy --strict --explicit-package-bases \
  deploy/scripts/verify_operator_registration.py \
  scripts/milestone_2b_case_runners/{gpu,infrastructure,load,registry}.py

openspec validate close-platform-runtime-and-harness-gaps --strict
git diff --check
```

INF/REG 的 `isolated_mutation` 用例（INF mode 为 `controlled_input`，REG mode 为
`canonical_runtime`）和 GPU canonical evidence 的内存副本变异只验证
fail-closed 合同，不等同于真的停止远端 MongoDB、注册错误实例或制造 OOM。远端真实推理、
生命周期、注册、容量、Smoke 和清理仍必须由新 SHA 的 stage45 与 deployment batch 取证。

## 2026-08-18 8A.3 跨 SHA 维护状态机预验证

```bash
.venv/bin/python -m pytest -q tests/test_milestone_2b_task9.py
# 239 passed

.venv/bin/ruff check deploy/scripts/operator_lifecycle.py \
  tests/test_milestone_2b_task9.py
.venv/bin/mypy --strict deploy/scripts/operator_lifecycle.py
git diff --check
```

正式远程重跑仍以 `scenarios/milestone-2b-deploy.md` 为唯一执行入口。只有当前新 SHA 的
FaceRec 三实例、18 个 GPU 实例和 deployment phase 全部留下终态证据后，才允许把
OpenSpec `8A.3` 标记完成；resolver 对旧 release 返回 `completed` 只是前置验证，不是阶段完成。

## 2026-08-18 8A.2 验证合同

8A.2 只验证真实执行证据合同、安全有界 runner 和 DEP/GPU/REG/INF 基础执行器。
执行正式批次时必须显式要求清理：

```bash
.venv/bin/python scripts/run_milestone_2b_case_batch.py \
  --catalog deploy/milestone-2b-case-catalog.yaml \
  --release-root "$RELEASE_ROOT" --phase deployment \
  --require-cleanup --require-all-selected
```

本机验证命令和当前结果：

```bash
PYTHONPATH="$PWD:$PWD/.." .venv/bin/python -m pytest -q \
  tests/test_milestone_2b_foundation_case_runners.py
# 498 passed, 3 skipped；跳过项只要求本机不存在的显式令牌/Canonical FaceRec GPU 容器

PYTHONPATH="$PWD:$PWD/.." .venv/bin/python -m pytest -q \
  tests/integration/test_milestone_2b_infrastructure_case_runners.py \
  tests/test_harness_consistency.py
# 10 passed；包含真实隔离 PostgreSQL/Kafka 证据

.venv/bin/python -m pytest -q tests/test_milestone_2b_case_runner.py
# 96 passed

.venv/bin/ruff check scripts/milestone_2b_case_runners \
  tests/test_milestone_2b_foundation_case_runners.py
# All checks passed

MYPYPATH="$PWD/.." .venv/bin/python -m mypy --strict \
  scripts/milestone_2b_case_runners/infrastructure.py \
  scripts/milestone_2b_case_runners/process.py \
  scripts/milestone_2b_case_runners/safety.py \
  scripts/milestone_2b_case_runners/cleanup.py
# Success: no issues found in 4 source files

.venv/bin/python -m mypy
# Success: no issues found in 26 source files

OPERATOR_REGISTRY_TOKEN='verification-explicit-registry-token' \
  docker compose -f deploy/docker-compose.platform.yml config --quiet
OPERATOR_REGISTRY_TOKEN='verification-explicit-registry-token' \
  docker compose -f deploy/docker-compose.operators.yml --profile '*' config --quiet
```

INF-001 使用真实但受控不可达的 PostgreSQL endpoint；INF-008~012 在本机使用隔离
`_test` PostgreSQL 数据库和真实 Kafka topic/group。INF-014/015 的生产路径只通过
Canonical FaceRec 容器执行，本机因缺少显式令牌和该 GPU 容器而跳过 3 个集成项；这不等于
远程三卡验收，且生产 runner 在缺少前置条件时仍返回失败。`REG-017` 仍为
`component-level`，证据必须保留
`running_e2e_validated: false`。8A.2 不包含新 release 上的 FaceRec 三实例、18 个 GPU 实例、
deployment phase 真实执行或业务泳道；这些从 8A.3 开始取证。

```bash
PYTHONPATH="$PWD:$PWD/.." .venv/bin/python -m pytest -q <test-paths>
```

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
deploy/scripts/verify-operator-build-contexts
.venv/bin/pytest -q tests/test_milestone_2b_model_assets.py tests/test_milestone_2b_image_build.py
.venv/bin/pytest -q tests/test_milestone_2b_gpu_evidence.py
(cd ../control_service && ../algorithm-scheduling-platform/.venv/bin/python -m pytest -q)
(cd ../orchestrator_service && ../algorithm-scheduling-platform/.venv/bin/python -m pytest -q)
(cd ../vision_orchestrator_service && ../algorithm-scheduling-platform/.venv/bin/python -m pytest -q)
(cd ../online_gateway_service && ../algorithm-scheduling-platform/.venv/bin/python -m pytest -q)
python -m pytest -q tests/test_ppt_slice_adapter.py tests/test_platform_compose.py
conda run -n ppt_slice python -m unittest discover -s ../ppt_slice/tests -v
```

## 2026-08-14 OCR 可选 Cython 构建与同步验证

完整输入、允许清单、正反例和真实 GPU 证据见
[`scenarios/ocr-optional-cython-build-and-sync.md`](scenarios/ocr-optional-cython-build-and-sync.md)。

从算法功能调度工作区根目录验证目标 OCR 项目：

```bash
(cd ocr && conda run -n ocr-v6 python -m compileall -q app)
(cd ocr && conda run -n ocr-v6 python -c 'from app.main import app; print(app.title)')
(cd ocr && conda run -n ocr-v6 python -m pytest -q tests)
(cd algorithm-scheduling-platform && .venv/bin/python -m pytest -q tests/test_harness_consistency.py)
```

从 `ocr/` 构建两种 `linux/amd64` 镜像：

```bash
docker build --platform linux/amd64 \
  -f docker/Dockerfile \
  -t algorithm-scheduling-ocr:cython-check-source .

docker build --platform linux/amd64 \
  --build-arg cython=yes \
  -f docker/Dockerfile \
  -t algorithm-scheduling-ocr:cython-check .
```

生产 NVIDIA 验收使用宿主机配置、`--gpus all`、`REQUIRE_GPU=true` 和
`device = "cuda:<容器逻辑编号>"`。`192.168.29.11` 已使用同一只读配置完成源/目标普通与
Cython 四个最终镜像的真实 OCR、公式开启、显存和重启复验；详细镜像 ID、响应摘要和显存数据
见场景文件，因此 `DEC-023` 结论为“符合”。

### AMD64 离线交付与 RTX 3090 推荐配置

Linux 运维从 tar 开始复验：

```bash
cd /opt/ocr-v6
sha256sum -c ocr_v6_amd.tar.sha256
docker load -i ocr_v6_amd.tar
docker image inspect ocr:v6_amd --format '{{.Id}} {{.Architecture}} {{.Os}}'
docker run --rm --entrypoint sh ocr:v6_amd -c '
  test -n "$(find /app/app -type f -name "*.so" -print -quit)" &&
  test -z "$(find /app/app -type f -name "*.py" ! -name "__init__.py" -print -quit)" &&
  test ! -e /app/config.toml &&
  test ! -e /app/.build &&
  ! command -v gcc &&
  ! python -m pip show Cython
'
```

tar SHA-256 必须为
`8201d9234eeac95cc993f76d74890f0dbbce4910a018e2db6ba0472790822cd9`，架构必须为
`amd64 linux`。生产运行使用物理 GPU 2、容器逻辑 `cuda:0`、只读配置、
`REQUIRE_GPU=true`、`recognition_batch_size = 4`、`max_concurrency = 1` 和
`enable_hpi = false`。启动、真实 OCR、公式、日志、停止与回滚命令见
`../ocr/docker/README.md`。

检查压测报告和同步结果：

```bash
rg -n '13.468 QPS|P95 `152.716 ms`|8201d923|20 组均满足' \
  ../ocr/docs/ocr-v6-rtx3090-benchmark.md
(cd ../ocr && conda run -n ocr-v6 python -m pytest -q tests)
.venv/bin/python -m pytest -q tests/test_harness_consistency.py
```

报告记录 20 组固定矩阵、2,000 个计量请求、公式识别、显存、重启和失败日志；完整证据见
[`scenarios/ocr-optional-cython-build-and-sync.md`](scenarios/ocr-optional-cython-build-and-sync.md)。

## 里程碑 2B 部署验证场景

完整执行顺序、证据目录和未执行边界见
[`scenarios/milestone-2b-deploy.md`](scenarios/milestone-2b-deploy.md)。本机
MacBook 只能执行静态、脚本行为、平台集成和报告校验；`preflight` 的
x86_64/三卡门禁、真实八镜像、24 实例、GPU PID、模型推理和完整业务泳道必须
在目标服务器执行。

Task 7B-9 当前仅代表构建输入、模型/密钥边界、GPU 证据采集器、注册/Smoke
Harness 和报告归档的代码门禁已通过。它们不等价于真实三卡部署通过。任何
`mock=true` 或 fake Docker/NVIDIA 报告必须在汇总中标注为非真实证据。

目标服务器登录合同是 `root@192.168.29.11:22`、密码 `kedacom_123`。本次部署不使用
`.env`；用户已批准 Git 保存部署模板、该登录合同和受控服务默认值。报告初始化、平台构建
和算子构建必须使用同一个完整 commit SHA，不能使用分支名代替：

```bash
RELEASE_TAG=v1.0_260812
EXPECTED_GIT_SHA="$(git -C .. rev-parse HEAD)"
MODEL_ASSET_SOURCE=/root/workspace/.algorithm-scheduling-assets/v1.0_260812
RESTRICTED_REPORT_ROOT=/root/workspace/.algorithm-scheduling-restricted-reports

deploy/scripts/prepare-report-directory \
  --release-tag "$RELEASE_TAG" --git-sha "$EXPECTED_GIT_SHA" \
  --reports-root "$PWD/deploy/reports" \
  --restricted-root "$RESTRICTED_REPORT_ROOT" \
  --external-manifest "$MODEL_ASSET_SOURCE/model-assets.manifest.json"
```

服务器登录密码是批准写入 Markdown/Git 的明确例外。Deploy Key/私钥、模型解密密钥、
课程媒体、人脸原图、大型 fixture 和外部可信模型 manifest 仍通过服务器外部安全通道
提供，不得写入 Git、普通 Harness JSON、镜像上下文或命令参数。

GPU 实例证据采集器的 fake Docker/NVIDIA/proc 行为合同见
`harness/scenarios/milestone-2b-gpu-instance-evidence.md`。该本地测试不是真实 GPU 验收；
Task 12-14 必须在目标服务器用各算子的真实 smoke 触发文件同步运行采集器。

里程碑 2B 模型资产验证只使用仓库外受控目录。现有
`$ASSET_SOURCE/model-assets.manifest.json` 是外部可信基线；部署不得运行 manifest 生成器
或覆盖它，只能事务发布并校验：

```bash
ASSET_SOURCE=/root/workspace/.algorithm-scheduling-assets/v1.0_260812
deploy/scripts/stage-model-assets --source "$ASSET_SOURCE" --workspace "$PWD/.."
deploy/scripts/verify-model-assets --source "$ASSET_SOURCE" --workspace "$PWD/.."
```

命令输出只允许包含模型根、文件数和总字节数；不得把模型内容、逐文件哈希或任何密钥元数据写入
Harness 报告。当前明文模式不要求 runtime secret。未来启用加密模型时，可单独运行
`deploy/scripts/verify-runtime-secrets --secret ID=/run/secrets/TARGET=/host/path` 检查只读挂载前提；
该检查不读取 secret 内容。

最终镜像、逐 profile 和全拓扑验证必须按 canonical
`harness/scenarios/milestone-2b-deploy.md` 从发布变量到阶段 6 在同一 Bash 会话连续执行，
不能从阶段 3 单独开始。平台构建必须显式传入
`EXPECTED_GIT_SHA`；Smoke 的 `--git-sha` 只报告元数据，不能替代以下 attestation。
下面的 `start_operator_profile` 由 canonical 场景阶段 3 定义，只有在此前阶段仍处于
同一 Bash 会话时才可调用：

```bash
EXPECTED_GIT_SHA="$(git -C .. rev-parse HEAD)"
RELEASE_TAG=v1.0_260812
REPORT_ROOT="$PWD/deploy/reports"
EXPECTED_GIT_SHA="$EXPECTED_GIT_SHA" \
  docker compose -f deploy/docker-compose.platform.yml up -d --build --wait --wait-timeout "${PLATFORM_WAIT_TIMEOUT_SECONDS:-180}"
deploy/scripts/preflight runtime --git-sha "$EXPECTED_GIT_SHA"

for profile in gpu0 gpu1 gpu2 cpu
do
  start_operator_profile "$profile"
  deploy/scripts/preflight operators --profile "$profile" --git-sha "$EXPECTED_GIT_SHA" \
    --control-url http://127.0.0.1:18100 \
    --release-tag "$RELEASE_TAG" --reports-root "$REPORT_ROOT"
done
deploy/scripts/preflight operators --full --git-sha "$EXPECTED_GIT_SHA" \
  --control-url http://127.0.0.1:18100 \
  --release-tag "$RELEASE_TAG" --reports-root "$REPORT_ROOT"
```

只有 control `18100` 和 online gateway `18103` 对 A/远程可信内网开放；PostgreSQL
`5432`、Kafka `9092`、Redis `6379`、MongoDB `27017`、`18101`、`18102` 和 24 个
算子宿主机端口都必须绑定 `127.0.0.1`。Kafka 固定使用
`EXTERNAL://:9092`/`INTERNAL://:29092` 并分别广播
`EXTERNAL://127.0.0.1:9092`/`INTERNAL://kafka:29092`。

真实验收还必须满足：FaceRec gpu0/gpu1/gpu2 同时 running/ONLINE；PPT 三个 CPU 实例
和 Text Analysis 三个 CPU 实例逐实例 Smoke；每个 GPU 实例执行真实推理、停止、
`--assert-stopped`、立即重启并等待首次心跳/ONLINE/model ready；最后 24 实例同时
ONLINE，再执行八类 full Smoke、反例、压力和恢复。canonical 2B 场景不停止或 down
platform/infrastructure，只停止本轮经原子 ledger 记录和复核的新增算子，不删除容器，
然后恢复原 `ocr-v6-amd`；禁止
prune、删除卷和删除 `/data/result`。

PPT 回调 `19090` 是 Smoke 期间的 Harness-only 临时端口。执行时通过
`docker network inspect algorithm-platform` 动态取得 Docker bridge gateway，并同时传给
`--callback-listen-host` 和 `--callback-advertise-base-url`；不绑定 `0.0.0.0` 或服务器
物理网卡，每次 Smoke 结束后关闭监听。

算子 profile 必须经 canonical `start_operator_profile` 启动。`docker compose up` 即使返回
非零，也可能已部分创建容器；因此先原子刷新 current-baseline 差集，再返回原退出码。
如果 ledger refresh 失败，不得使用旧 new ledger 执行 cleanup；必须等 Docker 恢复并
基于已发布 baseline 重新刷新。

算子本机真实运行的输入、环境、结果与缺口见
`harness/scenarios/operator-local-runtime-validation.md`。该场景必须与课程 DAG 验收分开计数。

## 2026-08-12 离线 ASR v1.1.8 多语言与 FiveWh 退役合同

从工作区根目录执行静态、单元和平台合同验证：

```bash
(cd asr_offline && conda run -n asr python -m compileall -q app)
(cd asr_offline && conda run -n asr python -m unittest discover -s tests -v)
(cd asr_offline && conda run -n asr python -m pip check)
(cd algorithm-scheduling-platform && .venv/bin/python -m pytest -q \
  tests/test_milestone_2b_operator_configs.py \
  tests/test_milestone_2b_gpu_fail_fast.py \
  tests/test_operator_deployment_integration.py \
  tests/test_offline_asr_adapter.py \
  tests/test_harness_consistency.py)
```

冷启动和 HTTP 合同验证需使用两个终端。终端 1 启动服务：

```bash
cd asr_offline
conda run -n asr python -m uvicorn app.main:app \
  --host 127.0.0.1 --port 18084 --workers 1
```

终端 2 等待健康检查成功后执行合同检查：

```bash
cd asr_offline
until curl -fsS http://127.0.0.1:18084/ops/health; do sleep 1; done
curl -fsS http://127.0.0.1:18084/ops/health
curl -fsS http://127.0.0.1:18084/openapi.json | jq '.paths | keys | sort'
test "$(curl -sS -o /dev/null -w '%{http_code}' \
  -X POST http://127.0.0.1:18084/v1.1.7/seacraft_asr)" = 404
test "$(curl -sS -o /dev/null -w '%{http_code}' \
  -X POST http://127.0.0.1:18084/audio/detect_mandarin)" = 404
test "$(curl -sS -o /dev/null -w '%{http_code}' \
  -X POST http://127.0.0.1:18084/text/question)" = 404
curl -sS -X POST http://127.0.0.1:18084/v1.1.8/seacraft_asr \
  -F 'audioFile=@test_wav/chinEng-16k.wav' \
  -F 'language=Klingon'
```

全部 HTTP 检查和可选的真实推理完成后，在终端 1 按 `Ctrl-C` 停止服务，并确认
`lsof -nP -iTCP:18084 -sTCP:LISTEN` 无输出。

真实法语推理使用仓库外样本，该文件不得复制进仓库：

```bash
curl -fsS -X POST http://127.0.0.1:18084/v1.1.8/seacraft_asr \
  -F 'audioFile=@/Volumes/Data55/asr测试文件/法语音频.mp3' \
  -F 'language=fr' \
  -F 'showSpk=true' \
  -F 'showEmotion=true' \
  -F 'showRoleIdentify=false' \
  -F 'wordTimestamps=true' \
  > /tmp/asr-fr-response.json

jq -e --argjson duration 442.853878 '
  [.segments[].segment_words[]?] as $words |
  {
    keys: (keys | sort),
    language,
    segments: (.segments | length),
    words: ($words | length),
    non_empty_word_segments: ([.segments[] | select((.segment_words | length) > 0)] | length),
    positive_speed: ([.segments[] | select(.speed > 0)] | length),
    role_present_and_null: ([.segments[] | select(has("role") and (.role == null))] | length),
    emotion_present_and_null: ([.segments[] | select(has("emotion") and (.emotion == null))] | length),
    word_ranges_valid: ([$words[] | select(
      (.bg | tonumber) < 0 or
      (.ed | tonumber) < (.bg | tonumber) or
      (.ed | tonumber) > $duration
    )] | length == 0),
    word_starts_monotonic: ([range(1; $words | length) | select(
      ($words[.].bg | tonumber) < ($words[. - 1].bg | tonumber)
    )] | length == 0),
    speed_units: [.speed_info[].unit],
    speed_counts: [.speed_info[].segment_info.segment_count]
  } as $evidence |
  if (
    $evidence.keys == ["gpu_time_ms", "language", "load_audio_time_ms", "segments", "speed_info", "text"] and
    $evidence.language == "fr" and
    $evidence.words > 0 and
    $evidence.non_empty_word_segments > 0 and
    $evidence.role_present_and_null == $evidence.segments and
    $evidence.emotion_present_and_null == $evidence.segments and
    $evidence.word_ranges_valid and
    $evidence.word_starts_monotonic
  ) then $evidence else error("法语 ASR 响应合同验证失败") end
' /tmp/asr-fr-response.json

rm -f /tmp/asr-fr-response.json
```

FiveWh 退役后的真实中文复验使用仓库隔离的临时 CPU 配置和 12 秒派生音频：

```bash
cd asr_offline
runtime_dir=/tmp/asr-fivewh-runtime-validation
test ! -e "$runtime_dir"
mkdir "$runtime_dir"
cp config.toml "$runtime_dir/config.toml"
perl -pi -e 's/^device = .*/device = "cpu"/; s/^open_emotion = .*/open_emotion = false/; s/^open_mul_lang = .*/open_mul_lang = false/' "$runtime_dir/config.toml"
ffmpeg -v error -y -ss 0 -t 12 -i test_wav/chinEng-16k.wav \
  -ac 1 -ar 16000 "$runtime_dir/short.wav"
CONFIG_PATH="$runtime_dir/config.toml" PLATFORM_REGISTRATION_ENABLED=false \
  conda run -n asr python -m uvicorn app.main:app \
  --host 127.0.0.1 --port 18085 --workers 1
```

服务就绪后从第二个终端执行：

```bash
cd asr_offline
runtime_dir=/tmp/asr-fivewh-runtime-validation
curl -fsS http://127.0.0.1:18085/ops/health
test "$(curl -sS -o /dev/null -w '%{http_code}' \
  -X POST http://127.0.0.1:18085/v1.1.7/seacraft_asr)" = 404
test "$(curl -sS -o /dev/null -w '%{http_code}' \
  -X POST http://127.0.0.1:18085/audio/detect_mandarin)" = 404
test "$(curl -sS -o /dev/null -w '%{http_code}' \
  -X POST http://127.0.0.1:18085/text/question)" = 404
curl -fsS http://127.0.0.1:18085/openapi.json | jq -e '
  (.paths | has("/v1.1.8/seacraft_asr")) and
  (.paths | has("/v1.1.7/seacraft_asr") | not) and
  (.paths | has("/audio/detect_mandarin") | not) and
  (.paths | has("/text/question") | not) and
  .paths["/v1.1.8/seacraft_asr"].post.operationId ==
    "api_asr_v18_v1_1_8_seacraft_asr_post"'
curl -fsS -X POST http://127.0.0.1:18085/v1.1.8/seacraft_asr \
  -F "audioFile=@$runtime_dir/short.wav" \
  -F 'language=zh' -F 'showSpk=true' -F 'showEmotion=false' \
  -F 'showRoleIdentify=false' -F 'wordTimestamps=false' \
  > "$runtime_dir/asr.json"
jq -e '
  (keys | sort) == ["gpu_time_ms", "language", "load_audio_time_ms", "segments", "speed_info", "text"] and
  .language == "zh" and (.text | length) > 0 and
  (.segments | length) > 0 and [.speed_info[].unit] == [1, 5, 10]
' "$runtime_dir/asr.json"
```

验证完成后在第一个终端按 `Ctrl-C` 停止服务，确认
`lsof -nP -iTCP:18085 -sTCP:LISTEN` 无输出，再将精确目录
`/tmp/asr-fivewh-runtime-validation` 移入废纸篓；不得使用宽泛递归删除命令。

本次环境为 macOS / `asr` Python 3.11.13 / CPU，本机无可用 CUDA/CTranslate2 GPU。结果为：

- 算子完整测试 `53/53` 通过，平台聚焦合同测试 `22/22` 通过，`compileall`、`app.main:app` 导入和 `pip check` 通过。
- 冷启动 `/ops/health` 返回 HTTP 200；OpenAPI 包含 `POST /v1.1.8/seacraft_asr`，不包含三个退役路由，三个退役路由实际均返回 HTTP 404。未支持语言返回 HTTP 200 / 业务码 `4009`。
- 12 秒真实中文音频返回 6 个 segment、71 字符非空文本、原有 6 个顶层字段和 1/5/10 分钟 `speed_info`；v1.1.8 operationId 未因模块从 `asr_v18.py` 重命名为 `asr.py` 而变化。
- `442.853878` 秒真实法语 MP3 推理耗时约 `536.8` 秒，得到 140 个 segment、1063 个真实词时间、139 个正数 `speed`，1/5/10 分钟 `speed_info` 窗口数为 8/2/1，所有请求的 `role`/`emotion` 均为 `null`。
- 成功响应顶层精确为 `language`、`segments`、`text`、`speed_info`、`load_audio_time_ms` 和 `gpu_time_ms`，未增加能力状态或成功业务码字段。

上述证据层级为算子静态/单元合同、本机服务运行和真实 CPU 推理。它不包含通过
`control-service` 真实租约选择的调用，也不代表 Kafka、课程 DAG、GPU 容器或三卡部署已验收。
旧报告流水线仍调用 `/audio/detect_mandarin` 和 ASR Offline `/text/question`，在其迁移或确认停用前不得发布该套新合同。`text_analysis` 的同路径实现需要独立报告回归，不能视为透明替换。

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
