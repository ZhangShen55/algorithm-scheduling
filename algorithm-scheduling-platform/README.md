# Algorithm Scheduling Platform

工作区采用单仓库管理。四个可部署服务位于工作区根目录，本目录只保留公共包、
数据库迁移、部署编排、平台契约测试和 Harness。

Current maturity: the four FastAPI service projects, typed configuration, control-plane
components, PPT shared-result components, Kafka-backed orchestrator and visual Worker
runtime wiring, and single-machine Compose are present. Final seven-operator deployment,
real business-lane and recovery evidence is still incomplete, so the repository must not yet
be treated as an end-to-end production runtime.

The current scheduling topology contains seven operator types and 21 instances: 18 GPU
instances (six operator types on each of three GPUs) and three CPU PPT Slice instances.
`text_analysis/` remains in the workspace as a non-platform project; platform build, deployment,
registration, routing, leasing and verification must exclude it. Historical task and audit data
that mention Text Analysis remain readable.

New offline tasks use only these DAGs:

- PPT: `PPT_SLICE -> PPT_OCR`
- ASR: `ASR_TRANSCRIPTION`

Historical `PPT_KEYWORDS` and `COURSE_OVERVIEW` nodes may still appear in queries for old tasks,
but new tasks do not create placeholders for them.

## 实时负载路由

算子实例选择使用公共 Redis 租约注册表，不再按 `instance_id` 排序后持续填充第一个未满
实例。每次申请租约时，在同一个 Lua 原子操作中清理过期租约、过滤可调度实例、计算负载、
选择实例并创建租约。实例有效负载为：

```text
effective_inflight = max(active_lease_count, reported_inflight)
```

调度器按 `effective_inflight / declared_capacity` 选择最低负载实例；同负载候选按 capability
共享轮询游标选择。实例的全部 capability、离线调用与 Online Gateway 调用继续共享同一声明
容量，不能重复计算容量池。该公共语义同时覆盖 ASR、OCR、PPT Slice、FaceRec、ScreenDet 和
VBas；既有其他算子现场结果只代表旧首次适配路由基线，必须在新 revision 上重新验证。

VBas 部署权威值为 `max_concurrent_requests=1024`、`MaxConcurrentBatches=1024`、
`MaxQueueSize=0` 和 `declared_capacity=1024`。Vision Orchestrator 使用
`max_batch_size=8`、服务级全局 `max_concurrency=16`；所有课程共享这 16 个 batch 槽位。
Kafka 消费按 partition 只提交连续完成的 offset，停止时未完成消息保留为可重放。上述调整
不改变 A 服务的课程提交/查询路径、字段、整数状态、响应结构或异步语义。

七算子、四平台、四中间件和三 GPU 的首次部署、升级、回滚、常驻启停及精确清理，统一以
[算法功能调度平台部署手册](deploy/算法功能调度平台部署手册.md) 为中文操作权威。本 README
只说明仓库边界和开发入口，不再维护第二套生产部署顺序。

## Services

- `../control_service`: course task API, persistent status, Outbox and operator control plane.
- `../orchestrator_service`: offline Outbox publisher, Kafka consumers and general DAG execution.
- `../vision_orchestrator_service`: adaptive T/S frame analysis and aggregation.
- `../online_gateway_service`: request-level online image routing and realtime ASR session proxy.

## Shared packages

- `platform_common`: configuration, logging, trace and runtime helpers.
- `platform_contracts`: stable API, event and status contracts.
- `operator_registry_client`: active registration and heartbeat client for operators.

## Single-machine platform stack

```bash
docker compose -f deploy/docker-compose.platform.yml config --quiet
deploy/scripts/apply-course-task-submission-migration
docker compose -f deploy/docker-compose.platform.yml up -d --build --wait --wait-timeout "${PLATFORM_WAIT_TIMEOUT_SECONDS:-180}"
```

The stack exposes control/orchestrator on `18100`/`18101`, and maps vision/online to
host ports `18102`/`18103`. All platform and infrastructure services share the
`algorithm-scheduling-platform` Compose project and the `algorithm-platform` network.
Host-run processes use PostgreSQL, Kafka, Redis
and MongoDB on localhost; containers use `postgres:5432`, `kafka:29092`, `redis:6379`
and `mongodb:27017`. Use the infrastructure Compose alone only for dependency tests;
do not start it and the platform Compose sequentially as separate projects.
