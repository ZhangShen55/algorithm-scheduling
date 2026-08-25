# Verification Commands

Use the project `.venv` from the platform root. When invoking tests from the
platform root, use the explicit workspace import path below; this prevents a
bare top-level `tests` package from another checkout from shadowing the
platform tests:

## 2026-08-23 A 服务极限负载 Campaign 本地门禁

本节对应
[`scenarios/milestone-2b-extreme-load-campaign.md`](scenarios/milestone-2b-extreme-load-campaign.md)
和 OpenSpec `run-milestone-2b-extreme-load-campaign`。从平台目录执行：

```bash
.venv/bin/ruff check \
  scripts/extreme_load tests/extreme_load tests/deploy \
  deploy/scripts/production_stack.py \
  deploy/scripts/migration_executor.py \
  deploy/scripts/image_lifecycle.py \
  deploy/scripts/extreme_load_faults.py \
  scripts/run_extreme_load_campaign.py

PYTHONPATH="$PWD:$PWD/.." .venv/bin/python -m pytest -q \
  tests/extreme_load tests/deploy

MYPYPATH="$PWD/.." .venv/bin/mypy --strict --explicit-package-bases \
  scripts/extreme_load \
  deploy/scripts/production_stack.py \
  deploy/scripts/migration_executor.py \
  deploy/scripts/image_lifecycle.py \
  deploy/scripts/extreme_load_faults.py \
  scripts/run_extreme_load_campaign.py

.venv/bin/python -m compileall -q scripts/extreme_load deploy/scripts
bash -n \
  deploy/scripts/run-extreme-load-campaign \
  deploy/scripts/start-production-stack \
  deploy/scripts/status-production-stack \
  deploy/scripts/stop-production-stack \
  deploy/scripts/apply-database-migrations \
  deploy/scripts/production-image-lifecycle
```

2026-08-23 本地结果为 `167 passed`，Ruff、strict Mypy（20 个源文件）、`compileall`、导入
和 Bash syntax 均通过。该结果只达到静态/单元层级：协调器对实时北向执行要求显式
`--allow-live-execution`，并对 SSH 媒体下载、真实主机指标、故障语义、混合和长稳用例返回
blocked。不能据此宣称 `192.168.29.11` 的发布、Campaign 或清理通过。

同日新增真实迁移账本与中间件联合门禁，从平台目录执行：

```bash
.venv/bin/python -m pytest -q -rs \
  tests/integration/test_course_repository.py \
  tests/integration/test_redis_operator_registry.py \
  tests/integration/test_operator_audit_repository.py \
  tests/integration/test_control_service_foundation.py \
  tests/integration/test_kafka_runtime.py \
  tests/integration/test_milestone_2a_runtime.py \
  tests/integration/test_migration_executor_runtime.py
```

结果为 `94 passed`、无 skip。测试创建唯一 `_test` PostgreSQL 数据库、隔离 Redis 前缀和
唯一 Kafka topic/group，覆盖任务突发、幂等、Outbox、DAG、租约、Orchestrator 重启恢复及
`0001`–`0007` 迁移首次/重复账本；没有连接或清理 `algorithm` 业务数据库。该证据达到真实
PostgreSQL/Redis/Kafka 集成层级，仍不代表真实算子、远端三卡部署或极限负载已经通过。

本地生产运行时收口后执行：

```bash
.venv/bin/python -m pytest -q \
  tests/extreme_load tests/deploy \
  tests/integration/test_migration_executor_runtime.py
.venv/bin/python -m ruff check \
  scripts/extreme_load scripts/run_extreme_load_campaign.py \
  deploy/scripts/image_lifecycle.py deploy/scripts/migration_executor.py \
  tests/extreme_load tests/deploy/test_image_lifecycle.py \
  tests/integration/test_migration_executor_runtime.py
.venv/bin/python -m mypy --strict \
  scripts/extreme_load scripts/run_extreme_load_campaign.py \
  deploy/scripts/image_lifecycle.py deploy/scripts/migration_executor.py
```

结果为 `315 passed`，Ruff 通过，strict Mypy 对 24 个源文件通过；`compileall`、直接导入和
Bash syntax 同样通过。覆盖 Campaign 查询/在线/人脸、连续观测、媒体下载、FaceRec 原图残留、
镜像生命周期和迁移账本。本结果只证明本地实现与失败关闭边界，不代表远端适配器已经执行。

随后执行完整平台回归：

```bash
.venv/bin/python -m pytest -q tests
.venv/bin/pip check
```

结果为 `3073 passed, 3 skipped`、`No broken requirements found`。3 项 skip 均明确要求外部
`OPERATOR_REGISTRY_TOKEN` 才能运行 Canonical FaceRec 集成；它们保持未执行，不计入远端
FaceRec 三实例、共享 MongoDB 或原图残留通过证据。

目标机 Docker 26 的只读格式兼容检查为：`docker system df -v --format json` 共 475 个
Images 行、475 个唯一完整 `sha256:` ID、零个缺失 `UniqueSize`，与
`docker image ls --all --no-trunc --quiet | sort -u` 的 475 个 ID 一致。未执行镜像删除。

## 2026-08-25 Campaign 故障见证最终本地门禁

故障探针收口后从平台目录执行：

```bash
PYTHONPATH="$PWD:$PWD/.." .venv/bin/python -m pytest -q \
  tests/extreme_load/test_extreme_load_fault_probe.py \
  tests/extreme_load/test_production_fault_adapter.py \
  tests/extreme_load/test_production_adapters.py \
  tests/extreme_load/test_faults.py

PYTHONPATH="$PWD:$PWD/.." .venv/bin/python -m pytest -q \
  tests/extreme_load tests/deploy

.venv/bin/ruff check \
  deploy/scripts/extreme_load_fault_probe.py \
  scripts/extreme_load/production_fault_adapter.py \
  tests/extreme_load/test_extreme_load_fault_probe.py \
  tests/extreme_load/test_production_fault_adapter.py

MYPYPATH="$PWD/.." .venv/bin/mypy --strict --explicit-package-bases \
  deploy/scripts/extreme_load_fault_probe.py \
  scripts/extreme_load/production_fault_adapter.py \
  tests/extreme_load/test_extreme_load_fault_probe.py \
  tests/extreme_load/test_production_fault_adapter.py
```

结果分别为 `111 passed` 和 `432 passed`；Ruff、strict Mypy 与 `compileall` 通过。随后完整
平台回归为 `3214 passed, 3 skipped`，`pip check` 为 `No broken requirements found`。
3 项 skip 仍要求外部 Canonical FaceRec Token/容器，保持未执行。独立终审无 P0/P1/P2，
但本地假运行时不能替代最终 SHA 在 `192.168.29.11` 的真实 Docker 故障 Campaign。

## 2026-08-25 `e91f5b21` 发布与阶段 0 在线定位验证

本节绑定 release `v1.0_260825`、SHA
`e91f5b21cb458983f8ab1eea2518e33579f4836d`。在目标机只读复核 11.3–11.7 的关键证据：

```bash
REMOTE_RELEASE_ROOT=/root/workspace/algorithm-scheduling/algorithm-scheduling-platform/deploy/reports/milestone-2b/releases/v1.0_260825/e91f5b21cb458983f8ab1eea2518e33579f4836d

jq -e 'length == 11
  and ([.[].architecture] | unique) == ["amd64"]
  and ([.[].revision] | unique) == ["e91f5b21cb458983f8ab1eea2518e33579f4836d"]
  and ([.[].id] | unique | length) == 11' \
  "$REMOTE_RELEASE_ROOT/build/release-images.inspect.json"

jq -e '.status == "PASS"
  and .git_sha == "e91f5b21cb458983f8ab1eea2518e33579f4836d"
  and .summary.infrastructure == 4
  and .summary.platform_services == 4
  and .summary.operator_instances == 21
  and .summary.gpu_instances == 18
  and .summary.cpu_instances == 3
  and .summary.registered_instances == 21
  and .summary.active_leases == 0' \
  "$REMOTE_RELEASE_ROOT/production/production-stack-status.json"

jq -e '.git_sha == "e91f5b21cb458983f8ab1eea2518e33579f4836d"
  and .release_tag == "v1.0_260825"
  and (.containers | length) == 29' \
  "$REMOTE_RELEASE_ROOT/production/production-stack.json"

jq -e '.git_sha == "e91f5b21cb458983f8ab1eea2518e33579f4836d"
  and .summary.expected_ports == 29
  and .summary.observed_ports == 29
  and .summary.external_host_ports == [18100, 18103]
  and .summary.internal_binding_count == 27
  and .missing_listener_ports == []' \
  "$REMOTE_RELEASE_ROOT/preflight/port-boundary.json"

jq -e 'length == 7 and all(.[];
  .status == "通过"
  and .mock == false
  and .git_sha == "e91f5b21cb458983f8ab1eea2518e33579f4836d")' \
  "$REMOTE_RELEASE_ROOT/smoke/cases.json"

jq -e '.code == 0
  and .data.task_id == "deploy-smoke-ppt-e91f5b21cb45"
  and any(.data.tasks[];
  .task_type == "PPT" and .status == 60)' \
  "$REMOTE_RELEASE_ROOT/production/a-service-ppt-smoke.json"

jq -e '.status == "PASS"
  and .git_sha == "e91f5b21cb458983f8ab1eea2518e33579f4836d"
  and .release_tag == "v1.0_260825"
  and .mock == false
  and [.cases[].business_code] == [0, 40001, 40001]' \
  "$REMOTE_RELEASE_ROOT/online/online-ocr.json"
```

上述检查实际得到 11 个互异 `amd64`/同 revision 镜像、生产栈 `PASS`、29/29 端口、7/7
Smoke、PPT `60` 和 Online OCR `0/40001/40001`。四份关键发布证据的 SHA-256 前缀依次为
`6ac2aa34`、`e12410fb`、`070f3567` 和 `bd714358`，且均为 root 所有、`0600`、单硬链接。

负载机上的新 attempt 只读验证命令为：

```bash
ATTEMPT_ROOT="$PWD/deploy/reports/milestone-2b-load/v1.0_260825/e91f5b21cb458983f8ab1eea2518e33579f4836d/attempts/phase0-online-e91f5b21cb45-20260825001147"

jq -s -e 'length == 5 and all(.[];
  .release_tag == "v1.0_260825"
  and .git_sha == "e91f5b21cb458983f8ab1eea2518e33579f4836d"
  and .status == "passed"
  and .business_status == "passed"
  and .request_count == 1
  and .guardrail_before.level == "CLEAR"
  and .guardrail_after.level == "CLEAR"
  and .runtime_observability.status == "passed"
  and .runtime_observability.runtime_metrics.target_oom_delta == 0
  and .runtime_observability.runtime_metrics.max_kafka_lag == 0
  and .runtime_observability.runtime_metrics.max_outbox_pending == 0
  and all(.runtime_observability.runtime_metrics.container_restart_delta[]; . == 0))' \
  "$ATTEMPT_ROOT/campaign/phase-0-baseline/base-online-vbas.json" \
  "$ATTEMPT_ROOT/campaign/phase-0-baseline/base-online-face.json" \
  "$ATTEMPT_ROOT/campaign/phase-0-baseline/base-online-screen-det.json" \
  "$ATTEMPT_ROOT/campaign/phase-0-baseline/base-online-ocr.json" \
  "$ATTEMPT_ROOT/campaign/phase-0-baseline/base-asr-ws.json"
```

四个图片单请求的 `latency_seconds[0]` 分别为 `0.181751`、`0.069775`、`0.246660`、
`0.139112` 秒；ASR WebSocket 会话为 `464.222339` 秒、2294 个音频块、零失败会话、零缺失
终态。图片用例各有 2 个不可变指标样本，ASR 有 93 个。五案均通过，但
`192.168.29.12` 缺少 CPU、内存、发送网络和连接数源端遥测，故 4.9、11.1、四个媒体下载
baseline、`PHASE-0-COMPLETE` 和完整 12.1 仍为 blocked。

目标端媒体下载修正复跑使用同一 frozen manifest，验证命令为：

```bash
MEDIA_PARTIAL="$ATTEMPT_ROOT/preflight/media-download-baseline-partial-rerun1.json"

jq -e '.release_tag == "v1.0_260825"
  and .git_sha == "e91f5b21cb458983f8ab1eea2518e33579f4836d"
  and .status == "PARTIAL_BLOCKED"
  and .manifest_driven_range_probe.status == "REACHABLE"
  and ([.manifest_driven_range_probe.fixtures[].http_status] | all(. == 206))
  and [.tiers[].concurrency] == [1, 3, 10, 30]
  and ([.tiers[].attempt_count] | add) == 44
  and ([.tiers[].success_count] | add) == 44
  and ([.tiers[].failure_count] | add) == 0
  and .summary.successful_payload_bytes == 37788131032
  and .summary.target_network_receive_bytes == 40954896306
  and .source_resources.status == "NOT_COLLECTED"
  and all(.source_resources.metrics[]; .value == null and .status == "NOT_COLLECTED")
  and .task_eligibility.task_4_9_completed == false
  and .task_eligibility.task_11_1_completed == false
  and .task_eligibility.campaign_phase_0_admitted == false' \
  "$MEDIA_PARTIAL"

shasum -a 256 "$MEDIA_PARTIAL"
```

实际结果为 `44/44` 成功、失败率 0，payload 吞吐约 `116.97 -> 112.99 MB/s`，目标入站速率
约 `123.06–123.10 MB/s`。本地 attempt 和远端 release 的证据摘要均为
`aed4c897503c9a1c8ef7f0f3d919f8b421d897c70bb006791f810f4e762bcae3`，权限为 `0600`、
单硬链接，敏感扫描和样本尺寸校验通过。首份 `6a7b34f1...` partial 不覆盖；新证据明确只
修正其下载/404解释，不修正源端四项遥测缺失，因此阶段 0 准入仍为 blocked。

Harness 记录同步后从平台目录执行：

```bash
.venv/bin/python -m pytest -q tests/test_harness_consistency.py
(cd .. && openspec validate run-milestone-2b-extreme-load-campaign --strict)
git diff --check -- \
  harness/scenarios/milestone-2b-extreme-load-campaign.md \
  harness/verification.md \
  harness/change-ledger.md \
  ../openspec/changes/run-milestone-2b-extreme-load-campaign/tasks.md
```

结果为 Harness consistency `5 passed`、OpenSpec strict `valid`、`git diff --check` 零输出。

## 2026-08-21 七算子当前验收入口

当前发布范围由
[`scenarios/milestone-2b-seven-operator-release.md`](scenarios/milestone-2b-seven-operator-release.md)
和 `deploy/operator-topology.json` 共同定义：7 类算子、21 个实例、18 个 GPU 实例、3 个
CPU PPT Slice 实例、14 个配置解析进程和 7/7 综合 Smoke。唯一正式总控入口是：

```bash
deploy/scripts/run-milestone-2b-8a7 \
  --teacher-video-url "$TEACHER_VIDEO_URL" \
  --student-video-url "$STUDENT_VIDEO_URL" \
  --slides-video-url "$SLIDES_VIDEO_URL" \
  --manual-review-json \
    "/root/workspace/.algorithm-scheduling-restricted-reports/${EXPECTED_GIT_SHA}-b-level-reviews.json"
```

`text_analysis/` 只作为非平台源码项目保留，不进入当前 clean clone 的算子合同、配置权威、
构建、注册、租约或 Smoke。本文后续注明“历史八算子”的命令、数量和 release 只用于复现
当时事实，不得作为当前发布入口或补足七算子验收。

## 2026-08-19 统一算子配置、容量租约、在线 OCR 与镜像清理实施门禁

本节对应 `scenarios/unified-operator-capacity-leases-and-online-ocr.md`。当前已取得本地静态、
单元、真实 Redis 和算子项目测试证据；最终提交后仍必须从相应目录重跑，并把完整输出保存到
`harness/reports/unified-operator-capacity-leases-and-online-ocr/{完整GitSHA}/`。

## 2026-08-21 三路课程媒体预检与失败关闭

release `99e0f9aeca14fda1679410a31b05e57bac1e936e` 已通过 `93/93` deployment
用例，但真实课程的 P/S 下载在后台处理时收到 HTTP `404`。ASR、课程脑图和教师行为完成，
PPT Slice 进入失败终态，学生视频准备保持重试；因此本 SHA 未发布 8 项 B 级复核索引，
不得计入 OpenSpec `14.3-14.7`。服务器恢复后 24 个测试算子均为 Exited，四平台与四基础设施
healthy，原 `ocr-v6-amd` 保持 `Exited(143)`，唯一权威审计为
`existing-containers.jsonl.paused.jsonl.audit.f25ccdfe5eab4b6daa86061574653cbb.jsonl`。
该审计为空是因为快照中的原容器本来就是 Exited，并非恢复证据缺失。PostgreSQL 的
`course_task_types.request_payload` 进一步确认，本 release 提交的 PPT URL 缺少 T/S 所在
课程目录的时间片段；使用修正后的同课程 T/S/P 地址从 Orchestrator 容器只读探测时三路均
返回 `206`、`Content-Length=1048576` 且首块长度为正。旧任务终态保持不变。

修复后的 Canonical 在 deployment 与 offline Campaign 之间执行：

```bash
deploy/scripts/preflight-course-media \
  --release-root "$RELEASE_ROOT" \
  --media-json-stdin \
  --attempts 3 \
  --request-timeout-seconds 30 \
  --retry-interval-seconds 2 <<MEDIA_JSON
{"teacher_video_url":"$TEACHER_VIDEO_URL","student_video_url":"$STUDENT_VIDEO_URL","slides_video_url":"$SLIDES_VIDEO_URL"}
MEDIA_JSON
```

命令必须在 `orchestrator-service` 容器内并发读取三路首块，三轮每一路都要求 HTTP
`200/206`、正 `Content-Length` 和正实际读取长度。结果以 URL 摘要写入
`preflight/course-media.json`；失败时不得创建课程任务。容器超时/不可用、空或异常 stdout、
退出码矛盾都必须先写脱敏失败证据。最终 aggregator 缺少该文件、身份不符、不是固定三轮、
角色不完整、同一角色摘要跨轮漂移或任一状态/长度失败时，不得发布 `summary/cases.json`。
宿主还必须把每项摘要与实际输入 URL 对账；外层 Canonical runtime 使用匿名受控脚本文件，
避免完整 runtime 出现在 Bash argv，也不占用可被部署子进程消费的 stdin。当前本地验证：

```bash
PYTHONPATH="$PWD:$PWD/.." .venv/bin/python -m pytest -q \
  tests/test_preflight_course_media.py tests/test_run_8a7.py
# 26 passed

.venv/bin/ruff check \
  deploy/scripts/preflight_course_media.py \
  deploy/scripts/run_milestone_2b_8a7.py \
  tests/test_preflight_course_media.py tests/test_run_8a7.py

PYTHONPATH="$PWD:$PWD/.." .venv/bin/mypy \
  deploy/scripts/preflight_course_media.py \
  deploy/scripts/run_milestone_2b_8a7.py

bash -n deploy/scripts/preflight-course-media deploy/scripts/run-milestone-2b-8a7
```

媒体、总控、聚合及锁边界定向回归为 `584 passed`；平台全量为
`2709 passed, 3 skipped, 27 warnings`。3 个 skip 只要求本机不存在的 Canonical FaceRec
Token/容器，远端不得跳过；warnings 是既有多线程进程中 `fork()` 的 Python 弃用提示。

这些本地结果只证明参数、顺序、证据发布和失败关闭逻辑；真实容器访问与后续全泳道仍需新
完整 SHA 在 `192.168.29.11` 重跑 Canonical。

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

真实 full-course Campaign 暴露 Vision Orchestrator 无界 ffmpeg 抽帧并发会在 `4G` cgroup 内
触发 OOM。修复后的本地门禁为：

```bash
(cd ../vision_orchestrator_service && \
  ../algorithm-scheduling-platform/.venv/bin/python -m pytest -q \
    tests/test_media.py tests/test_service_project.py tests/test_runtime.py)

PYTHONPATH="$PWD:$PWD/.." .venv/bin/python -m pytest -q \
  tests/test_platform_compose.py
```

该命令只证明配置、跨 T/S 共享并发上限和 Compose `/ready` 探针；最终仍需在远端 `4G`
Vision 容器内重跑真实 T/S 长视频，并确认任务完成、`/ready` 持续成功且 dmesg 没有新 cgroup OOM。

`c07df67910558716985941bb2feff73b637bd844` 在平台先于 VBas 启动时处理上轮未完成视觉命令，
租约申请 HTTP `503` 导致 Consumer 退出。该 release 已完成 `restore: complete`，维护锁也已释放，
但不能计入 OpenSpec 12.9/14.x。容量等待本地门禁为：

```bash
(cd ../vision_orchestrator_service && \
  ../algorithm-scheduling-platform/.venv/bin/python -m pytest -q \
    tests/test_capacity.py tests/test_runtime.py)
```

测试必须覆盖容量 `503` 与注册中心不可用 `503` 的分类、不提交 offset 的重试、等待期间 Consumer 仍存活，
以及关闭信号终止等待且不提交消息。最终 SHA 的远端 8A.7 还必须验证：先启动四个平台服务、
后启动 VBas 的固定顺序不会让 Vision 变为 unhealthy，且保留命令在 VBas 注册后继续处理。

`bec262b46bd7f570e43dc1a74b5f7e336f935084` 的真实课程粗扫生成 31 个候选窗口，
超过旧默认 `20`，因此该 release 不得计入 14.3。候选窗口与中断恢复本地门禁为：

```bash
PYTHONPATH="$PWD:$PWD/.." .venv/bin/python -m pytest -q \
  tests/test_adaptive_vision_scan.py \
  tests/test_run_8a7.py
```

测试必须证明默认 `128` 可处理 31 个候选窗口、第 129 个仍失败关闭；并向总控发送
`SIGINT`，确认长子进程已终止、锁持有进程在外层 Bash `EXIT` trap 开始时仍存活，且 Python 等待 trap 完成后才返回。最终远端结论仍必须来自
新 SHA 的完整 8A.7，旧 release 仅是诊断与精确恢复证据。

七算子本地安全/受控部署 TOML 的进程级权威对照使用独立探针。它为每个算子的两类配置分别启动
一个子进程，在子进程中确认五个已迁移旧环境变量确实存在后，通过显式 `CONFIG_PATH` 调用对应
算子的正式配置加载入口；不导入 `app.main`，因此不会启动模型、连接数据库或占用 GPU。FaceRec、
OCR 使用受版本控制且不含真实凭据的安全模板，clean clone 不依赖被忽略的运行
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

通过条件为 `operator_count=7`、`process_count=14`、全部结果与确认容量一致，根配置均为
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
RESTRICTED_REVIEW_ROOT=/root/workspace/.algorithm-scheduling-restricted-reports
deploy/scripts/run-milestone-2b-8a7 \
  --teacher-video-url "$TEACHER_VIDEO_URL" \
  --student-video-url "$STUDENT_VIDEO_URL" \
  --slides-video-url "$SLIDES_VIDEO_URL" \
  --manual-review-json "$RESTRICTED_REVIEW_ROOT/${EXPECTED_GIT_SHA}-b-level-reviews.json"
```

`b-level-reviews.json` 必须覆盖 6 个 B 级质量复核 case，并让每项 `artifact` 指向当前 release
内已存在的复核证据；它不能只写统一阶段结论。Campaign 会在当前课程离线结果完成后发布
`business/review-requests/offline.json` 并等待 5 项复核，在视觉结果完成后发布
`business/review-requests/vision.json` 并等待 `VIS-025`。独立复核输入必须位于 Git 外受限目录、
权限 `0600`，然后通过以下入口发布；发布器先写当前 SHA/课程的逐项证据，再原子更新索引：

```bash
deploy/scripts/publish-milestone-2b-b-level-reviews \
  --release-root "$RELEASE_ROOT" \
  --index "$RESTRICTED_REVIEW_ROOT/${EXPECTED_GIT_SHA}-b-level-reviews.json" \
  --review-document "$RESTRICTED_REVIEW_ROOT/${EXPECTED_GIT_SHA}-review-input.json"
```

发布器只接受已经出现的当期 request：offline 输入必须一次包含
`PPT-012/PPT-013/PPT-014/ASR-012/ASR-013`，vision 输入只能在 vision request 出现后单独包含
`VIS-025`。`reviewed_at` 必须是带时区的 ISO 8601 时间；`reviewer` 必须是可追溯的独立复核身份，
不能使用 `controller`、`canonical`、`系统` 等空泛值。逐案 `observed` 固定为：

```text
PPT-012: reviewed_start_slice_count, black_start_false_positive_count
PPT-013: reviewed_dynamic_segment_count, slice_count_in_dynamic_segments,
         obvious_burst_false_positive_count
PPT-014: reviewed_stable_page_count, obvious_missed_stable_page_count
ASR-012: reviewed_audio_seconds, reviewed_segment_count, obvious_omitted_span_count
ASR-013: reviewed_bilingual_segment_count, severe_error_segment_count
VIS-025: paired_evidence_count, mismatch_count
```

计数必须是非负整数，时长必须是有限非负数。原视频、图片和完整 ASR/OCR 文本只在 Git 外受限
位置供复核，不写入普通 release；人工观察计数写入上述固定 `observed`。`evidence` 使用
`release:<相对路径>#sha256:<64位小写摘要>` 引用当前 release 已经发布的 `0600` 身份/运行证据，
例如当期 `business/review-requests/{offline,vision}.json`、`preflight/course-media.json` 或已存在的
运行摘要；发布器会重新读取文件并核对权限和摘要。不得绕过 canonical publication allowlist
临时创建未定义的 `business/review-materials/` 目录。

复核输入顶层只包含 `git_sha`、`task_id`、`reviews`；每个 review 必须包含 `status=通过`、
`reviewer`、`reviewed_at`、`review_scope`、`method`、非空 `observed`、非空 `evidence` 和
`conclusion`。课程联系表、证据图片及识别全文不得写入普通 release JSON，普通证据只保存摘要、
SHA-256 和受限证据编号。

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

## 当前里程碑 2B 七算子部署验证场景

当前完整执行顺序、证据目录和终态边界见
[`scenarios/milestone-2b-seven-operator-release.md`](scenarios/milestone-2b-seven-operator-release.md)，
由 `deploy/scripts/run-milestone-2b-8a7` 读取当前场景并复用下述已经审计的生命周期脚手架。
当前数量、算子集合和禁止标识必须从 `deploy/operator-topology.json` 派生，不能从历史段复制。

## 历史：里程碑 2B 八算子部署验证场景

> **后续范围调整已废止（2026-08-21）：** 本节保留八算子、24 实例、Text Analysis 和旧
> Canonical 命令的历史事实。它所引用的 `milestone-2b-deploy.md` 不再是当前正式入口；当前
> 发布必须使用上一节的七算子场景和 `run-milestone-2b-8a7`。

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
deploy/scripts/apply-course-task-submission-migration
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

## `run-milestone-2b-extreme-load-campaign` 初始基线

从工作区根目录记录本地版本、用户 dirty 文件和两个前置变更进度：

```bash
git branch --show-current
git rev-parse HEAD
git status --short
openspec instructions apply --change standardize-service-file-logging --json
openspec instructions apply --change retire-text-analysis-from-scheduling-platform --json
```

目标服务器初始盘点必须是只读命令，只记录主机/Docker/GPU/端口/共享目录和 checkout；不得在同一
步骤执行停止、删除、重标、prune 或目录清理。2026-08-23 的机器可读摘要位于
`harness/baselines/milestone-2b-extreme-load-campaign-initial.json`，详细边界位于
`harness/scenarios/milestone-2b-extreme-load-campaign.md`。

当前盘点结论是目标机根盘仅剩约 103 GB、7%，已触发磁盘红线；因此这份证据只证明基线，不允许
进入远端负载。后续必须先生成可审核的精确镜像清理 dry-run，恢复到警戒线以上后再执行阶段 0。

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

## 2026-08-23 里程碑 2B 部署手册 Git 准备验证

在 `algorithm-scheduling-platform/` 执行：

```bash
.venv/bin/python -m pytest -q tests/deploy/test_deployment_runbook.py
(cd .. && openspec validate run-milestone-2b-extreme-load-campaign --strict)
git diff --check
```

静态门禁要求手册同时覆盖首次 clone、已有工作树 fetch、完整 SHA detached checkout、
HEAD 等值和切换前后 clean-worktree 检查，并精确选择 Git 外的 Deploy Key、校验
`origin`、直接 fetch 批准 SHA 且以 `set -euo pipefail` 失败即停。该测试不访问
GitHub，远端复现仍需在任务 11.1 中产生当前 SHA 证据。

## 2026-08-23 host preflight Git 状态失败关闭

在 `algorithm-scheduling-platform/` 执行：

```bash
.venv/bin/python -m pytest -q tests/test_milestone_2b_scripts.py \
  -k 'preflight and (git or exactly_three or explicit_host)'
.venv/bin/ruff check tests/test_milestone_2b_scripts.py
bash -n deploy/scripts/preflight
git diff --check
```

预期聚焦回归为 `8 passed`。新用例让 fake `git status --porcelain` 返回非零，要求
preflight 明确报“Git working tree inspection failed”且不得输出 host PASS。远端 NVIDIA
Runtime 注册、8/8 原容器恢复与 3/3 GPU 容器可见性是当次目标机运行证据；根盘红线和
最终 SHA preflight 仍未解除。

## 2026-08-24 构建前镜像清理与磁盘门禁

目标机使用计划 SHA-256
`2fd76c3646477d90fa32a1e2330237a6d32f383cf257f2ad0eac3c0f0ed1504d` 执行
`production-image-lifecycle execute`。结果账本 `status=PASS`，396 个候选镜像均按完整 ID
删除，镜像总数由 475 降为 79；四平台和四中间件保持 8/8 healthy，三张 GPU 与 NVIDIA
Runtime 保持可用。

执行后的 `df -B1 /` 仍只有 `110115663872` 字节可用；`docker buildx du` 报告 Shared
74.19 GB、Private 234 GB、Reclaimable 308.2 GB。这证明镜像差集实现和删除执行通过，但
Build Cache 仍引用相关层，磁盘警戒线没有解除。因此 OpenSpec 11.2 仍为未完成，11.3 构建
不得开始；本次没有执行缓存 prune、卷删除或持久目录清理。

经用户逐次批准后，目标机执行：

```bash
docker buildx prune --all --force --keep-storage 100GB
```

命令退出码为零；清理前后 BuildKit 总可回收分别为 308.2 GB 和 162 GB，根盘最终可用
`249091776512` 字节（231.98 GiB/15.35%）。缓存操作开始和结束的 76 个镜像完整 ID及 8 个
运行容器清单一致，8/8 healthy；当前/回滚各 11 个镜像及 3 个基础镜像均存在，NVIDIA
Runtime 容器探针返回三个 GPU UUID。因此任务 11.2 的磁盘门禁已解除。

原镜像清理结果与缓存清理之间存在外部状态变化：一个停止的 `vllm-qwen3-8b` 容器、其
`vllm/vllm-openai:v0.9.2` 镜像和两个无关镜像在缓存操作开始前已不存在。VLLM 只因该停止
容器引用进入原保护集，不属于当前发布、回滚或基础镜像；清理前后镜像清单一致，故不把该变化
归因于 BuildKit 命令，也不擅自恢复非平台资源。

随后以 clean SHA `0e11d3d70fd43d49f43dac44a6f8eec97f3782a1` 和
`MIN_ROOT_FREE_GIB=227` 执行七算子构建。ASR Offline 成功生成
`sha256:23091a1b326309e56acf37a43a1470896d77f35d3f5be10e10fc992ce4930cb6`；镜像为
`amd64` 且 revision label 与 SHA 一致。下一镜像开始前，构建入口返回：

```text
deployment-contracts: FAIL:
root disk has 229075452 KiB free; 227 GiB required
```

终态根盘约 218.46 GiB/14.46%，没有残留构建进程。8 个既有运行容器保持 healthy，未发现
OOM/NVIDIA Xid；但目标镜像集合仅 ASR Offline 属于新 revision，其余 10 个仍为旧 revision。
因此 11.3 未完成，禁止启动部分发布。再次执行 BuildKit 缓存清理需取得新的逐次明确授权。

用户再次逐次批准相同固定命令后，当前 SHA release 的 `cleanup/buildkit-prune-2/` 保存了新的
执行前后证据。命令退出码为零，报告回收 20.45 GB；普通镜像 ID 保持 78 个、运行容器保持
8 个，Runtime 与 3 GPU 文件逐字节一致。根盘从 `234552823808` 增至 `254694129664` 字节。

随后在 clean detached SHA `22717cf7abb584bb1891d86c89e215729ee48955` 执行七算子统一构建，
并逐个构建四平台。`build/operators.inspect.json` 与 `build/release-images.inspect.json` 证明
11 个完整镜像 ID 互异、架构均为 `amd64`、revision 均精确匹配；
`build/image-filesystem-verification.txt` 证明 11 个镜像均预创建项目根 `logs/`，六类模型资产
目录非空。`build/rollback-images.inspect.json` 证明旧 11 镜像继续存在且为 `amd64`。

构建终态根盘为 `245348466688` 字节可用，约 228.49 GiB/15.12%；8 个原运行容器仍全部
healthy，未观测到 OOM/NVIDIA Xid。本证据完成 OpenSpec 11.3，不代表 11.4 的 21 实例启动、
注册、GPU 真实进程或 Smoke 已通过。

## 2026-08-25 迁移账本旧库连续前缀采纳

在 `algorithm-scheduling-platform/` 执行：

```bash
PYTHONPATH="$PWD:$PWD/.." .venv/bin/python -m pytest -q \
  tests/deploy/test_migration_executor.py \
  tests/deploy/test_production_stack.py \
  tests/deploy/test_deployment_runbook.py \
  tests/test_harness_consistency.py
PYTHONPATH="$PWD:$PWD/.." .venv/bin/python -m pytest -q -rs \
  tests/integration/test_migration_executor_runtime.py
.venv/bin/ruff check deploy/scripts/migration_executor.py \
  deploy/scripts/production_stack.py tests/deploy/test_migration_executor.py \
  tests/deploy/test_production_stack.py tests/integration/test_migration_executor_runtime.py
MYPYPATH="$PWD/.." .venv/bin/mypy --strict --explicit-package-bases \
  deploy/scripts/migration_executor.py deploy/scripts/production_stack.py \
  tests/deploy/test_migration_executor.py tests/deploy/test_production_stack.py \
  tests/integration/test_migration_executor_runtime.py
.venv/bin/python -m compileall -q deploy/scripts tests/deploy \
  tests/integration/test_migration_executor_runtime.py
```

结果分别为 `31 passed`、`22 passed`且无 skip、Ruff 通过、strict Mypy 通过和
`compileall` 通过。集成用例使用真实 PostgreSQL 容器和随机
`algorithm_migration_<uuid>_test` 库，每例终态精确删除该测试库；未读写本地 `algorithm`
业务表或远端业务库。覆盖空库、v6/v7、环境 `search_path`、非 `public` 账本、畸形空账本、
索引、排序规则、表访问方法、序列依赖/持久性/并发 `setval()`/identity 位置与上界、账本锁内二次校验、新/旧并发 DDL 顺序、多行注释和 `submission_id` 数据不变量。实际远端采纳/v7 与常驻
启动尚未执行，本节的最高证据级层为 3。

远端只读 psql 追加核对返回：`public.algorithm_schema_migrations=0`、
`course_jobs=18`、`course_task_types=45`、全零 `submission_id=0`、跨课程复用
`submission_id=0`，且不存在非 `public` 同名账本。该证据只证明远端满足采纳前数据门禁，
不代表采纳、`0007` 或常驻启动已执行。

## 2026-08-25 阶段 0 下载与 PPT 轮询中断核验

当前 SHA `e91f5b21cb458983f8ab1eea2518e33579f4836d`、attempt
`phase0-online-e91f5b21cb45-20260825001147` 的四份正式下载证据均为非 symlink `0600`
单链接文件。其内嵌同一源端遥测标识
`source-fileserver-media-download-rerun2-20260825T103743+0800`，证明 `.12` 的 CPU、内存、
发送网络和连接数正式遥测已经可用；四档结果为 `1/1`、`3/3`、`10/10`、`30/30`，合计
`44/44` 成功。旧 partial 证据中 `NOT_COLLECTED` 的历史陈述不作改写。

`BASE-OFFLINE-PPT` 目录保留连续 `00000001.json` 至 `00000053.json`，共 53 份 `0600`
单链接运行指标；首末时间为 `2026-08-25T03:02:59.635483Z` 和
`2026-08-25T03:07:39.361109Z`，护栏均为 `CLEAR`。规范
`campaign/phase-0-baseline/base-offline-ppt.json` 不存在。对确定性任务 ID 执行一次只读
`GET /api/course-jobs/{task_id}` 并只选择状态字段，得到 `PPT=60`、两个 PPT 节点均为 `60`，
其余三个未请求固定槽位均为 `0`；这证明业务成功，也证明旧 Runner 因错误等待 `status=0`
槽位而中止发布 case 证据。脱敏中断记录为 attempt 根的
`attempt-interruption-offline-polling.json`，不含响应正文、媒体、文本、凭据或源端密码。

轮询修复验证结果为：新增聚焦用例 `6 passed`、完整 `test_execution.py` 为 `18 passed`、
完整 `tests/extreme_load` 为 `375 passed`；Ruff、strict Mypy、`compileall` 和
`git diff --check` 均通过。该核验达到真实北向业务终态的验证层级 6，但没有生成规范 PPT
case 证据；`BASE-OFFLINE-ASR`、`BASE-OFFLINE-TEACHER`、`BASE-OFFLINE-STUDENT` 未开始，
所以不得发布 `PHASE-0-COMPLETE` 或完整阶段 0 通过结论。

## 2026-08-25 PPT EOF、视觉失败终态和新 fixture 验证

从工作区根分别执行：

```bash
conda run -n ppt_slice python -m pytest -q
algorithm-scheduling-platform/.venv/bin/python -m pytest -q \
  vision_orchestrator_service
algorithm-scheduling-platform/.venv/bin/python -m pytest -q \
  orchestrator_service
MYPYPATH=algorithm-scheduling-platform \
  algorithm-scheduling-platform/.venv/bin/mypy --strict --explicit-package-bases \
  orchestrator_service/app/application/vision_events.py \
  orchestrator_service/tests/test_visual_runtime.py
```

结果依次为 PPT `104 passed`、Vision `44 passed`、Orchestrator `63 passed`，视觉修改源文件
和 Orchestrator 视觉事件文件 strict Mypy 通过；三项目 `compileall` 和 `app.main:app` 导入通过。
PPT 环境没有安装 Ruff，平台 Ruff 对 PPT 文件还会报告该项目既有风格债务，因此不把本轮结果
扩写为 PPT Ruff 全通过。

Git 工作区外 `fixture-manifest.yaml` 的三条短媒体已切换为同一课程 50.040 秒 T/S/P，并通过
`create-plan` schema/摘要校验。T/S/P 完整 SHA-256 为
`4b63885bcefb15cd3bdf9dec52c267b6b50bf63a58c4e9a1c93ff3dc76eff4e4`、
`b9819f5aef0fb2b193daef7d6213ea982f25436623692fbd4538bdf9f571e440`、
`f91ef623f0a62de6acdb5f578ac15b1afd9fe4574a079433c1d240da3dcfd775`，manifest SHA-256 为
`51ee3f8c1244fa08dc1566b6ff5f43fec35845adcce65d644e7568ba082ecedb`。`.12` 源文件只读
`stat`/`sha256sum` 与 manifest 一致；`.12` 上已完成 ffprobe、完整解码和末端抽帧，`.11`
宿主机与 `orchestrator-service` 容器对三条 URL 的首个 4096 字节均返回 HTTP `206` 且内容
摘要一致。新 T 片段的北向 ASR-only 任务返回平台业务码 `0`、任务/节点状态 `60`、结果标准
字段和 `23` 个 segments；核验命令只选择这些字段，没有输出或持久化完整转写文本。

从工作区根运行平台完整测试，结果为 `3223 passed, 3 skipped`，三个 skip 均因本机没有
canonical FaceRec GPU 容器。视觉普通单任务错误与进度基础设施异常隔离的聚焦测试为
`21 passed`，视觉事件源文件 strict Mypy 和服务 `compileall` 通过。根目录直接运行 Vision
项目全量测试会因项目合同要求的顶层 `app` 导入路径产生 8 个环境型失败；按项目根目录并设置
`PYTHONPATH=../algorithm-scheduling-platform` 的权威命令重跑为上述 `44 passed`，前一命令不作
代码失败结论。本节只完成实现与 fixture 前置，不能替代新 SHA 的 11 镜像 attestation、
29/29 healthy、阶段 0 规范 case 或后续 Campaign。

## 2026-08-25 `0ebaa126` 发布闭环核验

目标机 release 根：

```bash
REMOTE_RELEASE_ROOT=/root/workspace/algorithm-scheduling/algorithm-scheduling-platform/deploy/reports/milestone-2b/releases/v1.0_260825/0ebaa126f69e3993487c503c11b42e681cad12cd

jq -e 'length == 11
  and ([.[].architecture] | unique) == ["amd64"]
  and ([.[].revision] | unique) == ["0ebaa126f69e3993487c503c11b42e681cad12cd"]
  and ([.[].id] | unique | length) == 11' \
  "$REMOTE_RELEASE_ROOT/build/release-images.inspect.json"

jq -e '.status == "PASS"
  and .git_sha == "0ebaa126f69e3993487c503c11b42e681cad12cd"
  and .summary.infrastructure == 4
  and .summary.platform_services == 4
  and .summary.operator_instances == 21
  and .summary.gpu_instances == 18
  and .summary.cpu_instances == 3
  and .summary.registered_instances == 21
  and .summary.active_leases == 0' \
  "$REMOTE_RELEASE_ROOT/production/production-stack-status.json"

jq -e '.status == "PASS"
  and .summary.compute_processes == 18
  and .summary.mapped_containers == 18
  and .summary.processes_per_gpu == {"0":6,"1":6,"2":6}
  and .summary.cpu_ppt_instances == 3' \
  "$REMOTE_RELEASE_ROOT/runtime/gpu-process-cgroup-map.json"

jq -e 'length == 7 and all(.[];
  .status == "通过" and .mock == false
  and .git_sha == "0ebaa126f69e3993487c503c11b42e681cad12cd")' \
  "$REMOTE_RELEASE_ROOT/smoke/cases.json"
```

实际结果为 11/11 镜像、29/29 healthy、21/21 注册、18 GPU PID/cgroup、3 CPU PPT 和
7/7 Smoke 全部通过。三台 PPT 逐实例 `cases.json` 也分别为非 mock 通过。关键证据 SHA-256
前缀为 `f30ad00d/f3ba0daf/cc1f9266/f43fe846/aeeda69e/eedbd48a/9cdf06d9`，均为普通
`0600` 单链接文件。该核验完成 OpenSpec 11.8，但不替代新的阶段 0 attempt。

## 2026-08-25 `0ebaa126` 阶段 0 全量核验

当前 attempt 根：

```bash
ATTEMPT_ROOT="$PWD/deploy/reports/milestone-2b-load/v1.0_260825/0ebaa126f69e3993487c503c11b42e681cad12cd/attempts/phase0-rerun-0ebaa126f69e-20260825144344"

find "$ATTEMPT_ROOT/campaign/phase-0-baseline" \
  -maxdepth 1 -type f -name '*.json' -print0 |
  xargs -0 jq -s -e '
    length == 14
    and ([.[].case_id] | unique | length) == 14
    and all(.[]; .status == "passed")'

jq -e '
  .status == "passed"
  and .business_status == "passed"
  and .request_count == 1
  and .extra.sent_chunks == 2294
  and .extra.failed_session_count == 0
  and .extra.missing_final_message_count == 0
  and .guardrail_before.level == "CLEAR"
  and .guardrail_after.level == "CLEAR"' \
  "$ATTEMPT_ROOT/campaign/phase-0-baseline/base-asr-ws.json"
```

实际结果为 14/14 `passed`。正式媒体下载四档共 44/44 成功、零失败，聚合吞吐约
`117.62/117.05/114.78/115.83 MB/s`；工作区外源端证据包含 210 个连续样本和 44/44
calibration 下载，严格峰值为 30 个连接、CPU 2.33% 和发送 123.10 MB/s。PPT、ASR、教师、
学生离线单泳道与四类在线图片均业务通过；实时 ASR 为 464.12 秒、2294 块、73 个运行指标
样本，零失败会话和零缺失终态。该证据完成 OpenSpec 12.1，不替代阶段 1–6 或最终 217/26/6
门禁。

## 2026-08-25 catalog 阶段 1 的 PPT 唯一提交 100/300 档诊断核验

以下命令只读检查 `0ebaa126` 的 write-once attempt，不修改 case 或运行时样本：

```bash
ATTEMPT_ROOT="$PWD/deploy/reports/milestone-2b-load/v1.0_260825/0ebaa126f69e3993487c503c11b42e681cad12cd/attempts/phase0-rerun-0ebaa126f69e-20260825144344"

for CASE_ID in OFF-UNIQUE-PPT-100 OFF-UNIQUE-PPT-300; do
  jq -s '
    group_by(.guardrail.level)
    | map({
        level: .[0].guardrail.level,
        count: length,
        reasons: ([.[].guardrail.reasons[]] | unique)
      })' \
    "$ATTEMPT_ROOT/campaign/runtime-metrics/$CASE_ID"/[0-9]*.json
done

sha256sum \
  "$ATTEMPT_ROOT/campaign/phase-1-offline/off-unique-ppt-100.json" \
  "$ATTEMPT_ROOT/campaign/phase-1-offline/off-unique-ppt-300.json" \
  "$ATTEMPT_ROOT/campaign/runtime-metrics/OFF-UNIQUE-PPT-300/00000016.json"

jq '{case_id,status,business_status,elapsed_seconds,request_count,terminal_counts,
     guardrail_before,guardrail_after,
     runtime_summary:.runtime_observability.runtime_metrics}' \
  "$ATTEMPT_ROOT/campaign/phase-1-offline/off-unique-ppt-300.json"

ssh -o BatchMode=yes root@192.168.29.11 \
  "docker exec algorithm-scheduling-platform-postgres-1 \
   psql -U algorithm -d algorithm -Atc \\
   \"SELECT n.status,t.status,count(*) \
      FROM task_nodes n \
      JOIN course_task_types t ON t.id=n.course_task_type_id \
     WHERE n.status BETWEEN 10 AND 50 \
     GROUP BY n.status,t.status \
     ORDER BY n.status,t.status\""
```

实际结果：

- PPT-100 护栏聚合为 `CLEAR=9`，case 为 100/100 成功、`72.487953s`，SHA-256 为
  `fbb176ed52fc0c16d929c01b7a343c207fe1c2b69cfa34575c1b2b547319b9d0`。
- PPT-300 原始样本聚合为 `CLEAR=16, STOP=1`；STOP 原因是
  `关键容器不健康或缺失: orchestrator-service`。case 为 300/300 最终成功、`686.763116s`，
  但旧汇总仍写成 `passed/CLEAR`；case 与 STOP 样本 SHA-256 分别为
  `b3a6cdc9a9739bbe8ca0c9487fd1698195388d2405be8e9633426169f787d7d8`、
  `dfb2cf0b9cb84c6461d61d1e0a055e51df986d58e0352243f939e25fda1403e7`。
- PostgreSQL 联表输出为 `20|70|7`，证明旧快照中的队列 7 全部属于已失败父任务下的历史节点，
  不是活动工作。

`OFF-UNIQUE-PPT-100/300` 虽位于 catalog 的 `phase-1-offline` 目录，但业务语义属于 OpenSpec
12.3 的唯一提交，只能作为 12.3 的部分诊断，不能补足该任务。PPT-300 不能判为通过，且
中途 `STOP` 使当前 attempt 不能进入任何更高执行。OpenSpec 12.2 的四条单泳道和
3/6/12/24/36 长课阶梯尚未执行，因此保持未完成而不是判定失败。
修复门禁为：全过程任何 `WARNING/STOP` 使 case 阻断；活动队列只统计节点及父任务均为
`10`–`50`；PPT 身份落库竞态不终止后台循环。修复形成新 SHA 后必须重新构建 11 个镜像，
创建新 write-once attempt 并从阶段 0 重跑。

## 2026-08-25 `.12:5556` 受控慢媒体探针核验

远端脚本和容器使用如下非敏感只读事实复核；SSH 密码不进入命令、Git 或报告：

```bash
sha256sum /root/workspace/algorithm-scheduling-campaign/slow_media_fixture_server.py

docker inspect campaign-slow-media --format \
  '{{.Id}} {{.State.Running}} {{.State.ExitCode}} {{json .Config.Labels}} {{json .HostConfig.PortBindings}}'

curl -fsS --max-time 2 http://192.168.29.12:5556/healthz
curl --range 0-0 --max-time 2 -sS -o /dev/null \
  -w 'probe_status=%{http_code} elapsed=%{time_total}\n' \
  http://192.168.29.12:5556/timeout.mp4
curl --max-time 10 -sS -o /dev/null \
  -w 'terminal_status=%{http_code} elapsed=%{time_total}\n' \
  http://192.168.29.12:5556/timeout.mp4
curl -fsS --max-time 2 -o /dev/null -w 'fileserver_status=%{http_code}\n' \
  http://192.168.29.12:5555/course/
```

实际结果为脚本 SHA-256
`25549bfdc3484c9e7644265d94e96358c3a1f432341896f4f6d4923aa8b832a5`；容器完整 ID
`769b1176d15900ced01a63c8ceadab62422c31401a78512ed97a785e08343b27`，running、exit code 0，
唯一 Campaign label 正确，只绑定 `.12:5556->8080`。健康检查返回 `ok`；2 秒探测为
HTTP `000`、exit 28、`2.001411s`；完整慢响应为 HTTP 504、`5.006082s`；既有 5555 为 200。

完成负向用例后，必须先按容器 name、label 和完整 ID 二次 inspect，再只执行
`docker rm -f <完整容器ID>`，最后确认 5556 无监听且 5555 仍为 200。当前验证只完成慢 fixture
前置，不代表 OpenSpec 12.2、12.3 或任何更高阶段完成。

## 2026-08-25 阶段 1 诊断修复最终本地回归

从 `algorithm-scheduling-platform/` 使用绝对路径运行，避免临时测试目录中的同名模块污染
标准库，也避免四个顶层 `app` 包在同一 pytest 进程冲突：

```bash
PYTHONPATH="/Users/zhangshen/Documents/workspace/算法功能调度/algorithm-scheduling-platform:/Users/zhangshen/Documents/workspace/算法功能调度" \
  .venv/bin/python -m pytest -q -rs tests

PYTHONPATH="/Users/zhangshen/Documents/workspace/算法功能调度/algorithm-scheduling-platform" \
  .venv/bin/python -m pytest -q \
  tests/extreme_load/test_execution.py \
  tests/extreme_load/test_offline.py \
  tests/extreme_load/test_runtime_metrics.py \
  tests/extreme_load/test_coordinator.py \
  tests/deploy/test_slow_media_fixture_server.py

for service in control_service orchestrator_service \
  vision_orchestrator_service online_gateway_service; do
  (cd "../$service" && ../algorithm-scheduling-platform/.venv/bin/python -m pytest -q)
done

MYPYPATH="/Users/zhangshen/Documents/workspace/算法功能调度" \
  .venv/bin/mypy --strict --explicit-package-bases \
  scripts/extreme_load/execution.py \
  scripts/extreme_load/offline.py \
  scripts/extreme_load/runtime_metrics.py \
  scripts/extreme_load/coordinator.py \
  packages/platform_common/repository.py \
  deploy/scripts/slow_media_fixture_server.py \
  ../orchestrator_service/app/infrastructure/ppt_runtime.py

.venv/bin/python -m compileall -q packages scripts/extreme_load deploy/scripts \
  ../control_service/app ../orchestrator_service/app \
  ../vision_orchestrator_service/app ../online_gateway_service/app
.venv/bin/python -m pytest -q tests/test_harness_consistency.py
(cd .. && openspec validate run-milestone-2b-extreme-load-campaign --strict)
```

实际结果：平台完整套件 `3258 passed, 3 skipped`，三个 skip 都是本机缺少 canonical FaceRec
GPU 容器；Campaign 聚焦 `126 passed`，PPT runtime `16 passed`，四服务分别
`25/70/44/49 passed`。Ruff、strict Mypy、`compileall`、Harness `5 passed`、OpenSpec strict
和 `git diff --check` 全部通过。该记录证明新修复具备提交和远端重建条件，不将本地结果表达
为 12.2/12.3 已完成。

## 2026-08-25 阶段 1 catalog 与逐级前置验证

```bash
.venv/bin/python -m pytest -q \
  tests/extreme_load/test_catalog.py \
  tests/extreme_load/test_coordinator.py \
  tests/extreme_load/test_execution.py
.venv/bin/ruff check \
  scripts/extreme_load/catalog.py \
  tests/extreme_load/test_catalog.py
.venv/bin/mypy --strict scripts/extreme_load/catalog.py
```

实际结果为 `88 passed`，Ruff 与 strict Mypy 均通过。新增断言证明四个 `OFF-LANE-*` 用例
各自只提交一个对应任务类型并依赖 `PHASE-0-COMPLETE`；3 节长课依赖四条单泳道，后续
`6/12/24/36` 逐级依赖上一档。旧阶段 0 与 `OFF-UNIQUE-*` 证据不能补足这些新 ID。

## 2026-08-25 `28e74d7` Fault Adapter attempt 根与跨主机锁修复验证

`28e74d7a0422d35d612571f515e4e45f9e555b65` 的 attempt
`full-campaign-28e74d7a0422-20260825202700` 只完成
`BASE-MEDIA-DOWNLOAD-1/3/10`，三案分别为 `1/1`、`3/3`、`10/10` 通过且护栏均为
`CLEAR`。`BASE-MEDIA-DOWNLOAD-30`、业务基线和阶段 1–6 未启动；该历史 attempt 保持只读。

从 `algorithm-scheduling-platform/` 执行修复后的权威本地门禁：

```bash
.venv/bin/python -m pytest -q \
  tests/extreme_load/test_production_fault_adapter.py

.venv/bin/python -m pytest -q \
  tests/extreme_load/test_faults.py \
  tests/extreme_load/test_extreme_load_fault_probe.py \
  tests/extreme_load/test_production_adapters.py \
  tests/extreme_load/test_production_fault_adapter.py

.venv/bin/python -m pytest -q tests/extreme_load

PYTHONPATH="$PWD:$PWD/.." \
  .venv/bin/python -m pytest -q -rs tests

.venv/bin/ruff check scripts/extreme_load tests/extreme_load
MYPYPATH="$PWD/.." .venv/bin/mypy --strict --explicit-package-bases \
  scripts/extreme_load/production_fault_adapter.py \
  tests/extreme_load/test_production_fault_adapter.py
.venv/bin/python -m compileall -q scripts/extreme_load deploy/scripts \
  tests/extreme_load
.venv/bin/python -m pytest -q tests/test_harness_consistency.py
(cd .. && openspec validate run-milestone-2b-extreme-load-campaign --strict)
git diff --check
```

实际结果依次为 `37 passed`、`121 passed`、`420 passed` 和
`3274 passed, 3 skipped, 27 warnings`（`671.25s`）。三个 skip 都是本机未运行 canonical
`facerec-gpu0`，没有使用本地模拟补足。Ruff、strict Mypy 两个变更文件、compileall、Harness
`5 passed`、OpenSpec strict 和 diff-check 全部通过。

一次直接执行 `.venv/bin/pytest -q` 的非权威诊断因缺少 Harness 规定的 `PYTHONPATH`，在 deploy
测试收集阶段产生 5 个 `No module named deploy`；按上述权威命令重跑全绿，因此该现象记录为
命令环境错误，不作为实现失败。修复结果证明本地 attempt root 解析、`0600` 单链接
`.campaign-fault.lock`、逐动作本地探针和远端 canonical challenge 合同成立；它只完成
OpenSpec 10.19。下一步仍须形成新完整 SHA、重建/inspect 11 个镜像并创建新 attempt 从阶段 0
重跑。

## 2026-08-25 PPT 终态并发幂等聚焦回归

`4dc40757` attempt 的 `OFF-UNIQUE-PPT-100` 在第 33 份运行时样本因
`orchestrator-service` 不健康锁存 `STOP`。该样本 SHA-256 为
`1bc4e81e0b63758b89c2bfad5c74bf1bd56068fe6b2044b413f8eec86ef128e2`。现场为 100 任务中
33 完成、15 运行、52 待处理，Orchestrator 就绪详情为 `ppt_reconcile: 节点状态不允许从 60
转换到 60`。该 attempt 不存在规范 `OFF-UNIQUE-PPT-100` case 结果，不补写。

本地先运行新增并发回归，修复前结果为 `1 failed, 1 passed`，失败精确为
`InvalidNodeTransition: 60 -> 60`。修复后执行：

```bash
algorithm-scheduling-platform/.venv/bin/pytest -q \
  algorithm-scheduling-platform/tests/test_ppt_slice_adapter.py \
  orchestrator_service/tests/test_ppt_runtime.py

algorithm-scheduling-platform/.venv/bin/ruff check \
  orchestrator_service/app/infrastructure/ppt_slice.py \
  algorithm-scheduling-platform/tests/test_ppt_slice_adapter.py

(cd algorithm-scheduling-platform && PYTHONPATH="$PWD:$PWD/.." \
  .venv/bin/python -m pytest -q \
  tests/integration/test_orchestrator_repository.py \
  -k ppt_callback_and_reconcile)
```

实际结果为聚焦 `39 passed`，真实 PostgreSQL 双线程竞态 `1 passed`，Ruff 通过。
回归证明并发完成和并发失败只在状态与持久化载荷一致时被视为幂等重复；
完成/失败/取消异状态冲突、以及同状态不同结果仍以可识别冲突失败关闭。这只是本地
聚焦和数据库竞态验证；新 SHA、
11 镜像重建、远端就绪/Smoke 和新 attempt 阶段 0 仍属必需后续门禁。

补强后在平台 `.venv` 使用权威 `PYTHONPATH="$PWD:$PWD/.."` 重跑完整 `tests`，
最终结果为 `3284 passed, 3 skipped, 27 warnings`，用时 `666.64s`。三个 skip 仍仅因
本机未运行 canonical `facerec-gpu0`，将由同 SHA 远端 7/7 Smoke 和 Campaign 门禁补足；
该全量回归不替代 OpenSpec 11.9 的远端重建与新 attempt。

## 2026-08-26 实时 ASR 最终消息门禁聚焦回归

`da1f5e37` attempt 的 `OFF-UNIQUE-ASR-300` 在生成 74 份 `CLEAR` 运行时样本后失去 runner；
`phase1-runner.log` 缺少当前 case 的规范结果、`END` 和退出码。平台随后自然排空到空队列且
租约归零；三次有效样本均为队列/Outbox/Kafka lag/租约全 0 且 Orchestrator ready，
后两次间隔超过 30 秒。PostgreSQL 中该批 300 个任务类型和节点也均为 `60`。
这些是自然排空证据，不是缺失的 case 终态；该 attempt 不补写、不续跑。

从 `algorithm-scheduling-platform/` 执行：

```bash
.venv/bin/python -m pytest -q tests/extreme_load/test_execution.py -k 'asr_success'
.venv/bin/ruff check \
  scripts/extreme_load/execution.py \
  tests/extreme_load/test_execution.py
.venv/bin/mypy --strict scripts/extreme_load/execution.py
bash -n deploy/scripts/start-extreme-load-campaign-sequence
.venv/bin/python -m pytest -q tests/extreme_load/test_coordinator.py \
  -k 'execute_sequence or sequence_launcher or wrapper_is_local'
.venv/bin/python -m pytest -q tests/extreme_load/test_mixed_soak_adapters.py \
  -k finished_message
PYTHONPATH="$PWD:$PWD/.." .venv/bin/python -m pytest -q tests/extreme_load
```

实际结果依次为 `2 passed`、`3 passed`、`1 passed` 和完整 Campaign `424 passed`；平台
权威全量为 `3288 passed, 3 skipped, 27 warnings`，用时 `664.19s`。三个 skip 仍仅因
本机未运行 canonical `facerec-gpu0`，27 条 warning 均为既有 Python fork
`DeprecationWarning`。Ruff、strict Mypy、compileall、Bash syntax、Harness `5 passed` 和
OpenSpec strict 通过。回归证明：成功会话只
收到中间消息但 `finished_message_count=0` 时失败；收到 `finished=true` 时通过并在脱敏结果中
记录 `finished_message_count`；阶段 4/6 mixed/soak 使用同一终态判断；顺序 CLI 和后台入口会
发布逐案 START/END、最终退出码、PID 和 `0600` 日志。这只达到静态和单元验证层级，必须形成
新 SHA、重建 11 镜像、创建新 attempt 并从阶段 0 重跑。`da1f5e37` 阶段 0 的
`BASE-ASR-WS` 没有 `finished_message_count`，不能通过新门禁，因此 OpenSpec 12.1
重新为待验证。
